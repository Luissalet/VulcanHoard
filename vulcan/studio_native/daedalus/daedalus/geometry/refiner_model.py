"""Checkpoint-compatible compositions of Apache-2.0 Diffusers primitives.

Research implementation written here. No imports from the upstream DetailGen3D
transformer files carrying Tencent territorial terms. Weights: VAST-AI MIT.
Attention row permutations restore standard Q/K/V layouts used by Diffusers.
"""
import math
import torch
from torch import nn
from diffusers.models.attention import FeedForward
from diffusers.models.attention_processor import Attention
from diffusers.models.embeddings import Timesteps, TimestepEmbedding
from diffusers.models.normalization import FP32LayerNorm


def attention(dim, heads, context=None):
    return Attention(query_dim=dim, cross_attention_dim=context, heads=heads,
                     dim_head=dim // heads, bias=False, eps=1e-6,
                     cross_attention_norm='layer_norm' if context else None)


class GeometryBlock(nn.Module):
    def __init__(self, dim, heads, self_attn=True, context=None, modulated=False):
        super().__init__()
        if self_attn:
            self.norm1 = FP32LayerNorm(dim, eps=1e-5)
            self.attn1 = attention(dim, heads)
        if context:
            self.norm2 = FP32LayerNorm(dim, eps=1e-5)
            self.attn2 = attention(dim, heads, context)
            if modulated:
                # DiT image context is not normalized by the attention layer.
                self.attn2.norm_cross = None
        self.norm3 = FP32LayerNorm(dim, eps=1e-5)
        self.ff = FeedForward(dim, activation_fn='gelu', inner_dim=dim * 4)
        if modulated:
            self.adaln_modulation = nn.Sequential(nn.SiLU(), nn.Linear(dim, dim * 9))

    def forward(self, x, context=None, time=None):
        if hasattr(self, 'adaln_modulation'):
            values = self.adaln_modulation(time).chunk(9, dim=-1)
            for index, (norm, operation) in enumerate(((self.norm1, self.attn1),
                                                      (self.norm2, self.attn2),
                                                      (self.norm3, self.ff))):
                shift, scale, gate = values[index * 3:index * 3 + 3]
                y = norm(x) * (1 + scale) + shift
                y = operation(y, encoder_hidden_states=context) if index == 1 else operation(y)
                x = x + gate * y
        else:
            if hasattr(self, 'attn1'):
                x = x + self.attn1(self.norm1(x))
            if hasattr(self, 'attn2'):
                x = x + self.attn2(self.norm2(x), encoder_hidden_states=context)
            x = x + self.ff(self.norm3(x))
        return x


class RefineTransformer(nn.Module):
    def __init__(self):
        super().__init__()
        self.time_embed = Timesteps(768, flip_sin_to_cos=False, downscale_freq_shift=0)
        self.time_proj = TimestepEmbedding(768, 3072, act_fn='gelu', out_dim=768)
        self.proj_in = nn.Linear(64, 768)
        self.blocks = nn.ModuleList([GeometryBlock(768, 12, context=1024, modulated=True) for _ in range(24)])
        self.norm_out = nn.LayerNorm(768)
        self.proj_out = nn.Linear(768, 64)

    def forward(self, x, timestep, context):
        t = self.time_proj(self.time_embed(timestep).to(x.dtype))[:, None]
        x = self.proj_in(x)
        for block in self.blocks:
            x = block(x, context, t)
        return self.proj_out(self.norm_out(x))


class FourierFeatures(nn.Module):
    def __init__(self):
        super().__init__()
        self.register_buffer('frequencies', 2.0 ** torch.arange(8, dtype=torch.float32), persistent=False)

    def forward(self, points):
        angles = points[..., None] * self.frequencies
        angles = angles.flatten(-2)
        return torch.cat([points, angles.sin(), angles.cos()], dim=-1)


class SurfaceEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.proj_in = nn.Linear(54, 512)
        self.blocks = nn.ModuleList([GeometryBlock(512, 8, self_attn=False, context=512)] +
                                   [GeometryBlock(512, 8) for _ in range(8)])
        self.norm_out = nn.LayerNorm(512)

    def forward(self, queries, points):
        x, context = self.proj_in(queries), self.proj_in(points)
        x = self.blocks[0](x, context)
        for block in self.blocks[1:]:
            x = block(x)
        return self.norm_out(x)


class SurfaceDecoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.blocks = nn.ModuleList([GeometryBlock(1024, 8) for _ in range(16)] +
                                   [GeometryBlock(1024, 8, self_attn=False, context=1024)])
        self.proj_query = nn.Linear(51, 1024)
        self.norm_out = nn.LayerNorm(1024)
        self.proj_out = nn.Linear(1024, 1)

    def prepare(self, z):
        for block in self.blocks[:-1]:
            z = block(z)
        return z

    def query(self, features, context):
        x = self.blocks[-1](self.proj_query(features), context)
        return -self.proj_out(self.norm_out(x))


class GeometryVAE(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedder = FourierFeatures()
        self.encoder = SurfaceEncoder()
        self.decoder = SurfaceDecoder()
        self.quant = nn.Linear(512, 128)
        self.post_quant = nn.Linear(64, 1024)

    def moments(self, surface, query_indices):
        features = torch.cat([self.embedder(surface[..., :3]), surface[..., 3:]], dim=-1)
        return self.quant(self.encoder(features[:, query_indices], features))


def convert_attention_layout(model):
    """Interpret checkpoint QKV rows as heads-first, then store standard rows."""
    with torch.no_grad():
        for module in model.modules():
            if not isinstance(module, Attention):
                continue
            names = ['to_k', 'to_v'] if module.is_cross_attention else ['to_q', 'to_k', 'to_v']
            matrices = [getattr(module, name).weight for name in names]
            rows = torch.cat(matrices, dim=0)
            n = len(names)
            restored = rows.reshape(module.heads, n, -1, rows.shape[1]).permute(1, 0, 2, 3).reshape_as(rows)
            for name, weight in zip(names, restored.chunk(n, dim=0)):
                getattr(module, name).weight.copy_(weight)


def farthest_points(points, count):
    """Greedy FPS, deterministic start at zero; no compiled torch-cluster needed."""
    x = points.float()
    distances = torch.full((len(x),), float('inf'), device=x.device)
    selected = torch.empty(count, dtype=torch.long, device=x.device)
    current = torch.zeros((), dtype=torch.long, device=x.device)
    for index in range(count):
        selected[index] = current
        torch.minimum(distances, ((x - x[current]) ** 2).sum(-1), out=distances)
        current = distances.argmax()
    return selected
