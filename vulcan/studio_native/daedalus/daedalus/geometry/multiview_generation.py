"""Joint local multiview geometry with one resident model stage at a time."""
from contextlib import contextmanager
import gc
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image
import torch
import trimesh

from .multiview_model.models.conditioning import StructuredLatentConditioner
from .multiview_model.models.dino import DinoV3Encoder
from .multiview_model.models.loaders import assert_materialized, load_adapter, load_decoder, load_flow, load_structure
from .multiview_model.pipelines.shape import decode_shape_latent, sample_shape_latent
from .multiview_model.pipelines.sparse_structure import decoded_coordinates, decode_sparse_structure, sample_sparse_structure
from .multiview_model.utils.preprocessing import build_input_batch


def local_config(model_root):
    root = Path(model_root) / 'multiview'
    config = json.loads((root / 'UniForge3D' / 'pipeline.json').read_text(encoding='utf-8'))

    def resolve(value, decoder=False):
        if isinstance(value, dict):
            relative = value['repo_id'].split('/')[-1] + '/' + value['filename']
        else:
            relative = 'UniForge3D/' + value
        path = root / relative
        files = [Path(str(path) + ext) for ext in ('.json', '.safetensors')] if decoder else [path]
        for file in files:
            if not file.is_file():
                raise FileNotFoundError(f'Missing local multiview weights: {file}')
        return str(path)

    for stage in ('sparse_structure', 'structured_latent_512'):
        spec = config[stage]
        for key in ('checkpoint', 'adapter_checkpoint', 'dit_base', 'vggt_base'):
            if key in spec['weights']:
                spec['weights'][key] = resolve(spec['weights'][key])
        spec['decoder']['checkpoint'] = resolve(spec['decoder']['checkpoint'], decoder=True)
    extractor = config['conditioner']['vggt_feature_extractor']
    extractor['checkpoint_path'] = resolve(extractor['checkpoint_path'])
    config['dino_checkpoint'] = str(Path(model_root) / 'generator/clip_vision/dino_v3_vit_l.safetensors')
    if not Path(config['dino_checkpoint']).is_file():
        raise FileNotFoundError(config['dino_checkpoint'])
    return config


def cropped_image(image):
    """An opaque image is explicitly treated as already cropped foreground."""
    return image.convert('RGBA')


def finalize_mesh(vertices, faces):
    # Model coordinates use Z up; the studio's GLB viewer uses Y up.
    mesh = trimesh.Trimesh(vertices=vertices[:, [1, 2, 0]], faces=faces, process=False)
    mesh.update_faces(mesh.unique_faces() & mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    if not len(mesh.faces):
        raise RuntimeError('Multiview surface cleanup returned an empty mesh')
    mesh = drop_fragments(mesh)
    mesh.fix_normals(multibody=True)
    return mesh


FRAGMENT_FRACTION = 0.02


def drop_fragments(mesh, fraction=FRAGMENT_FRACTION):
    """Remove connected pieces smaller than ``fraction`` of the largest piece's faces.

    The sparse decoder leaves thin sheets lying on the domain boundary next to the object (a measured cup job: the
    object was 96% of the faces, 764 loose pieces the rest). Pieces that large are kept, so a handle or a detached
    part of the object survives; nothing is filled or moved."""
    pieces = mesh.split(only_watertight=False)
    if len(pieces) < 2:
        return mesh
    largest = max(len(piece.faces) for piece in pieces)
    keep = [piece for piece in pieces if len(piece.faces) >= largest * fraction]
    if len(keep) == len(pieces):
        return mesh
    kept = trimesh.util.concatenate(keep)
    kept.metadata['fragments_removed'] = len(pieces) - len(keep)
    return kept


def release_model(model):
    if isinstance(model, torch.nn.Module):
        model.to_empty(device='meta')
    else:
        frozen = model.frozen_vggt
        for name in ('aggregator', 'shape_encoder', 'backbone_shape_adapters', 'point_head', 'dpt_shape_adapter_32'):
            getattr(frozen, name).to_empty(device='meta')
    gc.collect()
    torch.cuda.empty_cache()


@contextmanager
def resident(factory, device):
    model = factory()
    try:
        if isinstance(model, torch.nn.Module):
            assert_materialized(model)
        model.to(device)
        yield model
    finally:
        release_model(model)


@torch.inference_mode()
def run(spec, progress):
    started = time.perf_counter()
    folder = Path(spec['output_dir'])
    folder.mkdir(parents=True, exist_ok=True)
    images = [Path(path) for path in spec['images']]
    if len(images) < 2:
        raise ValueError('Multiview geometry needs at least two views of the same object')
    config = local_config(spec['model_root'])
    device = torch.device('cuda:0')
    # An isolated worker starts cold; memory counters require the CUDA allocator.
    torch.cuda.init()
    torch.manual_seed(int(spec.get('seed', 42)))
    torch.cuda.reset_peak_memory_stats(device)
    references = []
    for index, path in enumerate(images):
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with Image.open(path) as image:
            image.convert('RGBA').save(folder / f'view-{index + 1:02d}.png')
        references.append({'index': index, 'name': path.name, 'sha256': digest, 'file': f'view-{index + 1:02d}.png'})
    batch = build_input_batch(images, config['image_preprocess'], cropped_image)
    Image.fromarray((batch.dino_images512[0, 0].permute(1, 2, 0).numpy() * 255).astype(np.uint8)).save(folder / 'preprocessed.png')
    timings = {}

    def stage(name, value):
        timings[name] = round(time.perf_counter() - started, 3)
        progress(name, value)

    stage('encoding_views', .03)
    with resident(lambda: DinoV3Encoder(config['dino_checkpoint']), device) as encoder:
        with torch.autocast('cuda', dtype=torch.bfloat16):
            dino = encoder(batch.dino_images512.to(device)).cpu()
    del encoder
    stage('building_structure', .12)
    structure = config['sparse_structure']
    with resident(lambda: load_structure(structure), device) as model:
        dit = model.dit
        noise = torch.randn((1, dit.in_channels, dit.resolution, dit.resolution, dit.resolution), device='cpu').to(device)
        with torch.autocast('cuda', dtype=torch.bfloat16):
            latent = sample_sparse_structure(model, noise, batch.vggt_images.to(device), dino.to(device), **structure['sampler']['args'], **structure['sampler']['params']).cpu()
    del model, dit, noise
    stage('decoding_structure', .34)
    with resident(lambda: load_decoder(structure['decoder']['checkpoint']), device) as decoder:
        logits = decode_sparse_structure(decoder, latent.to(device), structure['decoder'].get('normalization'))
        coords32, coords64 = (coords.cpu() for coords in decoded_coordinates(logits))
        np.savez_compressed(folder / 'structure.npz', latent=latent.float().numpy(),
                            logits=logits.float().cpu().numpy(),
                            coords32=coords32.numpy(), coords64=coords64.numpy())
    del decoder, latent, logits
    stage('matching_views', .4)
    shape = config['structured_latent_512']

    def conditioner_factory():
        with torch.device('meta'):
            return StructuredLatentConditioner(**config['conditioner'])

    with resident(lambda: load_adapter(shape), device) as adapter:
        with resident(conditioner_factory, device) as conditioner:
            condition = conditioner.encode(
                adapter, dino_features=dino.to(device), vggt_images=batch.vggt_images.to(device),
                structure_conditioned_coords=coords64.to(device), input_masks=batch.input_masks.to(device),
                latent_coords=coords32.to(device), router_grid=32,
            )
        del conditioner
    del adapter
    stage('generating_shape', .55)
    selected_view = condition['cond']['selected_view'].cpu()
    with resident(lambda: load_flow(shape), device) as model:
        shape_latent = sample_shape_latent(model, coords32.to(device), condition, shape['sampler']).cpu()
    np.savez_compressed(folder / 'shape-latent.npz',
                        coords=shape_latent.coords.numpy(), feats=shape_latent.feats.float().numpy())
    del model, condition, dino, batch
    stage('decoding_mesh', .82)
    with resident(lambda: load_decoder(shape['decoder']['checkpoint'], resolution=512), device) as decoder:
        mesh = decode_shape_latent(decoder, shape_latent.to(device), shape['decoder'].get('normalization'))[0]
        vertices = mesh.vertices.cpu().numpy()
        faces = mesh.faces.cpu().numpy()
    del decoder, shape_latent, mesh
    if not len(faces) or not np.isfinite(vertices).all():
        raise RuntimeError('Multiview decoder returned an empty or non-finite mesh')
    output_mesh = finalize_mesh(vertices, faces)
    for extension in ('glb', 'stl', 'ply'):
        output_mesh.export(folder / f'model.{extension}')
    np.savez_compressed(folder / 'geometry.npz', vertices=vertices, faces=faces)
    np.savez_compressed(folder / 'view-assignment.npz', selected_view=selected_view.numpy(), coords32=coords32.numpy(), coords64=coords64.numpy())
    provenance = {
        'code_revision': '8b6094780e202b8a6b1d239e0997552e18547026',
        'weights_revision': 'a084333f2aae0213e85b9a4541752d1221d618db',
        'weights_license': 'CC-BY-NC-4.0', 'joint_multiview': True,
        'precision': 'declared FP32/FP16 weights; BF16 autocast for encoding and sampling',
        'resolution': 512, 'textures': False,
        'surface_processing': 'remove duplicate/degenerate faces and orient connected triangles; no hole filling',
        'input': 'foreground alpha or already cropped images', 'views': references,
    }
    (folder / 'multiview.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    result = {
        'vertices': len(output_mesh.vertices), 'faces': len(output_mesh.faces), 'watertight': bool(output_mesh.is_watertight),
        'winding_consistent': bool(output_mesh.is_winding_consistent),
        'seconds_total': time.perf_counter() - started, 'generator': 'multiview',
        'model_provenance': provenance, 'stage_times': timings,
        'memory': [{'logical_gpu': 0, 'peak_allocated_mib': torch.cuda.max_memory_allocated(device) / 2**20}],
    }
    (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    stage('preparing_export', .87)
    return result
