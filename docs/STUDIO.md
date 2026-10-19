# Native 3D studio

Run `python -m vulcan` and open the displayed URL (default5186). The shell controls six independently owned native modules. No module or automation is enabled on first use. Switching a module OFF waits for its requests, native jobs and asynchronous cleanup before releasing its resources. Disconnect and simultaneous OFF/close share a single cancellation; the interface does not report an already-cleaning owner as closed.

| Module | Native editor/API prefix | Preserved capabilities |
| --- | --- | --- |
| library | /library/ | Existing STL/3MF/OBJ library, measurements, CPU thumbnails, collections, listings, parametric STL CAD and family contracts |
| gepetto | /gepetto/ | Figure/UV/texture relief, semantic proposals, editable Blender/.gepettos/portable projects, reference pose and native maps |
| daedalus | /daedalus/ | Image-to-3D generation, refinement, repair, optimization and game-ready results |
| plato | /plato/ | Full silhouette editor and legacy view, composition/fitting/frame, native .silhouettes packages and SVG/PNG/STL exports |
| heron | /heron/ | Measured CadQuery recipes, validated solids and STEP/STL |
| gulliver | /gulliver/ | Printable geographic terrain/cities and STL/3MF results |

The original library HTTP paths listed in the older README sections are relative to `/library`; host control, shared projects and MCP catalogue/calls remain under `/api`. Existing library tool names are unchanged. Native module tool names have namespaces and preserve native_name and inputSchema in the static catalogue. Multipart uploads remain available in the full native UI/API, as before; the historical generic MCP bridge did not expose multipart operations.

Shared projects use `studio-projects.json`: native owner/ID/revision/deep link and retained artifact path/full SHA256/size. Creating or reading a common project starts no engine. Read pieces explicitly from an enabled module, link the chosen revision, register an exported file, and index it through the library's real `model_import_file` operation. Turning modules OFF keeps the common project and downloadable retained files. Original project formats and authoritative stores are retained. A retained file is a reference to a completed revision: changed bytes/size produce409 rather than a new file under the old SHA. Indexing also verifies the SHA actually recorded by the library. Windows keeps a mandatory read-sharing lease through complete import/download/Range/HEAD/error/cancellation, preventing overwrite or replacement until consumption finishes; an already-open writer causes an immediate conflict. Downloads reuse the exact verified descriptor; off-thread read/seek operations are joined before releasing it, including repeated cancellation. Range handling uses the verified Starlette1.9+ file-opening hook. No original is copied. Non-Windows pre/post hashes detect changes but do not provide that Windows mandatory sharing guarantee.

Text and vision use Faustus's `/api/global-model` descriptor and `/api/global-model/v1/chat/completions`. Each job pins endpoint, model and connection revision; static/provider credentials never leave Faustus. JSON schema, images, reasoning and SSE completion metadata are preserved. Truncation, filtering, missing completion or selection changes fail explicitly. The synchronous family facade retains the global consumer and the same job pin, including a CAD repair round. No Ollama text-model fallback, load, download or unload is performed. This requires the corresponding Faustus transport to be installed; missing transport is an explicit unavailable state.

Library automation controls initial rescan and folder watching. Generation automation separately controls recovery of historical queued jobs. Neither automation is enabled by merely enabling its module. A cold MCP catalogue contains160 tools and imports no native engine or SQLite store; it starts no app or worker.

CPU installation: source `pip install -r requirements.txt`, then `python scripts/build_studio.py`. Wheel consumers install the `studio` extra. Daedalus's existing GPU runtime remains separate and is selected through DAEDALUS_ENGINE_PYTHON; DAEDALUS_MODEL_DIR points at the model store. GPU operations use the existing Hub leases. Blender remains configured through SCULPTORS_HOARD_BLENDER/RELIEF_BLENDER. No environment or weights are downloaded just by enabling the shell or discovering tools.

## Migration boundary

Fisiones task934 owns the real rollout after source review. Before moving anything: inventory every native store (including SQLite companions, secrets, IDs, queues, models and binaries), stop its explicit resource owner under the app lock, and perform same-volume atomic moves with receipts. Default target data paths are `data/` for the original library and `data/native/<module>/data` for incorporated modules; explicit data_paths can override these in studio-modules.json. Nested stores move before their parents. No symlink/junction, data copy or dependency on the retired source checkout is introduced.

Preserve old native formats, tokens, project IDs and source paths that still denote user assets. Compatibility app/tool aliases remain internal and point at the same owning module, without autostart or independent Hub cards. Source incorporation and synthetic QA alone do not certify real migration, installation or live GPU/model quality. See STUDIO-STATUS.md for the current delivery state.
