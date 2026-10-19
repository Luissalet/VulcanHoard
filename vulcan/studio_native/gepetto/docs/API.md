# API

The interface talks to the FastAPI backend (`backend/app.py`); the complete route list, with schemas, is served at `/openapi.json` (and `/docs`) while the app runs.

## Faustus copilot routes

Mounted by `install_copilot(app, "gepetto", DATA, app_name="Gepetto's Hoard")` from the vendored `backend/hoard_link/copilot.py` unless `HOARD_COPILOT=0`. They are a thin local relay; the panel, conversation, model and tools live in Faustus.

| Route | Purpose |
| --- | --- |
| `GET /api/copilot/info` | `{app_id, app_name, data_space_id, panel_url, server, tool_server_id, settings_url}`; `503` when Faustus is not reachable. `data_space_id` is stored in `data/copilot-space-id`. |
| `GET /api/copilot/panel.js` | The Faustus chat component, relayed from Faustus. |
| `POST /api/copilot/proxy` | Forwards `/api/hoard-copilot/*`, `/api/chat/*` and `/api/session(s)` calls (including the SSE stream, unbuffered) to Faustus; forces `origin.app_id` and `data_space_id` into the context. Any other path is `400`. |
| `GET /api/copilot/launcher.js` | The floating button and panel host; its tag is added to every HTML page the app serves. |

The page answers the launcher through `window.hoardCopilot.context()` (see `src/hoard-copilot.ts`): `project`, `active_objects` (`project`, `texture`, `region`, `evaluation`) and `view` (`page`, `surface`, `image_mode`, `inspector_tab`, `editor` for unsaved input). `onTurnEnd` reloads the project list and the open project.

`PATCH /api/projects/{pid}/assets/{aid}/regions` accepts `{"patches":[{"id":0,"fields":{"name":"Surface"}}]}`. Only `name`, `height` and `displacementColor` can change. The server merges them into the current regions under its existing edit lock and applies the same validation, linked-height rules and history as the full edit route. Invalid patches write nothing. The full region-list route and its revision conflict response remain available. The web editor sends only its changed fields and rebases drafts on refresh; navigation during a request cannot replace another project's view. The copilot captures the view before awaiting project data.
