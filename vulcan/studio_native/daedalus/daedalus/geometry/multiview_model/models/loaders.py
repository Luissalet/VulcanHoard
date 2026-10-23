"""Strict local checkpoint loading without downloads or eager model allocation."""
import json
import re
from pathlib import Path

import torch
from safetensors.torch import load_file

from .joint_dit_vggt_stage1 import JointDiTVGGTStage1Model
from .lora import attach_stage1_lora
from .structured_latent_flow import VGGTConditionedSLatFlowModel
from .vaes.flexi_dual_grid import FlexiDualGridVaeDecoder
from .vaes.sparse_structure import SparseStructureDecoder
from .vggt_adapter import VGGT4LayerDINOAdapter


def read_weights(checkpoint):
    checkpoint = str(checkpoint)
    if checkpoint.endswith('.safetensors'):
        return load_file(checkpoint, device='cpu')
    state = torch.load(checkpoint, map_location='cpu', mmap=True, weights_only=True)
    return state['model'] if 'model' in state else state


def assert_materialized(model):
    missing = [name for name, value in list(model.named_parameters()) + list(model.named_buffers()) if value.is_meta]
    if missing:
        raise RuntimeError(f'Checkpoint left uninitialized tensors: {missing[:20]}')
    return model.eval().requires_grad_(False)


def load_trainable_weights(model, state):
    required = {name for name, parameter in model.named_parameters() if parameter.requires_grad}
    missing = required - state.keys()
    if missing:
        raise RuntimeError(f'Missing training weights: {sorted(missing)[:20]}')
    result = model.load_state_dict(state, strict=False, assign=True)
    if result.unexpected_keys:
        raise RuntimeError(f'Unexpected training weights: {result.unexpected_keys[:20]}')


def load_structure(spec):
    with torch.device('meta'):
        model = JointDiTVGGTStage1Model(**spec['model']['args'])
    weights = spec['weights']
    dit = read_weights(weights['dit_base'])
    dit.setdefault('rope_phases', model.dit.rope_phases)
    model.dit.load_state_dict(dit, strict=True, assign=True)
    vggt = {name.removeprefix('model.'): value for name, value in read_weights(weights['vggt_base']).items()}
    vggt = {name: value for name, value in vggt.items() if not name.startswith('camera_head.') and name != 'aggregator.patch_embed.mask_token'}
    model.vggt.load_state_dict(vggt, strict=True, assign=True)
    attach_stage1_lora(model.dit, **weights['dit_lora'])
    attach_stage1_lora(model.vggt, **weights['vggt_lora'])
    state = {
        re.sub(r'^fusion_groups\.(\d+)\.f_from_z\.', r'dit_to_vggt.\1.', name): value
        for name, value in read_weights(weights['checkpoint']).items()
        if not re.match(r'^fusion_groups\.\d+\.z_from_f\.', name)
    }
    load_trainable_weights(model, state)
    return assert_materialized(model)


def load_flow(spec):
    from peft import LoraConfig, get_peft_model

    with torch.device('meta'):
        model = VGGTConditionedSLatFlowModel(**spec['model']['args'])
        model.denoiser.requires_grad_(False)
        model.denoiser = get_peft_model(model.denoiser, LoraConfig(**spec['weights']['lora']))
    model.denoiser.load_state_dict(read_weights(spec['weights']['checkpoint']), strict=True, assign=True)
    model.adapter.load_state_dict(read_weights(spec['weights']['adapter_checkpoint']), strict=True, assign=True)
    return assert_materialized(model)


def load_adapter(spec):
    with torch.device('meta'):
        model = VGGT4LayerDINOAdapter(**spec['model']['args']['adapter_args'])
    model.load_state_dict(read_weights(spec['weights']['adapter_checkpoint']), strict=True, assign=True)
    return assert_materialized(model)


def load_decoder(checkpoint, *, resolution=None):
    checkpoint = str(checkpoint)
    config = json.loads(Path(checkpoint + '.json').read_text(encoding='utf-8'))
    classes = {'FlexiDualGridVaeDecoder': FlexiDualGridVaeDecoder, 'SparseStructureDecoder': SparseStructureDecoder}
    with torch.device('meta'):
        model = classes[config['name']](**config['args'])
    model.load_state_dict(read_weights(checkpoint + '.safetensors'), strict=True, assign=True)
    if resolution is not None:
        model.set_resolution(resolution)
    return assert_materialized(model)
