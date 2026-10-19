"""Image-guided native refinement using the independently published Ultra weights."""
from concurrent.futures import ThreadPoolExecutor
import copy,gc,itertools,json,time
from pathlib import Path
import numpy as np
from PIL import Image
from scipy import ndimage
from skimage import measure
import torch
from transformers import Dinov2Config,Dinov2Model
import trimesh
from .ultra_model import UltraDenoiser,UltraDecoder

START=0; EVENTS=[]
def memory():
    return [{'logical_gpu':i,'physical_gpu':[1,2,3,0][i],
             'allocated_mib':round(torch.cuda.memory_allocated(i)/2**20,1),
             'peak_allocated_mib':round(torch.cuda.max_memory_allocated(i)/2**20,1)} for i in range(4)]
def event(name,**data):
    row={'event':name,'elapsed_s':round(time.perf_counter()-START,3),**data}; EVENTS.append(row)
    print(json.dumps(row),flush=True)

def split(model):
    sizes=[sum(p.numel()*p.element_size() for p in block.parameters()) for block in model.blocks]
    choices=[]
    for cuts in itertools.combinations(range(1,21),3):
        edges=(0,*cuts,21); groups=[sum(sizes[edges[i]:edges[i+1]]) for i in range(4)]
        choices.append((max(groups),sum(x*x for x in groups),edges))
    edges=min(choices)[2]
    for name,child in model.named_children():
        if name!='blocks':child.to('cuda:0')
    for device in range(4):
        for block in model.blocks[edges[device]:edges[device+1]]:block.to(f'cuda:{device}')
    event('refiner_split',blocks=[edges[i+1]-edges[i] for i in range(4)],
          parameter_mib=[sum(p.numel()*p.element_size() for p in model.parameters() if p.device.index==i)/2**20 for i in range(4)])

def image_condition(state,path):
    with torch.device('meta'):
        config=Dinov2Config(hidden_size=1024,num_hidden_layers=24,num_attention_heads=16,image_size=518,patch_size=14)
        model=Dinov2Model(config)
    stripped={key.removeprefix('main_image_encoder.model.'):value for key,value in state.items()}
    model.load_state_dict(stripped,strict=True,assign=True)
    model.eval().to(device='cuda:3',dtype=torch.bfloat16)
    source=Image.open(path).convert('RGBA')
    alpha=np.asarray(source)[...,3]
    ys,xs=np.nonzero(alpha)
    if not len(xs):raise ValueError('La referencia no contiene una pieza visible.')
    crop=source.crop((xs.min(),ys.min(),xs.max()+1,ys.max()+1))
    extent=max(source.size); target=int(extent*.85)
    ratio=target/max(crop.size)
    crop=crop.resize((max(1,round(crop.width*ratio)),max(1,round(crop.height*ratio))),Image.Resampling.LANCZOS)
    rgba=Image.new('RGBA',(extent,extent)); rgba.alpha_composite(crop,((extent-crop.width)//2,(extent-crop.height)//2))
    background=Image.new('RGBA',rgba.size,'white'); background.alpha_composite(rgba)
    rgb=background.convert('RGB').resize((1024,1024),Image.Resampling.BICUBIC)
    pixels=torch.tensor(np.asarray(rgb).copy(),device='cuda:3').permute(2,0,1)[None].float()/255
    pixels=torch.nn.functional.interpolate(pixels,size=(1022,1022),mode='bilinear',align_corners=False,antialias=True)
    mean=torch.tensor([.485,.456,.406],device=pixels.device)[None,:,None,None]
    std=torch.tensor([.229,.224,.225],device=pixels.device)[None,:,None,None]
    pixels=((pixels-mean)/std).to(torch.bfloat16)
    condition=model(pixels).last_hidden_state
    mask=rgba.getchannel('A').resize((1024,1024),Image.Resampling.NEAREST).resize((1022,1022),Image.Resampling.NEAREST)
    mask_tensor=torch.tensor(np.asarray(mask).copy(),device='cuda:3')[None,None].float()
    valid=torch.nn.functional.max_pool2d(mask_tensor,14,14).flatten().gt(0)
    valid=torch.cat([torch.ones(1,device=valid.device,dtype=torch.bool),valid])
    condition=condition[:,valid].to('cuda:0')
    event('image_encoded',tokens=condition.shape[1])
    model.cpu();del model,pixels
    return torch.cat([torch.zeros_like(condition),condition])

def mesh_anchors(path,count,seed):
    mesh=trimesh.load(path,force='mesh',process=False)
    center=mesh.bounds.mean(0); scale=1.98/float(mesh.extents.max())
    # Surface positions provide voxel anchors; no input geometry is overwritten.
    points,_=trimesh.sample.sample_surface(mesh,409600,seed=seed)
    coords=np.floor(((points-center)*scale+1)*64).clip(0,127).astype(np.int64)
    coords=np.unique(coords,axis=0)
    rng=np.random.default_rng(seed);coords=coords[rng.permutation(len(coords))]
    coords=coords[np.arange(count)%len(coords)]
    event('surface_encoded',unique_voxels=len(np.unique(coords,axis=0)),tokens=count,center=center.tolist(),scale=scale)
    return torch.tensor(coords,device='cuda:0')[None],center,scale

def extract(decoder,latents,center,scale,folder,resolution=512,chunk=2048):
    begin=time.perf_counter();decoder.to('cuda:0')
    context=decoder.prepare(latents)
    queries=[]
    for gpu in range(4):
        module=copy.deepcopy(decoder.geo_decoder).to(f'cuda:{gpu}')
        owner=UltraDecoder.__new__(UltraDecoder); torch.nn.Module.__init__(owner); owner.geo_decoder=module
        queries.append((owner,tuple(t.to(f'cuda:{gpu}') for t in context)))
    def query_worker(index,points):
        owner,kv=queries[index]
        with torch.inference_mode():
            output=[]
            for offset in range(0,len(points),chunk):
                x=torch.as_tensor(points[offset:offset+chunk],device=f'cuda:{index}',dtype=torch.float32)[None]
                output.append(owner.query(x,kv)[0,:,0].float().cpu().numpy())
        return np.concatenate(output) if output else np.empty(0,dtype=np.float32)
    total=0
    with ThreadPoolExecutor(max_workers=4) as pool:
        def query(indices,cells):
            nonlocal total
            points=(indices.astype(np.float32)/cells*2-1)
            chunks=np.array_split(points,4)
            futures=[pool.submit(query_worker,i,part) for i,part in enumerate(chunks)]
            total+=len(points)
            return np.concatenate([f.result() for f in futures])
        cells=128
        indices=np.indices((cells+1,)*3).reshape(3,-1).T
        field=query(indices,cells).reshape((cells+1,)*3)
        event('sdf_level',resolution=cells,queries=len(indices),memory=memory())
        while cells<resolution:
            lo=np.minimum.reduce([field[i:i+cells,j:j+cells,k:k+cells] for i in range(2) for j in range(2) for k in range(2)])
            hi=np.maximum.reduce([field[i:i+cells,j:j+cells,k:k+cells] for i in range(2) for j in range(2) for k in range(2)])
            active=ndimage.binary_dilation((lo<=0)&(hi>=0),iterations=2)
            vertex=ndimage.maximum_filter(np.pad(active,(0,1)),size=2,origin=-1)
            finer=cells*2
            mask=ndimage.zoom(vertex.astype(np.uint8),2,order=0)[:finer+1,:finer+1,:finer+1]>0
            field=torch.nn.functional.interpolate(torch.from_numpy(field)[None,None],size=[finer+1]*3,mode='trilinear',align_corners=True)[0,0].numpy()
            indices=np.argwhere(mask);field[mask]=query(indices,finer);cells=finer
            event('sdf_level',resolution=cells,queries=len(indices),memory=memory())
    vertices,faces,_,_=measure.marching_cubes(field,0)
    vertices=(vertices/cells*2-1)/scale+center
    mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=True)
    mesh.fix_normals(multibody=True)
    mesh.export(folder/'refined.glb')
    np.savez_compressed(folder/'refined.npz',vertices=mesh.vertices,faces=mesh.faces)
    event('mesh_decoded',vertices=len(mesh.vertices),faces=len(mesh.faces),watertight=bool(mesh.is_watertight),
          queries=total,seconds=time.perf_counter()-begin,memory=memory())

def run(spec):
    global START,EVENTS
    START=time.perf_counter();EVENTS=[]
    torch.set_num_threads(8)
    assert torch.cuda.device_count()==4
    folder=Path(spec['output_dir']);folder.mkdir(exist_ok=True,parents=True)
    weights=torch.load(Path(spec['model_root'])/'ultra/ultrashape_v1.pt',map_location='cpu',mmap=True,weights_only=True)
    with torch.device('meta'):
        model=UltraDenoiser(); decoder=UltraDecoder()
    model.load_state_dict(weights['dit'],strict=True,assign=True)
    decoder_state={key:value for key,value in weights['vae'].items() if key.startswith(('post_kl.','transformer.','geo_decoder.'))}
    decoder.load_state_dict(decoder_state,strict=True,assign=True)
    model.eval().to(dtype=torch.bfloat16);decoder.eval().to(dtype=torch.bfloat16)
    seed=int(spec.get('seed',42));count=int(spec.get('tokens',8192));steps=int(spec.get('steps',50))
    with torch.inference_mode():
        condition=image_condition(weights['conditioner'],spec['image'])
        indices,center,scale=mesh_anchors(spec['mesh'],count,seed)
        split(model)
        generator=torch.Generator(device='cuda:0').manual_seed(seed)
        latents=torch.randn((1,count,64),device='cuda:0',dtype=torch.bfloat16,generator=generator)
        sigmas=torch.cat([torch.linspace(0,1,steps,device='cuda:0'),torch.ones(1,device='cuda:0')])
        event('scheduler',scheduler='ascending rectified Euler',start=float(sigmas[0]),end=float(sigmas[-1]),steps=steps)
        for i in range(steps):
            prediction=model(latents.repeat(2,1,1),sigmas[i].expand(2),condition,indices.repeat(2,1,1))
            negative,positive=prediction.chunk(2);velocity=negative+5*(positive-negative)
            latents=(latents.float()+(sigmas[i+1]-sigmas[i])*velocity.float()).to(torch.bfloat16)
            event('refine_step',step=i+1,total=steps,memory=memory())
        calls=dict(model.calls)
        model.cpu();del model,condition,weights
        gc.collect()
        for gpu in range(4):
            with torch.cuda.device(gpu):torch.cuda.empty_cache()
        torch.save({'latents':latents.cpu(),'center':torch.from_numpy(center),'scale':scale},folder/'ultra-latents.pt')
        extract(decoder,latents,center,scale,folder,resolution=int(spec.get('mesh_resolution',512)))
    result={'events':EVENTS,'seconds_total':time.perf_counter()-START,'memory':memory(),'block_calls':calls,
            'tokens':count,'steps':steps,'backend':'ultra','implementation':'native PyTorch/Diffusers composition',
            'weights_revision':'5aeb21a7185d39f042d02b2695802f125a6f5159'}
    (folder/'result.json').write_text(json.dumps(result,indent=2))
