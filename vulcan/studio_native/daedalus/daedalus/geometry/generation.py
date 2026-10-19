"""Isolated TRELLIS.2 experiment, full BF16 weights and sequential model parallelism."""
import argparse
from collections import Counter
import gc
import hashlib
import json
from pathlib import Path
import sys
import time

from types import SimpleNamespace
ROOT = None
WEIGHTS = None

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F
from safetensors.torch import load_file
import trimesh
from scipy.spatial import cKDTree

from . import ops
from .image_encoder import DINOv3ViTModel
from .generator_model import Trellis2
from .surface_vae import ShapeVae, TextureVae, SparseTensor, VarLenTensor

DTYPE = torch.bfloat16
CALLS = Counter()
HANDLES = []
EVENTS = []
START = time.perf_counter()


def event(name, **data):
    entry = {'event': name, 'elapsed_s': round(time.perf_counter() - START, 3), **data}
    EVENTS.append(entry)
    print(json.dumps(entry), flush=True)


def memory():
    return [{'logical_gpu': i, 'physical_gpu': [1, 2, 3, 0][i],
             'allocated_mib': round(torch.cuda.memory_allocated(i) / 2**20, 1),
             'reserved_mib': round(torch.cuda.memory_reserved(i) / 2**20, 1),
             'peak_allocated_mib': round(torch.cuda.max_memory_allocated(i) / 2**20, 1)}
            for i in range(4)]


def move(value, device):
    if isinstance(value, SparseTensor):
        if value.device == device:
            return value
        return SparseTensor(feats=value.feats.to(device), coords=value.coords.to(device),
                            shape=value.shape, scale=value._scale)
    if isinstance(value, (torch.Tensor, VarLenTensor)):
        return value.to(device)
    if isinstance(value, tuple):
        return tuple(move(x, device) for x in value)
    if isinstance(value, list):
        return [move(x, device) for x in value]
    if isinstance(value, dict):
        return {k: move(v, device) for k, v in value.items()}
    return value


def place(module, device, name):
    module.to(device)

    def route(owner, args, kwargs):
        CALLS[f'{name}@{device}'] += 1
        return move(args, device), move(kwargs, device)

    HANDLES.append(module.register_forward_pre_hook(route, with_kwargs=True))


def split(model):
    # One DiT forward traverses 8/8/8/6 blocks across four GPUs.
    for stage_name in ['structure_model', 'img2shape_512', 'img2shape', 'shape2txt']:
        stage = getattr(model, stage_name)
        for child_name, child in stage.named_children():
            if child_name != 'blocks':
                child.to('cuda:0')
        for index, block in enumerate(stage.blocks):
            place(block, torch.device(f'cuda:{min(index // 8, 3)}'),
                  f'{stage_name}.blocks.{index}')
        place(stage.out_layer, torch.device('cuda:0'), f'{stage_name}.out_layer')
    distribution = Counter()
    for parameter in model.parameters():
        distribution[str(parameter.device)] += parameter.numel() * parameter.element_size()
    event('model_split', parameter_mib={k: round(v / 2**20, 2) for k, v in distribution.items()},
          blocks_per_stage=[8, 8, 8, 6], memory=memory())


def load_vae(cls, filename, device):
    model = cls()
    state = load_file(str(WEIGHTS / 'vae' / filename))
    result = model.load_state_dict(state, strict=True, assign=True)
    event('vae_loaded', filename=filename, missing=list(result.missing_keys), unexpected=list(result.unexpected_keys))
    return model.eval().to(device=device, dtype=DTYPE)


def encode_image(path):
    image = Image.open(path).convert('RGBA')
    rgba = np.asarray(image).astype(np.float32) / 255
    alpha = rgba[..., 3]
    yy, xx = np.where(alpha > 0.8)
    if len(xx):
        cx, cy = (xx.min() + xx.max()) / 2, (yy.min() + yy.max()) / 2
        side = max(xx.max() - xx.min(), yy.max() - yy.min())
        box = (int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))
        image = image.crop(box)
    rgba = np.asarray(image).astype(np.float32) / 255
    rgb = rgba[..., :3] * rgba[..., 3:4]
    Image.fromarray((rgb * 255).astype(np.uint8)).save(ROOT / 'preprocessed.png')
    config = json.loads((Path(__file__).parent / 'image-encoder-config.json').read_text())
    clip = DINOv3ViTModel(config, DTYPE, 'cpu', ops.disable_weight_init)
    clip.load_state_dict(load_file(str(WEIGHTS / 'clip_vision' / 'dino_v3_vit_l.safetensors')), strict=True, assign=True)
    clip.eval().to(device='cuda:0', dtype=DTYPE)
    tensor = torch.from_numpy(rgb).permute(2, 0, 1)[None].to('cuda:0')
    mean = torch.tensor([0.485, 0.456, 0.406], device='cuda:0').view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device='cuda:0').view(1, 3, 1, 1)
    conditions = {}
    for size in [512, 1024]:
        pixels = F.interpolate(tensor, size=(size, size), mode='bicubic', align_corners=False, antialias=True)
        conditions[size] = clip(((pixels - mean) / std).to(DTYPE), skip_norm_elementwise=True)[0]
    clip.cpu()
    del clip
    gc.collect()
    torch.cuda.empty_cache()
    event('image_encoded', token_shapes={str(k): list(v.shape) for k, v in conditions.items()})
    return conditions


def sample(stage, noise, cond, params, name, concat_cond=None):
    sample_start = time.perf_counter()
    x = noise.float()
    times = np.linspace(1, 0, params['steps'] + 1)
    shift = params['rescale_t']
    times = shift * times / (1 + (shift - 1) * times)
    for index, (t, previous) in enumerate(zip(times[:-1], times[1:])):
        timestep = torch.tensor([1000 * t], device='cuda:0', dtype=DTYPE)
        kwargs = {'concat_cond': concat_cond.to(dtype=DTYPE)} if concat_cond is not None else {}
        if isinstance(x, SparseTensor):
            pred_pos = stage(x.to(dtype=DTYPE), timestep, cond, **kwargs).feats.float()
            x_feats = x.feats
        else:
            pred_pos = stage(x.to(DTYPE), timestep, cond, **kwargs).float()
            x_feats = x
        strength = params['guidance_strength'] if params['guidance_interval'][0] <= t <= params['guidance_interval'][1] else 1
        pred = pred_pos
        if strength != 1:
            neg = torch.zeros_like(cond)
            if isinstance(x, SparseTensor):
                pred_neg = stage(x.to(dtype=DTYPE), timestep, neg, **kwargs).feats.float()
            else:
                pred_neg = stage(x.to(DTYPE), timestep, neg, **kwargs).float()
            pred = strength * pred_pos + (1 - strength) * pred_neg
            rescale = params['guidance_rescale']
            if rescale:
                a = 1 - 1e-5
                b = 1e-5 + a * t
                pos_x0 = a * x_feats - b * pred_pos
                cfg_x0 = a * x_feats - b * pred
                # SparseTensor's standard deviation is per logical batch over tokens and channels.
                dims = tuple(range(cfg_x0.ndim)) if isinstance(x, SparseTensor) else tuple(range(1, cfg_x0.ndim))
                rescaled = cfg_x0 * pos_x0.std(dim=dims, keepdim=True) / cfg_x0.std(dim=dims, keepdim=True)
                x0 = rescale * rescaled + (1 - rescale) * cfg_x0
                pred = (a * x_feats - x0) / b
        result = x_feats - float(t - previous) * pred
        x = x.replace(result) if isinstance(x, SparseTensor) else result
        for device in range(4):
            torch.cuda.synchronize(device)
        event('sampling_step', stage=name, step=index + 1, total=params['steps'], memory=memory())
    event('sampling_complete', stage=name, seconds=round(time.perf_counter() - sample_start, 3))
    return x


def denormalize(slat, normalization):
    mean = torch.tensor(normalization['mean'], device=slat.device)[None]
    std = torch.tensor(normalization['std'], device=slat.device)[None]
    return slat.replace(slat.feats * std + mean)


def run(spec):
    global ROOT, WEIGHTS, START
    ROOT = Path(spec['output_dir'])
    WEIGHTS = Path(spec['model_root']) / 'generator'
    START = time.perf_counter()
    args = SimpleNamespace(resolution=int(spec.get('resolution',512)),image=spec['image'],seed=int(spec.get('seed',42)),resume=False)
    out = ROOT
    out.mkdir(parents=True,exist_ok=True)
    assert torch.cuda.device_count()==4
    event('environment',torch=torch.__version__,devices=[torch.cuda.get_device_name(i) for i in range(4)],resolution=args.resolution)
    config = json.loads((WEIGHTS / 'pipeline.json').read_text())['args']
    torch.manual_seed(args.seed)
    with torch.inference_mode():
        conditions = encode_image(args.image)
        model = Trellis2(resolution=64, init_txt_model=True, dtype=DTYPE, device='cpu', operations=ops.disable_weight_init)
        state = load_file(str(WEIGHTS / 'diffusion_models' / 'trellis_2_bf16.safetensors'))
        result = model.load_state_dict({k.removeprefix('model.'): v for k, v in state.items()}, strict=True, assign=True)
        del state
        model.eval()
        event('diffusion_loaded', missing=list(result.missing_keys), unexpected=list(result.unexpected_keys))
        split(model)
        for i in range(4):
            with torch.cuda.device(i):
                torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats(i)
        shape_vae = load_vae(ShapeVae, 'trellis_2_shape_vae_bf16.safetensors', 'cuda:1')
        if args.resume and (out / 'latents.pt').exists():
            saved = torch.load(out / 'latents.pt', map_location='cpu', weights_only=True)
            shape = SparseTensor(saved['shape'].to('cuda:0'), saved['coords'].to('cuda:0'))
            texture = SparseTensor(saved['texture'].to('cuda:0'), saved['coords'].to('cuda:0'))
        else:
            if spec.get('source_latent'):
                source_path=Path(spec['source_latent'])
                saved=torch.load(source_path,map_location='cpu',weights_only=True)
                coords=saved['coords'].to('cuda:0')
                shape=SparseTensor(saved['shape'].to('cuda:0'),coords)
                with source_path.open('rb') as stream:
                    source_digest=hashlib.file_digest(stream,'sha256').hexdigest()
                event('source_latent_reused',path=str(source_path),sha256=source_digest,tokens=len(coords))
            else:
                noise = torch.randn((1, 8, 16, 16, 16), device='cuda:0')
                structure = sample(model.structure_model, noise, conditions[512], config['sparse_structure_sampler']['params'], 'structure')
                occupancy = shape_vae.decode_structure(structure.to(device='cuda:1', dtype=DTYPE)) > 0
                occupancy = F.max_pool3d(occupancy.float(), 2, 2) > 0.5
                coords = torch.argwhere(occupancy)[:, [0, 2, 3, 4]].int().to('cuda:0')
                event('structure_decoded', occupied_voxels=len(coords))
                shape_noise = SparseTensor(torch.randn((len(coords), 32), device='cuda:0'), coords)
                shape = sample(model.img2shape_512, shape_noise, conditions[512], config['shape_slat_sampler']['params'], 'shape_512')
            if args.resolution == 1024:
                physical_shape = denormalize(shape, config['shape_slat_normalization'])
                high_coords = shape_vae.upsample_shape(move(physical_shape, torch.device('cuda:1')).to(dtype=DTYPE), 4)
                quant = torch.cat([high_coords[:, :1], ((high_coords[:, 1:] + 0.5) / 512 * 64).int()], 1)
                coords = quant.unique(dim=0).to('cuda:0')
                event('cascade_upsampled', occupied_voxels=len(coords))
                shape_noise = SparseTensor(torch.randn((len(coords), 32), device='cuda:0'), coords)
                shape_params=dict(config['shape_slat_sampler']['params'])
                if spec.get('source_latent'):shape_params['steps']=int(spec.get('steps',shape_params['steps']))
                shape = sample(model.img2shape, shape_noise, conditions[1024], shape_params, 'shape_1024')
            tex_noise = SparseTensor(torch.randn((len(coords), 32), device='cuda:0'), coords)
            texture = sample(model.shape2txt, tex_noise, conditions[1024], config['tex_slat_sampler']['params'], 'texture', concat_cond=shape)
            torch.save({'shape': shape.feats.cpu(), 'texture': texture.feats.cpu(), 'coords': coords.cpu()}, out / 'latents.pt')
        physical_shape = denormalize(shape, config['shape_slat_normalization'])
        physical_tex = denormalize(texture, config['tex_slat_normalization'])
        # Generation has finished; release DiT weights before the high-resolution decoders.
        for handle in HANDLES:
            handle.remove()
        model.cpu()
        del model, conditions, shape, texture
        gc.collect()
        for i in range(4):
            with torch.cuda.device(i):
                torch.cuda.empty_cache()
        event('decode_start', memory=memory())
        meshes, subs = shape_vae.decode_shape_slat(move(physical_shape, torch.device('cuda:1')).to(dtype=DTYPE), args.resolution)
        vertices, faces = meshes[0]
        vertices = vertices.float().cpu().numpy()
        faces = faces.int().cpu().numpy()
        np.savez_compressed(out / 'geometry.npz', vertices=vertices, faces=faces)
        event('geometry_decoded', vertices=len(vertices), faces=len(faces), memory=memory())
        shape_vae.cpu()
        del shape_vae
        with torch.cuda.device(1):
            torch.cuda.empty_cache()
        tex_vae = load_vae(TextureVae, 'trellis_2_texture_vae_bf16.safetensors', 'cuda:2')
        subs = [move(sub, torch.device('cuda:2')) for sub in subs]
        voxel = tex_vae.decode_tex_slat(move(physical_tex, torch.device('cuda:2')).to(dtype=DTYPE), subs)
        voxel_coords = voxel.coords[:, 1:].cpu().numpy()
        attrs = voxel.feats.float().cpu().numpy()
        np.savez_compressed(out / 'pbr-voxels.npz', coords=voxel_coords, attributes=attrs, resolution=args.resolution)
        event('materials_decoded', voxels=len(voxel_coords), attribute_channels=attrs.shape[1], memory=memory())
        # Preview export preserves appearance as vertex colors; all six PBR channels remain in NPZ.
        tree = cKDTree((voxel_coords.astype(np.float32) + 0.5) / args.resolution - 0.5)
        _, indices = tree.query(vertices, workers=4)
        colors = np.clip(attrs[indices, :3] * 255, 0, 255).astype(np.uint8)
        vertices_yup = vertices[:, [1, 2, 0]]
        mesh = trimesh.Trimesh(vertices=vertices_yup, faces=faces, vertex_colors=colors, process=False)
        mesh.export(out / 'model.glb')
        mesh.export(out / 'model.ply')
        mesh.export(out / 'model.stl')
        reloaded = trimesh.load(out / 'model.glb', force='mesh', process=False)
        assert len(reloaded.vertices) > 0 and len(reloaded.faces) > 0
        assert np.isfinite(reloaded.vertices).all()
        summary = {'resolution': args.resolution, 'seed': args.seed, 'seconds_total': time.perf_counter() - START,
                   'upscale_source':{'latent_sha256':source_digest,'path':str(source_path)} if spec.get('source_latent') else None,
                   'vertices': len(mesh.vertices), 'faces': len(mesh.faces), 'watertight': bool(mesh.is_watertight),
                   'memory': memory(), 'block_calls': dict(CALLS), 'events': EVENTS,
                   'export': 'GLB/PLY vertex colors, STL geometry; six PBR voxel channels in NPZ',
                   'weights_revision': '430a9d09b2416687018c8fe8edced2ad4858a439',
                   'runtime_revision': '95539f56344958339e39b7582a476267d489b0ee'}
        (out / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        event('complete', result=str(out / 'result.json'), vertices=len(mesh.vertices), faces=len(mesh.faces))
