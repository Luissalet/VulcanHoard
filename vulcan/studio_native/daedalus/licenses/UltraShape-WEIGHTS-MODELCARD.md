---
license: apache-2.0
base_model:
- tencent/Hunyuan3D-2.1
pipeline_tag: image-to-3d
---

![teaser](https://cdn-uploads.huggingface.co/production/uploads/62fbe6cfa80632fbd47bb8ca/3u4DCuByPLQPkjuX4Msjn.png)

<h1>UltraShape 1.0 Refine Model</h1>

<a href="https://arxiv.org/pdf/2512.21185"><img src="https://img.shields.io/badge/arXiv-2512.21185-b31b1b.svg?style=flat-square" alt="arXiv"></a>
<a href="https://pku-yuangroup.github.io/UltraShape-1.0/"><img src="https://img.shields.io/badge/Project-Page-blue?style=flat-square" alt="Project Page"></a>
<a href="https://huggingface.co/infinith/UltraShape"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Models-yellow?style=flat-square" alt="HuggingFace Models"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square" alt="License"></a>

In this report, we introduce **UltraShape 1.0**, a scalable 3D diffusion framework for high-fidelity 3D geometry generation. The proposed approach adopts a **two-stage generation pipeline**: a coarse global structure is first synthesized and then refined to produce detailed, high-quality geometry.

To support reliable 3D generation, we develop a comprehensive data processing pipeline that includes a novel **watertight processing method** and **high-quality data filtering**. This pipeline improves the geometric quality of publicly available 3D datasets by removing low-quality samples, filling holes, and thickening thin structures, while preserving fine-grained geometric details. 

To enable fine-grained geometry refinement, we decouple spatial localization from geometric detail synthesis in the diffusion process. We achieve this by performing **voxel-based refinement** at fixed spatial locations, where voxel queries derived from coarse geometry provide explicit positional anchors encoded via **RoPE**, allowing the diffusion model to focus on synthesizing local geometric details within a reduced, structured solution space.

Extensive evaluations demonstrate that UltraShape 1.0 performs competitively with existing open-source methods in both data processing quality and geometry generation.


## 🔗 BibTeX

If you found this repository helpful, please cite our report:
```bibtex
@article{jia2025ultrashape,
    title={UltraShape 1.0: High-Fidelity 3D Shape Generation via Scalable Geometric Refinement},
    author={Jia, Tanghui and Yan, Dongyu and Hao, Dehao and Li, Yang and Zhang, Kaiyi and He, Xianyi and Li, Lanjiong and Chen, Jinnan and Jiang, Lutao and Yin, Qishen and Quan, Long and Chen, Ying-Cong and Yuan, Li},
    journal={arxiv preprint arXiv:2512.21185},
    year={2025}
}
```

## Acknowledgements

We would like to thank the contributors to
the [Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1), [Lattice](https://lattice3d.github.io/), [Cubvh](https://github.com/ashawkey/cubvh)
and [HuggingFace](https://huggingface.co) repositories, for their research and exploration.