import json
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class Config:
    def __init__(self,data_dir=None):
        self.data=Path(data_dir or os.environ.get('DAEDALUS_DATA_DIR') or ROOT/'data').resolve()
        self.data.mkdir(parents=True,exist_ok=True)
        self.images=self.data/'images'
        self.jobs=self.data/'jobs'
        self.images.mkdir(exist_ok=True)
        self.jobs.mkdir(exist_ok=True)
        settings_path=self.data/'settings.json'
        settings=json.loads(settings_path.read_text()) if settings_path.exists() else {}
        default=Path('E:/Development/AI Models/daedalus') if os.name=='nt' else Path.home()/'.local/share/daedalus/models'
        self.models=Path(os.environ.get('DAEDALUS_MODEL_DIR') or settings.get('model_dir') or default).resolve()
        self.python=os.environ.get('DAEDALUS_ENGINE_PYTHON')
        self.draft_python=os.environ.get('DAEDALUS_DRAFT_PYTHON') or settings.get('draft_python') or self.python
        self.multiview_python=os.environ.get('DAEDALUS_MULTIVIEW_PYTHON') or settings.get('multiview_python')
        if not self.multiview_python:
            runtime=ROOT.parents[2]/'.multiview-venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            if runtime.is_file():
                self.multiview_python=str(runtime)
        self.port=int(os.environ.get('DAEDALUS_PORT','8830'))

    @staticmethod
    def devices():
        try:
            result=subprocess.run(
                ['nvidia-smi','--query-gpu=index,uuid,name,memory.total,memory.used','--format=csv,noheader,nounits'],
                capture_output=True,text=True,check=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            devices=[]
            for line in result.stdout.splitlines():
                index,uuid,name,total,used=[value.strip() for value in line.split(',')]
                devices.append({'index':int(index),'uuid':uuid,'name':name,'total_mib':int(total),'used_mib':int(used)})
        except (OSError,ValueError,subprocess.SubprocessError):
            return []
        return devices

    @property
    def gpus(self):
        # Keep the strongest cards first, preserving the original four-card order on that host.
        return [row['index'] for row in sorted(self.devices(),key=lambda row:(-row['total_mib'],row['index']))[:4]]

    def model_status(self):
        model_root=self.models
        files={
            'generator':['generator/diffusion_models/trellis_2_bf16.safetensors','generator/clip_vision/dino_v3_vit_l.safetensors',
                         'generator/vae/trellis_2_shape_vae_bf16.safetensors','generator/vae/trellis_2_texture_vae_bf16.safetensors','generator/pipeline.json'],
            'refiner':['refiner/transformer/diffusion_pytorch_model.safetensors','refiner/vae/diffusion_pytorch_model.safetensors',
                       'refiner/image_encoder_1/model.safetensors','refiner/image_encoder_1/config.json',
                       'refiner/feature_extractor_1/preprocessor_config.json','refiner/scheduler/scheduler_config.json',
                       'refiner/noise_scheduler/scheduler_config.json'],
            'ultra':['ultra/ultrashape_v1.pt','ultra/weights-proof.json'],
            'draft':['triposr/model.ckpt','triposr/config.yaml','triposr/image-encoder-config.json','triposr/weights-proof.json'],
            'multiview':['generator/clip_vision/dino_v3_vit_l.safetensors','multiview/weights-manifest.json','multiview/UniForge3D/pipeline.json',
                'multiview/UniForge3D/uniforge3d/stage1/uniforge3d_stage1_structure_joint_model.pt',
                'multiview/UniForge3D/uniforge3d/vggt/uniforge3d_stage1_structure_vggt_backbone.pt',
                'multiview/UniForge3D/uniforge3d/vggt/uniforge3d_shape_texture_vggt_conditioner.pt',
                'multiview/UniForge3D/uniforge3d/shape512/uniforge3d_stage2_shape512_denoiser.pt',
                'multiview/UniForge3D/uniforge3d/shape512/uniforge3d_stage2_shape512_vggt_adapter.pt',
                'multiview/TRELLIS.2-4B/ckpts/ss_flow_img_dit_1_3B_64_bf16.safetensors',
                'multiview/TRELLIS.2-4B/ckpts/shape_dec_next_dc_f16c32_fp16.json',
                'multiview/TRELLIS.2-4B/ckpts/shape_dec_next_dc_f16c32_fp16.safetensors',
                'multiview/TRELLIS-image-large/ckpts/ss_dec_conv3d_16l8_fp16.json',
                'multiview/TRELLIS-image-large/ckpts/ss_dec_conv3d_16l8_fp16.safetensors']}
        return {name:{'ready':all((model_root/path).is_file() for path in paths),
                      'missing':[path for path in paths if not (model_root/path).is_file()]} for name,paths in files.items()}
