"""Native PyTorch sparse inference backends."""
CONV = 'torch'
ATTN = 'sdpa'
DEBUG = False

def set_debug(debug: bool):
    global DEBUG
    DEBUG = debug
