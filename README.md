# Vulcan's Hoard

**Listings → Combined catalog** prepares native folder and model listing metadata,
optionally within a folder. Its scope is independent of the listings table filters.
Copy and download share the complete native JSON text; preview counts entries,
not unique meshes, and distinguishes folder status from model listing source.
Price hints stay as stored text. No model files are included or products published.

A 3D studio with independently activated modules: model library (Vulcan), editable figure relief and Blender projects (Gepetto), image-to-3D generation/refinement (Daedalus), silhouette composition and extrusion (Plato), measured CAD (Heron), and printable terrain/cities (Gulliver). The complete native editors, APIs, formats and engines are owned here.

All six modules and their automation start **OFF** on first use. Open Vulcan explicitly, activate the function you need, and stop it from the same screen. Listing MCP tools starts no app, engine, database or watcher. Saved explicit choices are remembered; merely enabling generation does not resume historical queued jobs unless its automation is selected.

Shared projects retain native object IDs/revisions and exported-file references with SHA256. You can open the native piece, download its retained file, and index an exported STL in the same library without converting the native project. The complete cold catalogue contains160 tools; names longer than the MCP limit have short public aliases while retaining their native names and schemas.

Install CPU dependencies with `pip install -r requirements.txt` (source) or `pip install ".[studio]"`, then build the two React interfaces with `python scripts/build_studio.py` and run `python -m vulcan`. Blender and the separate Daedalus GPU environment/model store retain their existing roles and require explicit operations. Text/vision inference uses the exact global model configured in Faustus, through its authenticated stateless transport; provider credentials remain there. No local text fallback is used. See [studio operation and migration](docs/STUDIO.md), [source/verification status](docs/STUDIO-STATUS.md), and [licensing](NOTICE-STUDIO.md).

## Library module

Your personal library of 3D-printable models, indexed on your own PC. Point it at the folders where you keep your STL, 3MF and OBJ files and it measures every model (size in mm, triangles, volume, surface, watertight or not, number of bodies), renders a thumbnail on the CPU, finds duplicates and near-duplicates, and keeps tags, notes and a marketplace listing (title, description, tags, category) per model. An assistant reaches the same library through MCP, so it can find "that dragon bust from last spring", quote its real dimensions and draft the listing text for you — without inventing anything the geometry does not say.

The library stays on the machine: SQLite with FTS5 and thumbnails as WebP files, without an account. Faustus's shared copilot handles conversation and tool execution through the local HoardLink relay; the existing listing and CAD services keep their bounded domain jobs.

## Faustus copilot

The floating Faustus button opens the shared chat panel in every view. It receives the current native model, folder listing, root or album ID, the root's project scope, filters, selected text and the current form draft. New and missing objects have no active ID; reading context never creates a model, scans a folder or runs CAD. After a turn, the view refreshes without discarding locally edited fields. Model and folder listing saves send only changed fields and merge them atomically with other updates.

Configure the Faustus endpoint with `VULCAN_FAUSTUS_URL` (or `HOARD_FAUSTUS_URL` / HoardLink configuration); credentials stay in server configuration. The shared `data/copilot-space-id` identifies this library across ports and restarts. Conversation history, drafts and execution belong to Faustus; Vulcan keeps its native MCP tools. See [the copilot contract](docs/copilot.md).

Part of the Hoard family (see `faustus-plugin.json`).

## What it does

- **Roots** = folders you choose, with include/exclude globs (default `**/*.stl, **/*.3mf, **/*.obj`; `.git`, `node_modules`, hidden folders excluded), enable/disable and optional folder watching (rescan on change, debounced).
- **Scanning** is incremental: size + mtime first, then SHA-256; only changed files are parsed; deleted files are purged; a touched-but-identical file is not re-read. Hashing, parsing and thumbnail rendering run in a `ProcessPoolExecutor` (`VULCAN_SCAN_WORKERS`, default `cpu_count − 2`, spawn context so Windows and Linux behave the same); the scan thread only walks the folder, decides what changed and writes results, so SQLite keeps a single writer. Live progress per root: files done/total, parsed files per second, ETA, current file, per-file errors. Files over `VULCAN_MAX_FILE_MB` (300) are listed as `skipped` with a note instead of being loaded into memory.
- **Per-root options** for big generated trees (a slicer or a photogrammetry job that exports thousands of layer meshes): `thumbnails` = `all` | `top-level` (only files in the root folder and its immediate subfolders get a thumbnail) | `none`; `skip_small_bytes` (files under that size are not listed at all; default `VULCAN_SKIP_SMALL_BYTES` = 0); and the exclude globs, which are the right tool for skipping a generated folder by name — e.g. `**/export_job_*/**` or `**/normal_registered/**` — one pattern per folder, `**` on both sides.
- **Geometry** with `trimesh`: triangles, unique vertices, bounding box in mm, volume (cm³), surface (cm²), watertight, body count (connected components), and a `units_guess` flag when the size makes millimetres unlikely (`inches` under 5 mm, `meters` under 1 mm, `large` over 1.5 m). 3MF units are converted to mm; a 3MF with several objects is measured as one mesh with N bodies.
- **Thumbnails** in pure Python (numpy + Pillow, no OpenGL, so they work on any Windows box without a GPU driver): orthographic camera from the front-right at 30° elevation with Z up (print orientation), flat shading from one fixed light, per-pixel depth resolution over triangles sorted far-to-near, rasterised in numpy batches sized by the triangles' exact screen footprint. Meshes under 5 000 triangles come out at 384 px with 1.5× supersampling, bigger ones at 512 px with 2×; meshes over 300 000 triangles are decimated for the thumbnail only (quadric decimation through `fast_simplification` when installed, else a deterministic face subsample); closed, consistently wound meshes skip their back faces. WebP with transparent background, stored as `<DATA_DIR>/thumbs/<sha256>.webp` (identical files share one). A 300-triangle layer mesh renders in ~60 ms, a 300k-triangle scan in ~0.9 s; a deleted thumbs folder is regenerated on the next rescan without re-measuring.
- **Names**: the file stem, prettified (`dragon_bust-v2` → `Dragon bust v2`), editable. **Collection** = the immediate folder name by default, editable. **Tags** (lower-case, de-duplicated) and **notes** per model survive rescans.
- **Listings**: title, description (markdown), tags, category, price hint, language, with `listing_source` (`manual` | `assistant`) and `listing_updated_at`. The UI has a "Copiar ficha" button that produces the plain text to paste into a marketplace form (title, description, dimensions, category, tags, price hint).
- **Folder listings**: the marketplace convention where each *product* is a whole folder of models (e.g. `Contornos pokemon/1-3`), not one model. Its listing lives in that folder as `cults3d.json` (`{title, description, tags}`, exactly 20 tags, English description of 200+ characters) and is imported on every scan; an optional `cults3d_template.json` at the root (naming grammar, required/forbidden words, title patterns, base tags — every key optional) drives validation and drafting. Statuses: `none` → `draft` → `checked` → `approved`. Writing a folder listing (from the UI, the REST API or an MCP tool) writes both the database row and `cults3d.json` (atomic, one-time `.bak` of whatever was there before). Drafting uses the configured global model through Faustus (vendorized in `vulcan/hoard_link/`, same as every other Hoard app): folder name, file names/sizes/extents, the template and up to 3 already-approved listings from the same root, in the background with progress, one folder at a time; with no model backend reachable the tool returns the assembled material instead of failing, so the assistant can draft it and save with `folder_listing_set`. Export to CSV/Markdown/JSON in `data/exports/`.
- **Organise a collection** (`collection_plan` / `collection_apply` / `collection_undo`, the **Organizar** page): turns a folder of loose files and folders into one folder per group from a list the user pastes, with no model. The list has one group per line and members separated by `>`, `->`, `→`, a comma or a dash with spaces (`001 Bulbasaur > Ivysaur > Venusaur`; `Name: a > b` names the group, otherwise the first member does), or is a CSV `group,member,number`, or the path of a `.txt`/`.csv`. `collection_plan` (read-only, deterministic) scans the immediate children of the folder and matches each to a member ignoring case, accents, separators and number prefixes (`0001_bulbasaur.stl` = `Bulbasaur`; `Mr. Mime` = `mr_mime`), with an optional alias map; an exact name beats a name found inside a longer one, the member with the most words wins, and equal candidates are reported as *ambiguous* instead of guessed. Each group becomes `target_template` (default `{number} {group}`, `{number}` zero-padded to the widest number in the list; also `{first}`, `{last}`, `{count}`, `{index}`). The plan lists the items moving into each group, items already in place (including those already inside an existing target folder), unmatched items, ambiguous items and conflicts (target exists as a file, name already taken, two groups with the same folder name) and is stored with an id under `<DATA_DIR>/organize/plans/`. `collection_apply` re-checks the disk, creates the folders and moves the items: it **never deletes and never overwrites** (a collision is skipped and reported), refuses names that would leave the folder, writes an undo journal under `<DATA_DIR>/organize/journals/` and queues a rescan of the affected root. `collection_undo` moves everything back from the journal (again without overwriting; an occupied original place is skipped) and removes only the group folders the apply created and that are empty again.
- **Sheets for everything in one call** (`sheets_batch`, the «Fichas de todas» button): creates or refreshes sheet drafts for every folder of models under a root (the `cults3d.json` convention) or, with `scope=models`, every model, reusing the per-folder logic. The default `skeleton` mode is deterministic, needs no language model and loads none: title, English description and tags come from the folder name, the formats, the measured sizes and the root's template tags (`required_tags`, `base_tags`, `tags.pool`, `forbidden_words`); drafts stay in status `draft`. Existing drafts are rewritten only with `refresh=true`, checked/approved sheets (and manual model listings) only with `overwrite=true`; `dry_run` reports the counts without writing. `mode=model` queues the existing background drafting with the local model instead. Returns created / refreshed / skipped counts with the reason for each skip.
- **Text to parametric CAD** (`cad_from_text` / `cad_regenerate` / `cad_spec_schema`, REST `/api/cad/*`): describe a part ("a 60 x 40 x 5 mm plate with four 5 mm holes") or pass a spec, and Vulcan builds it in millimetres and adds it to the library as a new model. A spec is a small declarative JSON (`vulcan/cad.py`): primitives (`box`, `rounded_box`, `cylinder` with `radius_top` for cones, `sphere`), booleans (`union`, `difference` with through/blind `hole` cutters, `intersection`), `translate` / `rotate` / `scale`, `linear_pattern` / `polar_pattern`, named parameters with units and min/max, and expressions such as `"$width/2 + 3"`. Text to spec uses the configured global model through Faustus (`link.sync.chat`, JSON mode) with the node reference in the prompt; the answer is validated and compiled, and a rejected one gets exactly one repair round with the error list. When no model answers, the tool says so and the assistant can write the spec itself (`cad_spec_schema`) and pass `spec=`. A spec can carry an `expect` block (bounding box, volume, tolerance) and the result reports the measured bounding box, volume, surface, watertightness, bodies and a pass/fail verification against it. Each part is stored in `data/cad/` as `<slug>-<id>.stl` (indexed like any model, tagged `cad`), `<slug>-<id>.cad.json` (the spec with its current parameter values, the description and a short history) and `<slug>-<id>.check.png` (a render made with the thumbnail renderer, also at `GET /api/cad/<id>/check.png`). `cad_regenerate(id, params={...})` recompiles the stored spec with new values, rewrites the same model and returns before/after measures. Boolean engine: a built-in BSP-tree CSG (`vulcan/csg.py`, pure Python, T-junction repair, exact volumes) because no CAD kernel is installed; `manifold3d`, when present, is used automatically through trimesh. STEP export is not available (STL only); fillets of arbitrary edges, chamfers, shell, threads and sketches are reported as unsupported (use `rounded_box` for rounded edges).

The model detail includes **CAD parameters** for parts generated by Vulcan. Explicitly load the stored recipe, inspect units, ranges and derived expressions, and regenerate the same model using only the numerical values you changed. Editing a derived value replaces that expression with a fixed number; untouched expressions remain. Regeneration replaces the generated STL, check image and recipe: it is not an undo or file backup. The panel shows native before/after measures, objective verification, warnings and up to 20 history records; it refreshes the current viewer while retaining listing and notes drafts. External models without a native CAD recipe remain unchanged. This UI uses the existing CAD API and MCP tools, without a language model.

**Create variant** in that panel builds a separate library model from the stored recipe and the pending numerical edits. Give it a name, review its new ID, measured dimensions and preserved expressions, then open it with its result link. Creating does not navigate away, replace the original files or acknowledge the original parameter draft. Format-only changes such as `20.0` leave a derived expression intact. Listings and notes are not copied to the new piece. The existing `cad_from_text` API/MCP path compiles the explicit spec without a language model.

- **Search** (SQLite FTS5, diacritics-insensitive, prefix match on every word) over name, tags, notes, collection and listing text, with filters by root, format, tag, collection, album, watertight, has listing, duplicates only, status, size range, bbox range (largest extent) and triangle range; sort by name, date, file size, largest extent in mm, volume, triangles or relevance. Paginated.
- **Duplicates**: exact = same SHA-256 (every copy gets `dupe_of` = the first id); near = same triangle count, volume within 1 % and bounding-box extents within 1 % (an ASCII and a binary export of the same mesh, a rescaled copy, the same model saved in two formats), grouped by union-find and offered as suggestions.
- **Collections**: folder-derived groupings (read-only, from the scan) plus **albums** you build by hand (name + model ids).
- **UI (Spanish)**: Galería (thumbnail grid with dimensions and format chips, filter sidebar, search box, sort, "ver más" pagination), Modelo (three.js viewer with orbit controls, auto-fit and a build-plate grid; geometry table; tags/notes/collection editing; listing editor with "Copiar ficha"; exact and near duplicates), Colecciones (albums + folders), **Fichas** (folder listings: status table, draft/check/export buttons, title/description/20-tag editor), **Organizar** (paste the list, preview the plan as a table of group → target folder → items plus unmatched / ambiguous / conflicts, apply, undo, «Fichas de todas»), Carpetas (roots with progress, rescan, errors, watch), Estadísticas, Ajustes (values in use, maintenance actions, MCP notes). Works at phone width and installs as a PWA.

## Requirements

- Windows 10/11 (also runs on Linux/macOS), Python 3.11+ (3.13 fine), Node 22 only to build the client.
- CPU only. No OpenGL, no GPU: thumbnails are rasterised in numpy. The three.js viewer in the browser uses WebGL like any web page.
- Python's `sqlite3` must have FTS5 (the official Windows builds do). The app fails loudly at startup otherwise.

## Install and run (Windows)

```bat
git clone <this repo> vulcan-hoard
cd vulcan-hoard
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install
npm run build
venv\Scripts\python -m vulcan
```

Open http://127.0.0.1:5186, go to **Carpetas** and add a folder. The first scan of a big library takes a while (parsing plus one thumbnail per file, roughly 0.1–2 s per model depending on its size); **Carpetas** shows progress and every later scan only reads what changed.

- `python scripts/launch.py` starts the app on a free port and opens the browser.
- `python scripts/dev.py` runs uvicorn `--reload` + the Vite dev server (proxying `/api`).
- `python scripts/selftest.py <folder> [--no-thumbs] [--query "..."]` scans a folder into a temporary data dir and prints counts, timings, per-format totals, errors, near-duplicate groups and search hits.

## Configuration (environment)

| Variable | Default | Meaning |
| --- | --- | --- |
| `VULCAN_PORT` / `PORT` | `5186` | Preferred port; `PORT_STRICT=1` pins it, otherwise the first free port from there. |
| `VULCAN_DATA_DIR` | `<repo>/data` | Database (`vulcan-hoard.db`), `mcp-token`, `thumbs/`, `organize/` (stored plans and undo journals). |
| `VULCAN_ALLOWED_HOSTS` | | Extra host names accepted behind a tunnel (see below). |
| `VULCAN_THUMBS` | `1` | `0` skips thumbnail rendering (metrics only). |
| `VULCAN_THUMB_SIZE` | `512` | Thumbnail side in pixels (64–2048). |
| `VULCAN_MAX_FILE_MB` | `300` | Bigger files are listed as `skipped` and never loaded. |
| `VULCAN_SCAN_WORKERS` | `cpu_count − 2` (≥ 1) | Worker processes that hash, parse and render; `1` runs everything inline in the scan thread. |
| `VULCAN_SKIP_SMALL_BYTES` | `0` | Default minimum file size for new roots (0 = list everything). |
| `VULCAN_WATCH` | `1` | `0` disables folder watching. |
| `VULCAN_AUTOSTART` | `0` (studio) | `0` skips the rescan of every enabled root at startup. |

### Drafting folder listings with a local model

`folder_listing_draft` and the "Redactar" buttons in **Fichas** need a local model reachable through [Hoard Link](https://github.com/Luissalet/HoardLink) (vendored in `vulcan/hoard_link/`, see `VENDORED.txt`): Faustus's model registry, or a loopback llama.cpp/Ollama server, are found automatically. Nothing is configured by default. To pin an explicit server or model, or to point at a different Faustus, create `<DATA_DIR>/backend.json` (see `hoard_link/config.py` for the full schema) or set `HOARD_LLM_URL` / `HOARD_LLM_MODEL` / `HOARD_FAUSTUS_URL` / `HOARD_FAUSTUS_TOKEN`. Without any backend, `folder_listing_draft` never fails silently: it returns the assembled material (folder name, file names/sizes, template, examples) so the assistant can write the listing itself and save it with `folder_listing_set`.

### Access from your phone (behind a tunnel)

The server binds 127.0.0.1 and only answers requests whose `Host` is `localhost`, `127.0.0.1` or `[::1]`. To reach it from your phone through a tunnel that fronts the app (a private mesh network, a reverse proxy), list the extra host names in `VULCAN_ALLOWED_HOSTS`, comma-separated, exact names or `*.suffix`: `VULCAN_ALLOWED_HOSTS=my-pc.example,*.ts.net`. Letter case is ignored and the port is ignored unless you write one (`my-pc.example:8443` accepts only that port), and the `Origin` of API calls must resolve to one of those hosts too (any scheme or port). Cross-site *fetches* are still refused; opening the app from another page (a link, a bookmarklet, the share sheet) is a normal navigation and works.

## API

All JSON; errors are `{ "error": "..." }` (plus a machine-readable `code`, and `issues` for bad input). Agent-tool answers are capped at about 20 KB. The request guard, the error envelope, the single-page-app server (`index.html` is never cached), the MCP bridge, the agent routes, the database class, the folder policy, the atomic JSON writes, the ids and the search-query builder are the shared Hoard Link commons (`vulcan/hoard_link/`), not code of this app. A second `python -m vulcan` reports the running instance and exits without touching the token or the database; the token in `data/mcp-token` is created once and stays.

Adding a folder (`POST /api/roots`, `models_add_root`) accepts a pasted path with its quotes and refuses a drive root, the user profile folder, system folders, configuration or secret folders (`.ssh`, `AppData`, ...) and Vulcan's own data folder. Search words go through the shared builder: glue words (`the`, `de`) and one-letter words are ignored and plural endings are stemmed, so `the dragons` finds `dragon`. New organiser plan and apply ids are `plan_<ULID>` / `apply_<ULID>`; ids written by earlier versions keep working.

- `GET /api/health` → `{ service: "vulcan-hoard", version, dataDirConfigured }`
- `GET /api/status` → counts, worker queue and progress, watching, disk; `GET /api/stats` → totals, by format, by root, by collection, largest models, disk
- `GET/POST /api/roots` (POST: path, name, include, exclude, watch, `thumbnails` all|top-level|none, `skip_small_bytes`), `GET/PATCH/DELETE /api/roots/{id}`, `POST /api/roots/{id}/rescan`, `GET /api/roots/{id}/progress` (files done/total, `jobs_done`/`jobs_total`, `rate` files/s, `eta_s`, workers, errors)
- `GET /api/models?q&root&format&tag&collection&album&watertight&has_listing&dupes&status&size_min&size_max&bbox_min&bbox_max&triangles_min&triangles_max&sort&limit&offset` (paginated: `models`, `total`); `GET /api/search` is the same with relevance sort when `q` is given; `GET /api/models/facets`
- `GET /api/models/{id}` (everything + `listing`, `dupes`, `albums`), `PATCH /api/models/{id}` (name, tags, notes, collection)
- `GET /api/models/{id}/thumb` (WebP, 204 when none), `GET /api/models/{id}/file` (the original, `Range` supported, for the viewer and downloads)
- `GET/PUT/DELETE /api/models/{id}/listing` (PUT merges fields; `source` = manual | assistant)
- `GET /api/dupes?kind=exact|near`
- `GET /api/collections` (folders + albums), `POST /api/collections` (album: name, model_ids; idempotent by name), `GET/PATCH/DELETE /api/collections/{id}` (PATCH: name, add, remove)
- `GET /api/folder-listings?root_id&status` (missing listings first), `GET/PUT /api/folder-listings/{root_id}/{path}` (PUT: title, description, tags, status; writes the DB and `cults3d.json`), `POST /api/folder-listings/{root_id}/{path}/check` and `POST /api/folder-listings/check-all?root_id`, `POST /api/folder-listings/draft` (root_id, path pattern, limit, overwrite — background job), `GET /api/folder-listings/draft/progress`, `GET /api/folder-listings/export?root_id&format=csv|md|json&status`
- `POST /api/organize/plan` (root, reference, reference_format, target_template, match, number_width → plan with `id`), `GET /api/organize/plans[/{id}]`, `POST /api/organize/apply` (plan_id), `POST /api/organize/undo` (apply_id optional, remove_created_folders), `GET /api/organize/applies`, `POST /api/organize/sheets-batch` (root_id or path, scope, mode, refresh, overwrite, dry_run, limit, language)
- `POST /api/maintenance/rescan-all | rebuild-fts | refresh-dupes`
- `GET /api/agent/tools` (catalog + instructions), `POST /api/agent/call` (Bearer token from `data/mcp-token`)
- `GET /api/copilot/info`, `GET /api/copilot/panel.js`, `GET /api/copilot/launcher.js`, `POST /api/copilot/proxy` (shared local relay), `POST /api/copilot/context` (read-only native context; also `copilot_context_get` over MCP)

## MCP tools

`mcp_server.py` is a stdio bridge: it fetches the tool list from the running app and proxies every call to `POST /api/agent/call` with the token from `<DATA_DIR>/mcp-token`. It never opens the database. Env: `VULCAN_URL`, `VULCAN_TOKEN_FILE` (or `VULCAN_TOKEN`).

| Tool | What it does |
| --- | --- |
| `models_search` | Words + filters (format, tag, collection, root, watertight, has_listing, dupes_only, bbox range) → id, name, format, bbox, triangles, tags, has_listing, thumb_url; paginated. |
| `model_info` | Everything about one model by id or path, including listing, exact/near duplicates and albums. |
| `model_listing_get` | The listing of a model, or none. |
| `model_listing_set` | Write the listing (title, description, tags, category, price_hint, language); source = assistant; fields left out keep their value; idempotent (write). |
| `model_tag` | Add/remove tags, lower-cased and de-duplicated (write). |
| `model_note` | Replace or append the notes (write). |
| `models_stats` | Counts by format and root, collections, listings, duplicates, errors, scan queue with files/s and ETA per root; cached 5 s while a scan runs. |
| `models_dupes` | Exact or near duplicate groups. |
| `models_add_root` | Add a folder that must exist (options `thumbnails`, `skip_small_bytes`); idempotent by path; scanning starts in the background (write). |
| `models_rescan` | Queue a non-destructive rescan of one root or all (write). |
| `models_recent` | The n newest models by file modification date. |
| `folder_listings` | List folder listings (one per product folder), filtered by status, folders missing one first. |
| `folder_listing_get` | The listing of one folder (title, description, 20 tags, status, issues) by root+path or an absolute path. |
| `folder_listing_set` | Write a folder's listing; saves the DB and `cults3d.json` (write). |
| `folder_listing_check` | Validate one or every folder listing against the format rules and the root's template. |
| `folder_listing_draft` | Draft folder listings with the local model in the background; returns the assembled material when no backend is available (write). |
| `folder_listings_export` | Export a root's folder listings to CSV/Markdown/JSON in `data/exports/` (write). |
| `collection_plan` | Read-only, no model: match the immediate children of a folder to a pasted list (groups, members, numbers; CSV too) ignoring case, accents, separators and number prefixes; returns the target folder per group with its items, items already in place, unmatched, ambiguous and conflicts, plus a `plan_id`. |
| `collection_apply` | Apply a stored plan: create the group folders and move the items in; never deletes, never overwrites, re-checks the disk, writes an undo journal and queues a rescan (write). |
| `collection_undo` | Undo the last apply (or a given one): move every item back without overwriting and remove the folders the apply created if empty (write). |
| `sheets_batch` | Skeleton sheet drafts for every folder (or model) under a root in one call, no model needed; `refresh` / `overwrite` / `dry_run` / `mode=model` options; created / refreshed / skipped counts (write). |
| `model_import_file` | Index one STL/3MF/OBJ file now (`path`, optional `source_ref` = a `hoard://` reference): thumbnail, size, duplicate hash. A file inside a scanned root joins that root; any other file is listed through a small "Imported" root for its folder that lists only the files imported from it. Importing the same file again changes nothing. Emits `vulcan.model.added {model_id, ref, name, format, path, source_ref}` for a new model and links `source_ref` to `hoard://vulcan/model/<id>` in the hub (write). |
| `listings_export_catalog` | Read-only: every folder listing (status other than `none`) and every model listing as a catalogue entry `{ref, kind, title, description, tags, price, folder, model_ids, status, updated_at}`; `folder` limits it to one folder and below. Mercator imports this shape (`catalog_from_vulcan`). |
| `cad_spec_schema` | Read-only: the JSON schema, the node reference, an example spec, the boolean engine in use and what is unsupported. |
| `cad_from_text` | Build a parametric part from a `description` (a local model writes and the app validates the spec, one repair round) or from a `spec` you pass; optional `name`, `params` overrides, `expect` (bbox / volume), `save=false` for a dry run. Saves STL + spec + check render into the library and returns the id, measures and verification (write). |
| `cad_regenerate` | `id` of a generated part + `params={name: value}` (or a replacement `spec`): recompiles, rewrites the STL and render, reindexes the same model and returns the new measures and what changed (write). |

The instructions shipped with the tools tell the assistant to describe a model only from the geometry data and what the user says (never to invent features), to write listings in the user's voice when asked, to keep tags lower-case without duplicates, to report the model id back, and to use the `folder_listing_*` tools (never `model_listing_set`) whenever the user asks for "la ficha de la carpeta X" or "las fichas que faltan" — the marketplace convention where a product is a folder, not a single model. They also tell it never to organise a folder by moving files one by one with the shell: plan with `collection_plan`, show the summary, fix the list or the aliases, apply only after the user agrees and use `collection_undo` to revert; and that "fichas de todas" is one `sheets_batch` call (dry run first on a big root).

## Tests

```bat
venv\Scripts\python -m pytest -q
```

Covers geometry extraction on generated meshes (cube, sphere, open box, two-body file; binary and ASCII STL, OBJ, 3MF), thumbnail rendering (non-blank, deterministic, transparent, adaptive size, decimation, back-face culling, degenerate input, timing), incremental scan with dedupe and near-dupes, the process-pool path with two workers, rate/ETA, thumbnail policy and minimum size, FTS search and filters, listings/tags/notes/albums, folder listings (validator, import/write-back round trip, drafting with a fake model backend, export, tools, API), the collection organiser (reference parsing, matching with accents/numbering/ambiguity, plans and conflicts, apply without overwrite, undo, tools, API), the sheets batch (skeleton drafts, refresh/overwrite protection, dry run, models scope, model mode with a fake backend), the API via TestClient, agent auth, the folder watcher, the request guard, the parametric CAD generator (spec compilation with dimensions and exact volumes, parameters and expressions, validation errors, unsupported features, the text path with a fake model and its repair round, library insertion, regeneration, the check render, the MCP tools and the REST routes), and a subprocess end-to-end test that boots the app and talks to it through the MCP stdio bridge.

## Limits (v1)

- Formats: STL, 3MF, OBJ. STEP/AMF/PLY are not scanned.
- Volume of an open (non-watertight) mesh is the signed-volume estimate; treat it as approximate when `watertight` is false.
- Near-duplicate detection compares triangle count, volume and bounding box; it cannot tell a re-meshed copy from a different model with the same numbers, hence "suggested".
- `file_created_at` is the filesystem birth time where available (Windows, macOS) and the inode change time elsewhere.
- CAD generator: STL only (no STEP), no fillets of arbitrary edges, chamfers, shells, threads or sketches; spheres are polygon approximations (volume a little under the ideal one); the built-in CSG is pure Python, so parts with thousands of boolean triangles take seconds and a 60 s budget stops runaway specs.
- The thumbnail renderer keeps all triangles in memory; a 300 MB STL (~6 M triangles) needs a few hundred MB of RAM for a few seconds, per worker process.
- Two workers can render the thumbnail of the same duplicated file at the same time; the second result is discarded, nothing breaks.

## License

GPL-3.0 combined studio. Original component grants and notices are retained; see [NOTICE-STUDIO.md](NOTICE-STUDIO.md).


## Native connections

Former MCP and Hub identities can use the incorporated cold compatibility adapters without the retired repositories. See [native connections](docs/NATIVE-CONNECTIONS.md). Modules still require explicit activation.
