# Local single-GPU drafts

Vulcan's generation module offers **Borrador · una GPU** alongside the existing
four-GPU TRELLIS pipeline. TripoSR estimates a complete object from one image;
unseen surfaces and fine parts are generated guesses. Its vertex-colored GLB/PLY
is a prototype, not a guarantee of matching geometry or PBR materials. Existing
generations and versions remain intact.

## Explicit setup

Run from the source checkout using Python3.11:

```powershell
py -3.11 -m vulcan.studio_native.daedalus.tools.install_draft_engine --environment .engine-venv --models D:/LocalAI/HoardStorage/daedalus-models/triposr
```

Setup downloads pinned public weights and ordinary Python packages. It does not
start an app or load a model. The source module is packaged with the studio too;
installed-package users can run the same module from their installed environment
and choose an independent environment folder. Configure the printed Python path
in **Motores locales → Python del entorno de borradores**, or set
`DAEDALUS_DRAFT_PYTHON`. The main model folder still contains `generator/`,
`refiner/`, `ultra/` and the new `triposr/`. Setup verifies the 1,677,246,742-byte
checkpoint against its published SHA256 and the configuration files against Git
blob IDs, retaining `weights-proof.json` and the model card beside the weights.
No keys, paid service or hosted inference API are required. Once installed, the
new inference path loads local files only. It does not download a missing encoder
configuration during generation.

## Workflow and native tools

1. Enable Generación3D explicitly, choose a reference image and select the draft
   generation type. Choose a physical GPU and either draft mesh detail level.
2. Generate normally and inspect its retained progress, error, measured geometry
   and memory. Cancellation uses the existing owned-worker lifecycle.
3. Review silhouette, hole openings, thin parts and other views. Export GLB/PLY
   with vertex colors or STL geometry at an explicit size. **Para juegos** can
   bake the draft's colors into a separate version.
4. Generate another version from the same currently selected image to compare
   it with the completed version. API/MCP callers can pass `source_job_id`;
   it must name a completed version with the same image ID. It remains a parent
   comparison reference and is never overwritten. Uploading another image does
   not implicitly attach it to a different source.

The normal `model_generate` operation (public studio MCP
`daedalus_model_generate`) accepts `generator: "triposr"`,
`draft_resolution: 128 | 256`, `draft_gpu` and optional `source_job_id`.
The legacy default remains `generator: "trellis"`. TRELLIS's 512/1024 setting is
separate from the draft's mesh grid. The native worker obtains a Hub lease for
only the selected draft GPU, sets CUDA visibility to that device and runs the
owned network/export pipeline. Cold tool discovery starts no engine.

A draft has no TRELLIS shape latent, so the learned TRELLIS upscale operation is
unavailable. Refinement can create new geometry/appearance from its reference,
but the existing PBR-voxel transfer cannot preserve draft vertex colors; the
UI disables that option for a draft source and the API rejects an incompatible
request. Other versions, generated files and job failures remain available.

## Source and limitations

Network architecture incorporated from VAST-AI-Research/TripoSR at
`107cefdc244c39106fa830359024f6a2f1c78871`; per-file Git blob IDs and modifications
are recorded in the native `docs/triposr-incorporation.json`. MIT and original
Apache2 notices/licenses are retained. The model repository is
`stabilityai/TripoSR` revision `5b521936b01fbe1890f6f9baed0254ab6351c04a`, published
MIT. Its encoder configuration is from `facebook/dino-vitb16` revision
`f205d5d8e640a89a2b8ef0369670dfc37cc07fc2`, Apache2. The application does not import
another product checkout or dispatch that product's CLI.

Native adaptations use a local encoder configuration, tensor-only checkpoint
loading and scikit-image CPU marching cubes with the input xyz axes. They remove
the remote background-removal path and custom torchmcubes build requirement.
The existing local flat-background removal and transparent-image handling remain
available; complex backgrounds are not promised automatic segmentation.

Two original CPU-rendered reference objects (open ring/thin stand and fork/brace)
were generated through actual stdio MCP, owning API, Hub lease and native GPU
worker on an RTX4070Ti. Observed128-grid end-to-end worker times were21.7s/10.4s;
meshes had15,484/33,440 faces. PyTorch reported1,867.5MiB peak allocation and
2,028MiB peak reservation on the single visible GPU. Those counters exclude
driver/context and other processes; they are not total board VRAM or guarantees
for every input, device or detail level. Independent Blender import and editable
scene/FBX exports were exercised. Quality and multiview findings belong to the
retained review evidence; these technical results do not certify better shape
than TRELLIS or readiness for printing.

The256-grid native GPU worker also completed with Python socket connections
blocked throughout model loading, inference and export; the parent acquired its
local Hub lease first. This verifies the installed generation path works without
network access. Geometry review used four fixed cameras and normalized scale,
without rigid pose registration: the draft's canonical rotation differs from
the reference scene. The open ring, fork separation and brace remain visible;
surface ripples, rounded edges and irregular thickness remain visible too.
These are useful prototypes that still need geometric correction.

This path reconstructs **one image**. Several unposed photographs are not fused
by batching independent drafts. UniForge3D's MIT code does not remove the
noncommercial/commercial restrictions of its referenced model dependencies;
those weights and its hosted service are not installed by this feature.
