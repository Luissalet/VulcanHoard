"""Multiview feature banks from the existing native local image encoder."""
import json
from pathlib import Path

import torch
from safetensors.torch import load_file
from .checkpoints import assign_checkpoint

from vulcan.studio_native.daedalus.daedalus.geometry import ops
from vulcan.studio_native.daedalus.daedalus.geometry.image_encoder import DINOv3ViTModel


class DinoV3Encoder(torch.nn.Module):
    def __init__(self, checkpoint: str | Path):
        super().__init__()
        config_path = Path(__file__).resolve().parents[2] / 'image-encoder-config.json'
        config = json.loads(config_path.read_text(encoding='utf-8'))
        self.model = DINOv3ViTModel(config, torch.float32, 'meta', ops.disable_weight_init)
        assign_checkpoint(self.model, load_file(str(checkpoint)))
        head_dim = config['hidden_size'] // config['num_attention_heads']
        self.model.rope_embeddings.inv_freq = 1 / config['rope_theta'] ** torch.arange(
            0, 1, 4 / head_dim, dtype=torch.float32, device='cpu',
        )
        self.register_buffer('_mean', torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1), persistent=False)
        self.register_buffer('_std', torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1), persistent=False)
        self.eval().requires_grad_(False)

    @torch.no_grad()
    def forward(self, images):
        if images.ndim != 5 or images.shape[2] != 3 or images.shape[-1] != images.shape[-2]:
            raise ValueError('Expected square RGB images [B,V,3,H,W]')
        batch, views, channels, height, width = images.shape
        banks = []
        for image in images.reshape(-1, channels, height, width):
            normalized = (image[None] - self._mean) / self._std
            banks.append(self.model(normalized, skip_norm_elementwise=True)[0])
        value = torch.cat(banks)
        return value.reshape(batch, views, value.shape[-2], value.shape[-1]).float()
