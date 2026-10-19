"""Native tensor composition for separately published Apache-2.0 UltraShape weights.

Uses PyTorch and Apache Diffusers primitives. No upstream pipeline, transformer,
CUDA extension or geographically restricted inference module is imported.
Checkpoint head packing is part of the tensor contract, not standard QKV order.
"""
import math
import torch
from torch import nn
import torch.nn.functional as F
from diffusers.models.attention import FeedForward

def rotary_grid(indices,dim=128):
    widths=[dim//3,dim//3,dim-2*(dim//3)]
    counts=[(widths[0]+1)//2,(widths[1]+1)//2]
    counts.append(dim//2-sum(counts))
    frequencies=[10000.0**(-torch.arange(0,width,2,device=indices.device).float()/width) for width in widths]
    angles=torch.cat([indices[...,axis,None].float()*frequencies[axis][:count] for axis,count in enumerate(counts)],-1)
    angles=torch.cat([angles,angles],-1)
    return angles.cos(),angles.sin()

def rotate(x,pair):
    cos,sin=pair
    a,b=x.chunk(2,-1)
    return (x*cos[:,None]+torch.cat([-b,a],-1)*sin[:,None]).to(x.dtype)

class PackedAttention(nn.Module):
    def __init__(self,dim,heads,context=None,norm='rms',fused=False):
        super().__init__()
        self.heads=heads; self.dim=dim; self.context=context; self.fused=fused
        if fused:
            self.c_qkv=nn.Linear(dim,3*dim,bias=False)
            self.c_proj=nn.Linear(dim,dim)
            self.attention=nn.Module()
            target=self.attention
        else:
            self.to_q=nn.Linear(dim,dim,bias=False)
            self.to_k=nn.Linear(context or dim,dim,bias=False)
            self.to_v=nn.Linear(context or dim,dim,bias=False)
            self.out_proj=nn.Linear(dim,dim)
            target=self
        norm_type=nn.RMSNorm if norm=='rms' else nn.LayerNorm
        target.q_norm=norm_type(dim//heads,eps=1e-6)
        target.k_norm=norm_type(dim//heads,eps=1e-6)

    def forward(self,x,context=None,rope=None):
        b,n,_=x.shape; h=self.heads; d=self.dim//h
        if self.fused:
            q,k,v=self.c_qkv(x).reshape(b,n,h,3*d).chunk(3,-1)
            norms=self.attention
        elif context is None:
            q,k,v=torch.cat([self.to_q(x),self.to_k(x),self.to_v(x)],-1).reshape(b,n,h,3*d).chunk(3,-1)
            norms=self
        else:
            q=self.to_q(x).reshape(b,n,h,d)
            k,v=torch.cat([self.to_k(context),self.to_v(context)],-1).reshape(b,context.shape[1],h,2*d).chunk(2,-1)
            norms=self
        q=norms.q_norm(q).transpose(1,2); k=norms.k_norm(k).transpose(1,2); v=v.transpose(1,2)
        if rope is not None:
            q=rotate(q,rope); k=rotate(k,rope)
        y=F.scaled_dot_product_attention(q,k,v).transpose(1,2).reshape(b,n,self.dim)
        return self.c_proj(y) if self.fused else self.out_proj(y)

class DenseMLP(nn.Module):
    def __init__(self,dim):
        super().__init__(); self.c_fc=nn.Linear(dim,4*dim); self.c_proj=nn.Linear(4*dim,dim)
    def forward(self,x):
        return self.c_proj(F.gelu(self.c_fc(x)))

class ExpertLayer(nn.Module):
    def __init__(self,dim):
        super().__init__()
        self.experts=nn.ModuleList([FeedForward(dim,inner_dim=dim*4,activation_fn='gelu') for _ in range(8)])
        self.gate=nn.Module(); self.gate.weight=nn.Parameter(torch.empty(8,dim))
        self.shared_experts=FeedForward(dim,inner_dim=dim*4,activation_fn='gelu')
    def forward(self,x):
        shape=x.shape; flat=x.reshape(-1,shape[-1])
        probabilities=F.linear(flat,self.gate.weight).softmax(-1)
        weights,ids=probabilities.topk(2,dim=-1,sorted=False)
        out=torch.zeros_like(flat)
        for i,expert in enumerate(self.experts):
            rows,slots=torch.where(ids==i)
            if rows.numel():
                out.index_add_(0,rows,expert(flat[rows])*weights[rows,slots,None])
        return out.reshape(shape)+self.shared_experts(x)

class DenoiseBlock(nn.Module):
    def __init__(self,index,dim=2048):
        super().__init__()
        self.norm1=nn.LayerNorm(dim,eps=1e-6); self.norm2=nn.LayerNorm(dim,eps=1e-6); self.norm3=nn.LayerNorm(dim,eps=1e-6)
        self.attn1=PackedAttention(dim,16); self.attn2=PackedAttention(dim,16,1024)
        if index>10:
            self.skip_linear=nn.Linear(2*dim,dim); self.skip_norm=nn.LayerNorm(dim,eps=1e-6)
        if index>=15:
            self.moe=ExpertLayer(dim)
        else:
            self.mlp=nn.Module(); self.mlp.fc1=nn.Linear(dim,dim*4); self.mlp.fc2=nn.Linear(dim*4,dim)
    def forward(self,x,context,rope,skip=None):
        if skip is not None:
            x=self.skip_norm(self.skip_linear(torch.cat([skip,x],-1)))
        x=x+self.attn1(self.norm1(x),rope=rope)
        x=x+self.attn2(self.norm2(x),context)
        y=self.norm3(x)
        return x+(self.moe(y) if hasattr(self,'moe') else self.mlp.fc2(F.gelu(self.mlp.fc1(y))))

class UltraDenoiser(nn.Module):
    def __init__(self):
        super().__init__()
        self.x_embedder=nn.Linear(64,2048)
        self.t_embedder=nn.Module(); self.t_embedder.mlp=nn.Sequential(nn.Linear(2048,8192),nn.GELU(),nn.Linear(8192,2048))
        self.blocks=nn.ModuleList([DenoiseBlock(i) for i in range(21)])
        self.final_layer=nn.Module(); self.final_layer.norm_final=nn.LayerNorm(2048,eps=1e-6); self.final_layer.linear=nn.Linear(2048,64)
        self.calls={}
    def forward(self,latents,t,context,indices):
        home=self.x_embedder.weight.device
        freqs=torch.exp(-math.log(10000)*torch.arange(1024,device=home).float()/1024)
        phase=t.to(home).float()[:,None]*freqs[None]
        time=self.t_embedder.mlp(torch.cat([phase.sin(),phase.cos()],-1).to(latents.dtype))[:,None]
        x=torch.cat([time,self.x_embedder(latents.to(home))],1)
        cos,sin=rotary_grid(indices.to(home)); cos=torch.cat([torch.ones_like(cos[:,:1]),cos],1); sin=torch.cat([torch.zeros_like(sin[:,:1]),sin],1)
        caches={}; skips=[]
        for i,block in enumerate(self.blocks):
            device=block.norm1.weight.device
            if device not in caches:
                caches[device]=(context.to(device),(cos.to(device),sin.to(device)))
            c,rope=caches[device]
            skip=skips.pop().to(device) if i>10 else None
            x=block(x.to(device),c,rope,skip)
            if i<10: skips.append(x)
            key=f'blocks.{i}@{device}'; self.calls[key]=self.calls.get(key,0)+1
        x=self.final_layer.norm_final(x.to(home))[:,1:]
        return self.final_layer.linear(x)

class VAESelfBlock(nn.Module):
    def __init__(self):
        super().__init__(); self.attn=PackedAttention(1024,16,norm='layer',fused=True)
        self.ln_1=nn.LayerNorm(1024,eps=1e-6); self.ln_2=nn.LayerNorm(1024,eps=1e-6); self.mlp=DenseMLP(1024)
    def forward(self,x):
        x=x+self.attn(self.ln_1(x)); return x+self.mlp(self.ln_2(x))

class VAEQuery(nn.Module):
    def __init__(self):
        super().__init__(); self.c_q=nn.Linear(1024,1024,bias=False); self.c_kv=nn.Linear(1024,2048,bias=False); self.c_proj=nn.Linear(1024,1024)
        self.attention=nn.Module(); self.attention.q_norm=nn.LayerNorm(64,eps=1e-6); self.attention.k_norm=nn.LayerNorm(64,eps=1e-6)
    def prepare(self,context):
        k,v=self.c_kv(context).reshape(1,-1,16,128).chunk(2,-1)
        return self.attention.k_norm(k).transpose(1,2),v.transpose(1,2)
    def forward(self,x,kv):
        q=self.attention.q_norm(self.c_q(x).reshape(1,-1,16,64)).transpose(1,2)
        return self.c_proj(F.scaled_dot_product_attention(q,*kv).transpose(1,2).reshape(1,-1,1024))

class UltraDecoder(nn.Module):
    def __init__(self):
        super().__init__(); self.post_kl=nn.Linear(64,1024)
        self.transformer=nn.Module(); self.transformer.resblocks=nn.ModuleList([VAESelfBlock() for _ in range(16)])
        self.geo_decoder=nn.Module(); self.geo_decoder.query_proj=nn.Linear(51,1024)
        block=nn.Module(); block.attn=VAEQuery(); block.ln_1=nn.LayerNorm(1024,eps=1e-6); block.ln_2=nn.LayerNorm(1024,eps=1e-6); block.ln_3=nn.LayerNorm(1024,eps=1e-6); block.mlp=DenseMLP(1024)
        self.geo_decoder.cross_attn_decoder=block; self.geo_decoder.ln_post=nn.LayerNorm(1024); self.geo_decoder.output_proj=nn.Linear(1024,1)
    def prepare(self,z):
        x=self.post_kl(z/1.0039506158752403)
        for block in self.transformer.resblocks: x=block(x)
        block=self.geo_decoder.cross_attn_decoder
        return block.attn.prepare(block.ln_2(x))
    def query(self,points,kv):
        angles=(points[...,None]*(2.0**torch.arange(8,device=points.device))).flatten(-2)
        features=torch.cat([points,angles.sin(),angles.cos()],-1)
        decoder=self.geo_decoder; block=decoder.cross_attn_decoder
        x=decoder.query_proj(features.to(decoder.query_proj.weight.dtype)); x=x+block.attn(block.ln_1(x),kv); x=x+block.mlp(block.ln_3(x))
        return decoder.output_proj(decoder.ln_post(x))
