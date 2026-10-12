# Vulcan copilot contract

Vulcan mounts the official `hoard_link.copilot.install_copilot` relay and launcher. The actual `<faustus-chat>` bundle comes from Faustus. No Vulcan conversation store or agent loop is introduced.

## HTTP and MCP

`GET /api/copilot/info` reports `app_id=vulcan`, the persisted `data_space_id`, local panel/proxy URLs and the Faustus settings URL. `GET /api/copilot/panel.js` streams the Faustus component. `GET /api/copilot/launcher.js` serves the common launcher. `POST /api/copilot/proxy` forwards the shared conversation, streaming and upload contract, preserving upstream errors and forcing Vulcan's actual origin. Authentication stays on the server.

`POST /api/copilot/context` and the read-only native MCP tool `copilot_context_get` accept `page`, optional `model_id`, `listing_id`, `root_id`, `album_id`, `locale`, `editor` and `selection`. IDs are resolved against the same SQLite stores used by the existing domain tools. Unknown, deleted, `new`, boolean or out-of-range IDs produce no active object. These reads create no rows or work. Folder listings use their own native row ID; `view.folder` carries the existing `root_id` and relative path required by `folder_listing_get/set`.

The envelope has `origin`, `active_objects`, optional `project`, `view` and `selection`. The singular `view.editor` includes the native `kind`, verified `object_id` (or null), `available`, form `fields` and `dirty_fields`. The native root ID supplies `project.external_project_id`; albums have their own object ID without an invented project. Model drafts include metadata and nested listing fields; changed listing keys are qualified as `listing.title`, for example. Text selection includes its field, text and UTF-16 range. Filters come from the captured route. A request snapshots the current form before asynchronous native resolution.

`window.hoardCopilot.context` supplies this domain snapshot to the common launcher. `onTurnEnd` refreshes status and the current native list/editor. Refresh merges remote fields into the draft while retaining local changes. Save replies acknowledge submitted fields only, so an older response cannot reset a later edit or unrelated remote field. Model listing and folder listing read/merge/write execute inside a database transaction; folder listings retain their existing atomic `cults3d.json` write.

New album/root forms, gallery search and organizer input are transient editor context. Context never creates albums/roots, starts scans, plans or applies filesystem moves. Native MCP catalog order and existing schemas remain intact; `copilot_context_get` is appended.

## Verification

Run `venv/Scripts/python -m pytest -q`, `node --test tests/test_copilot_forms.mjs`, and `npm run build`. Context tests cover real/new/missing IDs and concurrent disjoint writes; draft tests cover partial saves, refresh and older replies. Private native browser QA uses the shipped Faustus SDK with an explicitly scripted HTTP provider and real stdio MCP calls. It verifies the panel turn, all principal views, selection, refresh and partial-save preservation; it does not measure a language model's behavior. This delivery migrates Vulcan only.
