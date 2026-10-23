# Local multiview geometry

Candidate implementation for task1185. CPU numerical checks, native API/MCP transport and checkpoint topology checks pass. A full local GPU generation and geometry review are still pending; this document does not certify reconstruction quality or peak memory.

The Create form offers **Reconstrucción multivista**. Add two or more foreground/alpha images of the same object. **Poner primera** chooses the anchor view that fixes orientation; each image can be removed before submission. Opaque inputs are treated as already cropped foreground, without semantic background removal. Views jointly condition one sparse structure and one shape, rather than generating and merging independent meshes.

The current cut produces geometry at512 with GLB/STL/PLY export and normal preserved repair/optimization versions. It does not generate textures, UVs, PBR materials, a rig or a1024 pass. Refinement can create another version when **Conservar aspecto** is off. The original view order, normalized source-image SHA256 values, model revisions and a per-voxel selected-view array are retained in the job. The saved view list and reconstruction JSON are available in the normal viewer.

Native REST: import images with `POST /api/images`, then `POST /api/multiview` with `image_ids`, `title`, `seed`, and physical `gpu` (default1). The native MCP tool is `model_generate_multiview`; Vulcan's cold catalog exposes `daedalus_model_generate_multiview` with the same schema. Jobs support the usual status, cancellation, library and export operations. Inference reserves only the selected GPU through the Hub, without stopping other models.

## Provisioning

Use Python3.11 for the isolated content interpreter. Run the packaged explicit setup script with `--environment <venv>` and/or `--models <normal-model-root>/multiview`. It installs PyTorch2.11/CUDA12.8 and pinned PEFT/Transformers dependencies, downloads19.17GB of public pinned files, checks every SHA256 and retains a weight manifest. `--models ... --verify-only` checks existing files without network access. The existing generator's local `generator/clip_vision/dino_v3_vit_l.safetensors` is reused; it is not duplicated.

Set **Python del entorno multivista** in Motores locales, use `models_configure.multiview_python`, or set `DAEDALUS_MULTIVIEW_PYTHON`. This setting is separate from the server and draft interpreters. Inference never downloads files and the worker sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. There is no external inference API, paid endpoint or third-party CLI/server dependency.

The detached native source, model topology and immutable origin hashes are retained in `multiview_model/PROVENANCE.json`. Each stage is constructed from meta tensors and strict local mmap checkpoints; large generators are released before loading the next stage. Packed attention uses PyTorch SDPA and the existing native sparse convolution/dual-grid implementation. The encoder feature path is checked against a small independent Transformers model. BF16 inference and geometry-only output are deliberate differences from the full reference demo.

Original source MIT does not relicense all components or weights. Joint VGGT-derived checkpoints retain CC-BY-NC-4.0; the Meta VGGT and DINOv3 notices and TRELLIS base terms remain applicable. See `THIRD_PARTY.md` and the retained notices. BRIA/RMBG is not part of this route.
