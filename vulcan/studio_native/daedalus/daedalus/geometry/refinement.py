"""Real DetailGen3D MIT weights, permissive execution path, four GPUs."""
import argparse
from collections import Counter
import gc
import importlib.util
import json
import shutil
from pathlib import Path
import sys
import time

from types import SimpleNamespace
ROOT = None

import numpy as np
from PIL import Image
from scipy import ndimage
from skimage import measure
import torch
from torch import nn
import torch.nn.functional as F
from safetensors.torch import load_file
import trimesh
from transformers import Dinov2Model, BitImageProcessor
from diffusers import FlowMatchEulerDiscreteScheduler, DDPMScheduler
from .refiner_model import GeometryVAE, RefineTransformer, convert_attention_layout, farthest_points

from .refiner_scheduler import RectifiedFlowScheduler

START = time.perf_counter()
CALLS = Counter()
HANDLES = []
EVENTS = []
DTYPE = torch.float16

def memory():
    return [{'logical_gpu': i, 'physical_gpu': [1, 2, 3, 0][i],
             'allocated_mib': round(torch.cuda.memory_allocated(i) / 2**20, 1),
             'peak_allocated_mib': round(torch.cuda.max_memory_allocated(i) / 2**20, 1)} for i in range(4)]

def event(name, **kwargs):
    row = {'event': name, 'elapsed_s': round(time.perf_counter() - START, 3), **kwargs}
    EVENTS.append(row)
    print(json.dumps(row), flush=True)

def move(value, device):
    if isinstance(value, torch.Tensor):
        return value.to(device)
    if isinstance(value, (list, tuple)):
        return type(value)(move(item, device) for item in value)
    if isinstance(value, dict):
        return {key: move(item, device) for key, item in value.items()}
    return value

def route(module, device, name):
    module.to(device)
    def hook(owner, args, kwargs):
        CALLS[f'{name}@{device}'] += 1
        return move(args, device), move(kwargs, device)
    HANDLES.append(module.register_forward_pre_hook(hook, with_kwargs=True))

def split(model):
    for name, child in model.named_children():
        if name != 'blocks':
            child.to('cuda:0')
    for index, block in enumerate(model.blocks):
        route(block, torch.device(f'cuda:{index // 6}'), f'refiner.blocks.{index}')
    route(model.norm_out, torch.device('cuda:0'), 'refiner.norm_out')
    distribution = Counter()
    for parameter in model.parameters():
        distribution[str(parameter.device)] += parameter.numel() * parameter.element_size()
    event('refiner_split', blocks=[6, 6, 6, 6], parameter_mib={k:v / 2**20 for k,v in distribution.items()})

def load_model(cls, component):
    with torch.device('meta'):
        model = cls()
    state = load_file(str(ROOT / component / 'diffusion_pytorch_model.safetensors'))
    result = model.load_state_dict(state, strict=True, assign=True)
    if hasattr(model, 'embedder'):
        model.embedder.frequencies = 2.0 ** torch.arange(8, dtype=torch.float32)
    model = model.eval().to(dtype=DTYPE)
    convert_attention_layout(model)
    event('loaded_strict', component=component, missing=list(result.missing_keys), unexpected=list(result.unexpected_keys))
    return model

def surface_latent(vae, mesh, seed, tokens=2048):
    vertices = mesh.vertices
    center = mesh.bounds.mean(axis=0)
    scale = 1.9 / np.ptp(vertices, axis=0).max()
    points, faces = trimesh.sample.sample_surface(mesh, 1000000, seed=seed)
    chosen = np.random.default_rng(seed).choice(len(points), 20480, replace=False)
    features = np.concatenate([(points[chosen] - center) * scale, mesh.face_normals[faces[chosen]]], axis=-1)
    surface = torch.tensor(features, device='cuda:0', dtype=DTYPE)[None]
    # The published encoder uses a random 8192-point subset, then deterministic 2048 FPS.
    subset = np.random.default_rng(seed).choice(20480, tokens*4, replace=tokens*4>20480)
    subset_gpu = torch.tensor(subset, device='cuda:0')
    indices = subset_gpu[farthest_points(surface[0, subset_gpu, :3], tokens)]
    vae.embedder.to('cuda:0')
    vae.encoder.to('cuda:0')
    vae.quant.to('cuda:0')
    moments = vae.moments(surface, indices)
    mean, logvar = moments.chunk(2, dim=-1)
    rng = torch.Generator(device='cuda:0').manual_seed(seed)
    latent = mean + torch.exp(0.5 * logvar.clamp(-30, 20)) * torch.randn(mean.shape, device='cuda:0', dtype=DTYPE, generator=rng)
    event('surface_encoded', input_points=20480, fps_candidates=tokens*4, latent_shape=list(latent.shape), center=center.tolist(), scale=scale)
    return latent, center, scale

def sdf_mesh(vae, latent, center, scale, output, resolution):
    begin = time.perf_counter()
    device = torch.device('cuda:1')
    vae.decoder.to(device)
    vae.post_quant.to(device)
    vae.embedder.to(device)
    context = vae.decoder.prepare(vae.post_quant(latent.to(device)))
    query_count = 0
    def query(coords, cells):
        nonlocal query_count
        values = np.empty(len(coords), dtype=np.float32)
        for start in range(0, len(coords), 8192):
            xyz = torch.tensor(coords[start:start + 8192], device=device, dtype=DTYPE)
            xyz = xyz / cells * 2.01 - 1.005
            result = vae.decoder.query(vae.embedder(xyz[None]), context)
            values[start:start+len(xyz)] = result[0, :, 0].float().cpu().numpy()
        query_count += len(coords)
        return values
    cells = 128
    coords = np.stack(np.meshgrid(*[np.arange(cells + 1)] * 3, indexing='ij'), -1).reshape(-1, 3)
    field = query(coords, cells).reshape([cells + 1] * 3)
    event('sdf_level', variant=output.name, resolution=cells, queries=len(coords))
    while cells < resolution:
        # Evaluate a two-cell halo around every coarse sign-changing cell.
        lo = np.minimum.reduce([field[i:i+cells, j:j+cells, k:k+cells] for i in range(2) for j in range(2) for k in range(2)])
        hi = np.maximum.reduce([field[i:i+cells, j:j+cells, k:k+cells] for i in range(2) for j in range(2) for k in range(2)])
        active = ndimage.binary_dilation((lo <= 0) & (hi >= 0), iterations=2)
        vertex_mask = np.pad(active, (0, 1))
        vertex_mask = ndimage.maximum_filter(vertex_mask, size=2, origin=-1)
        finer = cells * 2
        mask = ndimage.zoom(vertex_mask.astype(np.uint8), 2, order=0)[:finer+1, :finer+1, :finer+1] > 0
        field = F.interpolate(torch.from_numpy(field)[None, None], size=[finer+1]*3,
                              mode='trilinear', align_corners=True)[0,0].numpy()
        indices = np.argwhere(mask)
        field[mask] = query(indices, finer)
        cells = finer
        event('sdf_level', variant=output.name, resolution=cells, queries=len(indices))
    vertices, faces, _, _ = measure.marching_cubes(field, 0, method='lewiner')
    vertices = ((vertices / cells * 2.01 - 1.005) / scale + center).astype(np.float32)
    mesh = trimesh.Trimesh(vertices, faces, process=True)
    mesh.export(output)
    np.savez_compressed(output.with_suffix('.npz'), vertices=mesh.vertices, faces=mesh.faces)
    del field, context
    gc.collect()
    event('mesh_decoded', variant=output.name, vertices=len(mesh.vertices), faces=len(mesh.faces),
          watertight=bool(mesh.is_watertight), total_queries=query_count, seconds=round(time.perf_counter()-begin, 3), memory=memory())
    return mesh

def run(spec):
    global ROOT, START
    ROOT = Path(spec['model_root']) / 'refiner'
    START = time.perf_counter()
    args = SimpleNamespace(mesh=spec['mesh'],image=spec['image'],name=spec['output_dir'],
            steps=int(spec.get('steps',50)),resolution=512,seed=int(spec.get('seed',42)),
            tokens=int(spec.get('tokens',2048)),resume=False,initial_latent=None,baseline=None)
    out = Path(spec['output_dir'])
    out.mkdir(parents=True,exist_ok=True)
    assert torch.cuda.device_count() == 4
    torch.set_num_threads(8)
    torch.set_float32_matmul_precision('highest')
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.manual_seed(args.seed)
    event('environment', torch=torch.__version__, devices=[torch.cuda.get_device_name(i) for i in range(4)], steps=args.steps)
    with torch.inference_mode():
        vae = load_model(GeometryVAE, 'vae')
        if args.resume and (out / 'latents.pt').exists():
            saved = torch.load(out / 'latents.pt', weights_only=True)
            initial, refined = saved['initial'], saved['refined']
            center, scale = saved['center'].numpy(), saved['scale']
        else:
            if args.initial_latent:
                saved = torch.load(args.initial_latent, weights_only=True)
                initial, center, scale = saved['initial'].to('cuda:0'), saved['center'].numpy(), saved['scale']
                event('initial_latent_reused', path=args.initial_latent)
            else:
                mesh = trimesh.load(args.mesh, force='mesh', process=False)
                initial, center, scale = surface_latent(vae, mesh, args.seed, args.tokens)
            processor = BitImageProcessor.from_pretrained(ROOT / 'feature_extractor_1', local_files_only=True)
            encoder = Dinov2Model.from_pretrained(ROOT / 'image_encoder_1', local_files_only=True,
                                                torch_dtype=DTYPE).eval().to('cuda:3')
            image = Image.open(args.image).convert('RGB')
            pixels = processor(image, return_tensors='pt').pixel_values.to(device='cuda:3', dtype=DTYPE)
            condition = encoder(pixels).last_hidden_state.to('cuda:0')
            condition = torch.cat([torch.zeros_like(condition), condition])
            encoder.cpu()
            del encoder, pixels
            model = load_model(RefineTransformer, 'transformer')
            model.to(device="cuda:0",dtype=DTYPE)
            split(model)
            scheduler = RectifiedFlowScheduler.from_pretrained(ROOT / 'scheduler', local_files_only=True)
            event('official_scheduler', scheduler='RectifiedFlowScheduler', direction='sample + (sigma - sigma_next) * velocity')
            noise = DDPMScheduler.from_pretrained(ROOT / 'noise_scheduler', local_files_only=True)
            torch.manual_seed(args.seed)
            refined = noise.add_noise(initial, torch.randn_like(initial), torch.tensor(0))
            scheduler.set_timesteps(args.steps, device='cuda:0')
            for gpu in range(4):
                torch.cuda.reset_peak_memory_stats(gpu)
            sample_start = time.perf_counter()
            for index, t in enumerate(scheduler.timesteps):
                pred = model(refined.repeat(2,1,1), t.expand(2), condition)
                negative, positive = pred.chunk(2)
                velocity = negative + 10 * (positive-negative)
                refined = scheduler.step(velocity, t, refined, return_dict=False)[0]
                event('refine_step', step=index+1, total=args.steps, memory=memory())
            torch.cuda.synchronize()
            event('refinement_sampled', seconds=round(time.perf_counter()-sample_start,3))
            torch.save({'initial':initial.cpu(), 'refined':refined.cpu(), 'center':torch.from_numpy(center), 'scale':float(scale)}, out / 'latents.pt')
            for handle in HANDLES:
                handle.remove()
            model.cpu()
            del model, condition
            gc.collect()
            for gpu in range(4):
                with torch.cuda.device(gpu):
                    torch.cuda.empty_cache()
        for label, latent in [('refined',refined)]:
            if label == 'vae-only' and args.baseline:
                shutil.copy2(args.baseline, out / f'{label}.glb')
                npz = Path(args.baseline).with_suffix('.npz')
                if npz.exists():
                    shutil.copy2(npz,out / f'{label}.npz')
                event('baseline_reused',path=args.baseline)
            if not (out / f'{label}.glb').exists():
                sdf_mesh(vae, latent, center, scale, out / f'{label}.glb', args.resolution)
        (out / 'result.json').write_text(json.dumps({'steps':args.steps,'resolution':args.resolution,'tokens':args.tokens,
                    'seconds_total':time.perf_counter()-START,'memory':memory(), 'block_calls':dict(CALLS),
                    'events':EVENTS, 'weights_revision':'ff2fac7768babe768bb73d03e998f80a397a3454',
                    'implementation':'independent composition of Apache Diffusers; no restricted upstream transformer imported'},indent=2))
        event('complete', output=str(out))
