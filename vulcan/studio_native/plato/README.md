# Plato's Hoard

[Español](README.es.md)

A local editor for turning 2D images into editable silhouettes and printable 3D relief. Upload a PNG, refine the layers in the browser editor and export the resulting black silhouette, vector SVG or extruded STL. The original one-shot converter remains available at `/legacy`.

## Run locally

Requires Python 3 and the packages in `requirements.txt`.

```sh
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open [http://127.0.0.1:5000/editor](http://127.0.0.1:5000/editor). On other operating systems, use `.venv/bin/python` in the commands. The Flask app stores editor documents and jobs under `data/` and limits uploads to 16 MB.

## Faustus and Hoard Hub

[`faustus-plugin.json`](faustus-plugin.json) lets Hoard Hub discover and start the app. Faustus reaches its REST editor API through the generic REST MCP bridge defined by its manifest. Plato has **no** shared `/api/agent/call` contract: the bridge and Hub handle discovery and launch.

### Export events

When `python app.py` runs and an export job finishes with STL in its formats, Plato unpacks every STL of the bundle into `data/exports/<job id>/<family>/` and announces each one on the family bus as `plato.export.done {path, ref, title, format, job_id, document_id, layer_id, family}`, where `ref` is `hoard://plato/export/<job id>` and `title` is the document and layer name. The Hub's recommended rule hands the `path` to the model library (`model_import_file`). Exports have no 3MF format, so only STL is announced. The events of one bundle are sent 5.5 s apart (`PLATO_EVENT_SPACING_S`) because that rule ignores a repeat inside its 5 s cooldown. Plato writes `data/mcp-token` at start (the Hub knows the sender by it). A job is noticed by a background watcher, not only when the editor asks for it. Without the Hub nothing changes: the STL files are still unpacked and the emit is dropped. The legacy `/api/process` preview is not an export and emits nothing.

`hoard_link/` is the shared family library, vendored unchanged; only its standard-library modules (`family`, `atomic`, `tokens`, `guard`) are loaded, through a private package name in `plato_family.py`, so Plato's requirements do not change. From it the app takes the request guard (every request is checked for a loopback `Host`, a same-site `Origin` and Fetch Metadata; `PLATO_ALLOWED_HOSTS` opens a LAN or tailnet name on purpose), the atomic write of editor documents (temp file, fsync, replace with retries on Windows) and the token file. The server now listens on `127.0.0.1` (`PLATO_HOST` and `PLATO_PORT` change it; before, it listened on all interfaces).

## Faustus copilot

Every page of the app (the layer editor at `/editor`, the legacy page at `/legacy`) has a floating **Faustus** button. It opens a side panel with the Faustus chat, so you can ask for things while you look at the plate: the agent sees which document and layer you have open and reads or changes them with the same REST editor API the REST MCP bridge exposes. Conversation, model, tools and memory live in Faustus; Plato keeps no chat loop and no history.

What the panel sends with every turn (`window.hoardCopilot.context()` in [`static/editor/hoard-copilot.js`](static/editor/hoard-copilot.js), helpers in [`copilot_context.mjs`](static/editor/copilot_context.mjs)):

- `project`: the open document (`external_project_id` = document id, `title` = its name). One conversation per document: opening another document shows that document's conversation.
- `active_objects`: the document (`kind: "document"`: id, name, revision, canvas in mm, layer count, the layer tree as id / title / parent) and, when a layer is selected, that layer (`kind: "layer"`: id, title, parent, visible, locked, asset, pose). The document is read from the server at the moment of the turn, so an agent's earlier change is already in it. With no document (the legacy page, a document that no longer exists) it is `[]`.
- `view.editor`: canvas width, height and margins you typed but have not applied (`{kind: "canvas", dirty: true, fields}`), only when there are any.

When a turn ends the editor reloads the document if its revision moved (the agent changed it), redraws, drops the selection of a layer that is gone and keeps what you were typing in the canvas fields. Changed fields, including cleared inputs and typing during the load, remain pending even when the agent changes the same canvas; untouched fields follow the new document. Pending fields stay in the context across revisions. The undo history restarts at that point, because its snapshots predate the agent's change. The floating button sits at the bottom right of the canvas area, left of the inspector, so it never covers its fields.

Routes added by [`plato_copilot.py`](plato_copilot.py), a small Flask blueprint over the vendored Hoard Link copilot pieces (`CopilotRelay`, `multipart_context`, `inject_launcher`, `LAUNCHER_JS`), with the same four routes as the Hoard Link copilot for FastAPI and Node hosts:

| Route | What it does |
|---|---|
| `GET /api/copilot/info` | app id and name, a stable data space id (`data/copilot-space-id`), where the panel comes from |
| `GET /api/copilot/panel.js` | the `<faustus-chat>` element, served by Faustus through the relay |
| `POST /api/copilot/proxy` | every panel request, chat turns as Server-Sent Events streamed without buffering; the Faustus token stays on the server, `origin.app_id` / `data_space_id` are forced, and only Faustus's copilot paths (`/api/hoard-copilot/`, `/api/chat/`, `/api/session(s)`, `/api/chat_stream`) are reachable |
| `GET /api/copilot/launcher.js` | the button and panel script, added to every HTML page the app serves (never to `/api/` answers) |

Faustus is found through `PLATOS_FAUSTUS_URL` or `HOARD_FAUSTUS_URL` (plus `HOARD_FAUSTUS_TOKEN`), or the Hoard Link config, falling back to `127.0.0.1:7000` / `7001`. **To turn it off** start the app with `HOARD_COPILOT=0`: no routes, no button, nothing injected. `faustus-plugin.json` declares it under `x-family.copilot`.

## Verify

```sh
python -m pytest tests/editor -q
python -m pytest tests -q
```

The repository's `implementation_status.json` records completed editor tasks and known test results. The full historical suite has recorded a pre-existing star IoU failure; check the current run rather than assuming it passed. Node's `npm test` is a placeholder and is not the Python test suite.
