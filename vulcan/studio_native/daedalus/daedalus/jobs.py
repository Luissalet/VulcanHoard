from contextlib import ExitStack
import json
import os
from pathlib import Path
import queue
import sys
import threading

from vulcan.hoard_link.lease import lease,LeaseTimeout
from vulcan.hoard_link import proc
from .store import TERMINAL

class JobManager:
    def __init__(self,config,store,runner=None,*,resume_pending=False):
        self.config=config
        self.store=store
        self.queue=queue.Queue()
        self.events={}
        self.lock=threading.RLock()
        self.stopping=threading.Event()
        self.runner=runner or self.run_native
        self.store.recover()
        self.thread=threading.Thread(target=self.loop,name='daedalus-jobs',daemon=True)
        self.thread.start()
        if resume_pending:
            for job in reversed(self.store.list()):
                if job['status']=='queued':
                    self.enqueue(job['id'])

    def enqueue(self,identity):
        with self.lock:
            self.events[identity]=threading.Event()
        self.queue.put(identity)

    def cancel(self,identity):
        with self.lock:
            job=self.store.request_cancel(identity)
            event=self.events.get(identity)
            if event:
                event.set()
        return job

    def loop(self):
        while not self.stopping.is_set():
            try:
                identity=self.queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                job=self.store.job(identity)
                if not job or job['status']!='queued':
                    continue
                with self.lock:
                    cancel=self.events[identity]
                    if cancel.is_set():
                        continue
                    self.store.update(identity,status='waiting',stage='preparing_geometry' if job['kind'] in {'repair','optimize'} else 'waiting_resources')
                image=self.store.image(job['image_id'])
                folder=self.config.jobs/identity
                folder.mkdir(exist_ok=True)
                spec={**job['params'],'kind':job['kind'],'image':image['path'],'output_dir':str(folder),
                      'model_root':str(self.config.models),'physical_gpus':self.config.gpus,'job_id':identity}
                if job['kind']=='generate' and job['params'].get('generator')=='triposr':
                    spec['physical_gpus']=[job['params'].get('draft_gpu',0)]
                if job['kind'] in {'refine','repair','optimize'}:
                    parent=self.store.job(job['parent_id'])
                    if not parent or parent['status']!='complete':
                        raise ValueError('La versión de origen ya no está disponible.')
                    spec['mesh']=str(self.config.jobs/parent['id']/'model.glb')
                    spec['source_folder']=str(self.config.jobs/parent['id'])
                    base=parent
                    while base.get('parent_id') and base['kind'] in {'refine','repair','optimize'}:
                        base=self.store.job(base['parent_id'])
                    spec['material_source']=str(self.config.jobs/base['id'])
                    if job['kind']!='refine' and parent['result'].get('materials')=='none':
                        spec['material_source']=None
                        spec['material_mode']='none'
                    spec['image']=str(self.config.jobs/base['id']/'preprocessed.png')
                    if spec.get('backend')=='ultra' and (self.config.jobs/base['id']/'input.png').is_file():
                        spec['image']=str(self.config.jobs/base['id']/'input.png')
                elif job['kind']=='upscale':
                    parent=self.store.job(job['parent_id'])
                    if not parent or parent['status']!='complete':
                        raise ValueError('La generación de origen ya no está disponible.')
                    spec['source_latent']=str(self.config.jobs/parent['id']/'latents.pt')
                    prepared=self.config.jobs/parent['id']/'input.png'
                    if prepared.is_file():
                        spec['image']=str(prepared)
                def on_event(event):
                    with self.lock:
                        current=self.store.job(identity)
                        if current['status'] not in {'waiting','running'}:
                            return
                        self.store.update(identity,status='running',stage=event.get('stage','working'),
                                          progress=max(current['progress'],float(event.get('progress',current['progress']))))
                result=self.runner(spec,on_event,cancel)
                with self.lock:
                    if cancel.is_set():
                        self.store.update(identity,status='interrupted' if self.stopping.is_set() else 'cancelled',stage='cancelled')
                    else:
                        self.store.finish(identity,result)
            except proc.Cancelled:
                self.store.update(identity,status='interrupted' if self.stopping.is_set() else 'cancelled',stage='cancelled')
            except Exception as error:
                with self.lock:
                    job=self.store.job(identity)
                    status='cancelled' if job and job['status']=='cancelling' else 'error'
                    self.store.update(identity,status=status,stage=status,error=str(error))
            finally:
                with self.lock:
                    self.events.pop(identity,None)
                self.queue.task_done()

    def run_native(self,spec,on_event,cancel):
        request=Path(spec['output_dir'])/'request.json'
        request.write_text(json.dumps(spec),encoding='utf-8')
        if spec['kind'] in {'repair','optimize'}:
            on_event({'stage':'checking_mesh','progress':.01})
            return self.run_worker(spec,on_event,cancel,request,cpu=True)
        # A short acquisition attempt is retried without holding a partial set.
        # Waiting is cancellable and does not remove models already in use.
        while not cancel.is_set():
            try:
                with ExitStack() as stack:
                    draft=spec['kind']=='generate' and spec.get('generator')=='triposr'
                    for gpu in spec.get('physical_gpus',self.config.gpus):
                        reservation=stack.enter_context(lease(vram_mb=7000 if draft or gpu==0 else 13000,
                            purpose=f'Daedalus {spec["kind"]} {spec["job_id"]}',owner='daedalus',
                            gpu=gpu,timeout_s=3,autostart=False))
                        if reservation.via!='hub' or reservation.state!='granted':
                            raise RuntimeError('Hoard Hub no responde para coordinar las gráficas. Abre el Hub y vuelve a intentarlo.')
                    if cancel.is_set():
                        raise proc.Cancelled()
                    on_event({'stage':'loading_models','progress':.01})
                    return self.run_worker(spec,on_event,cancel,request)
            except LeaseTimeout:
                if cancel.wait(1):
                    break
        raise proc.Cancelled()

    def run_worker(self,spec,on_event,cancel,request,cpu=False):
        draft=spec['kind']=='generate' and spec.get('generator')=='triposr'
        env=proc.build_env(CUDA_VISIBLE_DEVICES='' if cpu else ','.join(map(str,spec.get('physical_gpus',self.config.gpus))),CUDA_DEVICE_ORDER='PCI_BUS_ID')
        if draft:
            env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
        log_path=Path(spec['output_dir'])/'engine.log'
        with log_path.open('w',encoding='utf-8') as log:
            def line(text):
                log.write(text+'\n');log.flush()
                try:
                    event=json.loads(text)
                except (ValueError,TypeError):
                    return
                if event.get('event')=='progress':
                    on_event(event)
            python=(self.config.draft_python if draft else self.config.python) or sys.executable
            result=proc.run_streaming([python,'-u','-m','vulcan.studio_native.daedalus.daedalus.worker',str(request)],
                on_line=line,cancel=cancel,timeout=None,cwd=Path(__file__).resolve().parents[4],env=env)
        if result:
            detail=Path(spec['output_dir'])/'failure.json'
            if detail.exists():
                raise RuntimeError(json.loads(detail.read_text())['message'])
            raise RuntimeError('El motor no ha podido terminar. El registro del trabajo conserva el detalle.')
        return json.loads((Path(spec['output_dir'])/'artifacts.json').read_text())

    def close(self):
        self.stopping.set()
        with self.lock:
            for event in self.events.values():
                event.set()
        # Keep the instance lock until the owned worker and its leases have exited.
        self.thread.join()
