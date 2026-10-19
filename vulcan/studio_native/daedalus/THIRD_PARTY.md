# Third-party provenance

Daedalus incorporates detached source and its own execution pipeline. Historical references below are provenance, not operational dependencies on another application checkout or CLI.

| Component | Origin / revision | License and use |
| --- | --- | --- |
| Sparse transformer, surface VAE, sparse operations, DINOv3 architecture implementation | ComfyUI `95539f56344958339e39b7582a476267d489b0ee`, paths in `docs/source-incorporation.json` | GPL-3.0, root `LICENSE`; incorporated and modified imports, tensor helpers and model placement. |
| Generator model weights | Comfy-Org/TRELLIS2 `430a9d09b2416687018c8fe8edced2ad4858a439`, based on Microsoft TRELLIS.2 | Published MIT model files; licenses and immutable weight proof retained in the separate model store. |
| Generator visual encoder weights | DINOv3 | Custom DINOv3 License, copy in `licenses/DINOv3-LICENSE.md`; do not describe the whole pipeline as MIT. |
| Refinement model weights | VAST-AI/DetailGen3D `ff2fac7768babe768bb73d03e998f80a397a3454` | MIT model repository; pinned files and SHA256 proofs retained in the model store. |
| Rectified-flow scheduler | DetailGen3D `acdf05e1677c9aa7e656efddf4973e6b02ef1c24`, standalone scheduler file | MIT / retained Apache notices; `daedalus/geometry/DETAILGEN-LICENSE`. |
| Refinement network implementation | Own `refiner_model.py`, using Hugging Face Diffusers0.32.2 primitives | Diffusers Apache-2.0. The upstream DetailGen3D transformer modules containing Tencent EU-exclusion and FlashVDM notices are not incorporated or imported. Root MIT alone was not treated as permission to use those restricted modules here. |
| Refinement visual encoder | DINOv2 via Transformers4.49.0 | Apache-2.0; weights provided in the pinned DetailGen3D model repository. |
| WebGL model viewer | Google model-viewer, bundled local build | Apache-2.0, `client/vendor/LICENSE-model-viewer`. Its upstream license comments are preserved. |
| Alternative refinement weights | `infinith/UltraShape`, revision `5aeb21a7185d39f042d02b2695802f125a6f5159` | Published Apache-2.0 tag and Hunyuan3D-2.1 base; separate reference code has Tencent geographic restrictions. Conflicting provenance is retained in `docs/ULTRA-PROVENANCE.md`, not treated as unrestricted clearance. Weights are not redistributed. |
| Alternative refinement execution | Own `ultra_model.py` and `ultra_refinement.py`, PyTorch/Transformers/Diffusers primitives | No reference pipeline, transformer, CUDA extension or CLI is imported, copied or packaged. Architecture reference revision and implementation differences are documented. |
| Hoard family theme | HoardLink generated `hoard-theme.css`, vendored byte-identical | Existing shared family theme, app-specific turquoise overrides in own CSS. |
| Cinzel and Source Sans 3 | Google Fonts and existing family assets; source hashes in `docs/font-provenance.json` | SIL Open Font License copies beside the self-hosted fonts. |
| Daedalus dragon icon | Built-in image generation with existing Hoard family icon references | Exact prompt and provenance in `docs/icon-*`; original generation retained separately. |

The Python dependencies retain their respective licenses in the installed environment. The application repository contains no model weights. Redistribution of weights must include the corresponding model licenses and provenance. Research reference checkouts remain separate from the installed product.

Optional mesh processing: PyMeshFix0.17.1 / MeshFix (GPL-3.0, pinned to the published GPL release; licenses/MeshFix-0.17.1-LICENSE.txt), xatlas-python0.0.11 and xatlas (MIT; licenses/xatlas-python-LICENSE.txt), fast-simplification (MIT), Trimesh (MIT) and SciPy (BSD). These are in-process geometry libraries, not external application runtimes. Own CPU job/validation/material baking code lives in daedalus/geometry/postprocess.py. Exact notice sources and SHA are in docs/postprocessing-provenance.json.
