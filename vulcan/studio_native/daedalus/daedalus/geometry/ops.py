"""Local tensor operations used by the incorporated geometry backends.

Portions of positional embedding and rotary math adapted from ComfyUI
95539f56344958339e39b7582a476267d489b0ee, GPL-3.0; see THIRD_PARTY.md.
"""
import math
from types import SimpleNamespace
import torch
from torch import nn
import torch.nn.functional as F


class Linear(nn.Linear):
    def reset_parameters(self):
        pass

class Conv2d(nn.Conv2d):
    def reset_parameters(self):
        pass

class Conv3d(nn.Conv3d):
    def reset_parameters(self):
        pass

class LayerNorm(nn.LayerNorm):
    def reset_parameters(self):
        pass

disable_weight_init = SimpleNamespace(Linear=Linear,Conv2d=Conv2d,Conv3d=Conv3d,LayerNorm=LayerNorm)

def cast_to(value,dtype=None,device=None):
    return value.to(device=device,dtype=dtype)

def cast_to_input(value,other):
    return value.to(device=other.device,dtype=other.dtype)

def get_free_memory(device=None):
    cuda=device is None or torch.device(device).type=='cuda'
    return torch.cuda.mem_get_info(device)[0] if cuda and torch.cuda.is_available() else 2**30

def optimized_attention(q,k,v,heads,mask=None,skip_reshape=False,skip_output_reshape=False,**kwargs):
    if not skip_reshape:
        q,k,v=[x.reshape(x.shape[0],x.shape[1],heads,-1).transpose(1,2) for x in (q,k,v)]
    out=F.scaled_dot_product_attention(q,k,v,attn_mask=mask,dropout_p=0.0)
    if skip_output_reshape:
        return out
    return out.transpose(1,2).reshape(out.shape[0],-1,out.shape[1]*out.shape[-1])

def optimized_attention_for_device(device,mask=False):
    return optimized_attention

def apply_rope1(x,frequencies):
    pairs=x.to(frequencies.dtype).reshape(*x.shape[:-1],-1,1,2)
    if pairs.shape[2]!=1 and frequencies.shape[2]!=1 and pairs.shape[2]!=frequencies.shape[2]:
        frequencies=frequencies[:,:,:pairs.shape[2]]
    result=frequencies[...,0]*pairs[...,0]
    result.addcmul_(frequencies[...,1],pairs[...,1])
    return result.reshape_as(x).to(x.dtype)

def apply_rope(q,k,frequencies):
    return apply_rope1(q,frequencies),apply_rope1(k,frequencies)

class TimestepEmbedder(nn.Module):
    def __init__(self,hidden_size,frequency_embedding_size=256,*,bias=True,timestep_scale=None,dtype=None,device=None,operations=None):
        super().__init__()
        self.mlp=nn.Sequential(operations.Linear(frequency_embedding_size,hidden_size,bias=bias,dtype=dtype,device=device),
                               nn.SiLU(),operations.Linear(hidden_size,hidden_size,bias=bias,dtype=dtype,device=device))
        self.frequency_embedding_size=frequency_embedding_size
        self.timestep_scale=timestep_scale

    def forward(self,t,out_dtype):
        if self.timestep_scale is not None:
            t=t*self.timestep_scale
        half=self.frequency_embedding_size//2
        frequencies=torch.arange(half,device=t.device,dtype=torch.float32)
        frequencies.mul_(-math.log(10000)/half).exp_()
        angles=t[:,None].float()*frequencies[None]
        embedding=torch.cat([angles.cos(),angles.sin()],dim=-1)
        if self.frequency_embedding_size%2:
            embedding=F.pad(embedding,(0,1))
        return self.mlp(embedding.to(out_dtype))

class LayerScale(nn.Module):
    def __init__(self,dim,dtype=None,device=None,operations=None):
        super().__init__()
        self.lambda1=nn.Parameter(torch.empty(dim,dtype=dtype,device=device))
    def forward(self,x):
        return x*cast_to_input(self.lambda1,x)
