# API additions: Faustus copilot

The native library APIs and MCP tool schemas retain their behavior. The copilot adds these endpoints:

| Endpoint | Behavior |
| --- | --- |
| `GET /api/copilot/info` | Stable library identity and shared panel/proxy information. |
| `GET /api/copilot/panel.js` | Faustus's actual shared web component. |
| `GET /api/copilot/launcher.js` | Official HoardLink launcher. |
| `POST /api/copilot/proxy` | Shared JSON, multipart and streaming transport; credentials stay server-side. |
| `POST /api/copilot/context` | Resolve native IDs and an editor snapshot without creating objects or starting work. |

`copilot_context_get` exposes the same context envelope over native MCP. For its input/envelope, editor, selection and refresh semantics, see [the copilot contract](copilot.md).
