# Vulcan's Hoard

Your personal library of 3D-printable models, indexed on your own PC. Point it at the folders where you keep your STL, 3MF and OBJ files and it measures every model (size in mm, triangles, volume, surface, watertight or not, number of bodies), renders a thumbnail on the CPU, finds duplicates and near-duplicates, and keeps tags, notes and a marketplace listing (title, description, tags, category) per model. An assistant reaches the same library through MCP, so it can find "that dragon bust from last spring", quote its real dimensions and draft the listing text for you — without inventing anything the geometry does not say.

Everything stays on the machine: SQLite with FTS5, thumbnails as WebP files, no accounts, no network. The app itself never calls a language model.

Part of the Hoard family (see `faustus-plugin.json`).

## What it does

- **Roots** = folders you choose, with include/exclude globs (default `**/*.stl, **/*.3mf, **/*.obj`; `.git`, `node_modules`, hidden folders excluded), enable/disable and optional folder watching (rescan on change, debounced).
- **Scanning** is incremental: size + mtime first, then SHA-256; only changed files are parsed; deleted files are purged; a touched-but-identical file is not re-read. Hashing, parsing and thumbnail rendering run in a `ProcessPoolExecutor` (`VULCAN_SCAN_WORKERS`, default `cpu_count − 2`, spawn context so Windows and Linux behave the same); the scan thread only walks the folder, decides what changed and writes results, so SQLite keeps a single writer. Live progress per root: files done/total, parsed files per second, ETA, current file, per-file errors. Files over `VULCAN_MAX_FILE_MB` (300) are listed as `skipped` with a note instead of being loaded into memory.
- **Per-root options** for big generated trees (a slicer or a photogrammetry job that exports thousands of layer meshes): `thumbnails` = `all` | `top-level` (only files in the root folder and its immediate subfolders get a thumbnail) | `none`; `skip_small_bytes` (files under that size are not listed at all; default `VULCAN_SKIP_SMALL_BYTES` = 0); and the exclude globs, which are the right tool for skipping a generated folder by name — e.g. `**/export_job_*/**` or `**/normal_registered/**` — one pattern per folder, `**` on both sides.
- **Geometry** with `trimesh`: triangles, unique vertices, bounding box in mm, volume (cm³), surface (cm²), watertight, body count (connected components), and a `units_guess` flag when the size makes millimetres unlikely (`inches` under 5 mm, `meters` under 1 mm, `large` over 1.5 m). 3MF units are converted to mm; a 3MF with several objects is measured as one mesh with N bodies.
- **Thumbnails** in pure Python (numpy + Pillow, no OpenGL, so they work on any Windows box without a GPU driver): orthographic camera from the front-right at 30° elevation with Z up (print orientation), flat shading from one fixed light, per-pixel depth resolution over triangles sorted far-to-near, rasterised in numpy batches sized by the triangles' exact screen footprint. Meshes under 5 000 triangles come out at 384 px with 1.5× supersampling, bigger ones at 512 px with 2×; meshes over 300 000 triangles are decimated for the thumbnail only (quadric decimation through `fast_simplification` when installed, else a deterministic face subsample); closed, consistently wound meshes skip their back faces. WebP with transparent background, stored as `<DATA_DIR>/thumbs/<sha256>.webp` (identical files share one). A 300-triangle layer mesh renders in ~60 ms, a 300k-triangle scan in ~0.9 s; a deleted thumbs folder is regenerated on the next rescan without re-measuring.
- **Names**: the file stem, prettified (`dragon_bust-v2` → `Dragon bust v2`), editable. **Collection** = the immediate folder name by default, editable. **Tags** (lower-case, de-duplicated) and **notes** per model survive rescans.
- **Listings**: title, description (markdown), tags, category, price hint, language, with `listing_source` (`manual` | `assistant`) and `listing_updated_at`. The UI has a "Copiar ficha" button that produces the plain text to paste into a marketplace form (title, description, dimensions, category, tags, price hint).
- **Search** (SQLite FTS5, diacritics-insensitive, prefix match on every word) over name, tags, notes, collection and listing text, with filters by root, format, tag, collection, album, watertight, has listing, duplicates only, status, size range, bbox range (largest extent) and triangle range; sort by name, date, size, triangles or relevance. Paginated.
- **Duplicates**: exact = same SHA-256 (every copy gets `dupe_of` = the first id); near = same triangle count, volume within 1 % and bounding-box extents within 1 % (an ASCII and a binary export of the same mesh, a rescaled copy, the same model saved in two formats), grouped by union-find and offered as suggestions.
- **Collections**: folder-derived groupings (read-only, from the scan) plus **albums** you build by hand (name + model ids).
- **UI (Spanish)**: Galería (thumbnail grid with dimensions and format chips, filter sidebar, search box, sort, "ver más" pagination), Modelo (three.js viewer with orbit controls, auto-fit and a build-plate grid; geometry table; tags/notes/collection editing; listing editor with "Copiar ficha"; exact and near duplicates), Colecciones (albums + folders), Carpetas (roots with progress, rescan, errors, watch), Estadísticas, Ajustes (values in use, maintenance actions, MCP notes). Works at phone width and installs as a PWA.

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
| `VULCAN_DATA_DIR` | `<repo>/data` | Database (`vulcan-hoard.db`), `mcp-token`, `thumbs/`. |
| `VULCAN_ALLOWED_HOSTS` | | Extra host names accepted behind a tunnel (see below). |
| `VULCAN_THUMBS` | `1` | `0` skips thumbnail rendering (metrics only). |
| `VULCAN_THUMB_SIZE` | `512` | Thumbnail side in pixels (64–2048). |
| `VULCAN_MAX_FILE_MB` | `300` | Bigger files are listed as `skipped` and never loaded. |
| `VULCAN_SCAN_WORKERS` | `cpu_count − 2` (≥ 1) | Worker processes that hash, parse and render; `1` runs everything inline in the scan thread. |
| `VULCAN_SKIP_SMALL_BYTES` | `0` | Default minimum file size for new roots (0 = list everything). |
| `VULCAN_WATCH` | `1` | `0` disables folder watching. |
| `VULCAN_AUTOSTART` | `1` | `0` skips the rescan of every enabled root at startup. |

### Access from your phone (behind a tunnel)

The server binds 127.0.0.1 and only answers requests whose `Host` is `localhost`, `127.0.0.1` or `[::1]`. To reach it from your phone through a tunnel that fronts the app (a private mesh network, a reverse proxy), list the extra host names in `VULCAN_ALLOWED_HOSTS`, comma-separated, exact names or `*.suffix`: `VULCAN_ALLOWED_HOSTS=my-pc.example,*.ts.net`. Port and letter case are ignored, and the `Origin` of API calls must resolve to one of those hosts too (any scheme or port). Cross-site *fetches* are still refused; opening the app from another page (a link, a bookmarklet, the share sheet) is a normal navigation and works.

## API

All JSON; errors are `{ "error": "..." }`.

- `GET /api/health` → `{ service: "vulcan-hoard", version, dataDirConfigured }`
- `GET /api/status` → counts, worker queue and progress, watching, disk; `GET /api/stats` → totals, by format, by root, by collection, largest models, disk
- `GET/POST /api/roots` (POST: path, name, include, exclude, watch, `thumbnails` all|top-level|none, `skip_small_bytes`), `GET/PATCH/DELETE /api/roots/{id}`, `POST /api/roots/{id}/rescan`, `GET /api/roots/{id}/progress` (files done/total, `jobs_done`/`jobs_total`, `rate` files/s, `eta_s`, workers, errors)
- `GET /api/models?q&root&format&tag&collection&album&watertight&has_listing&dupes&status&size_min&size_max&bbox_min&bbox_max&triangles_min&triangles_max&sort&limit&offset` (paginated: `models`, `total`); `GET /api/search` is the same with relevance sort when `q` is given; `GET /api/models/facets`
- `GET /api/models/{id}` (everything + `listing`, `dupes`, `albums`), `PATCH /api/models/{id}` (name, tags, notes, collection)
- `GET /api/models/{id}/thumb` (WebP, 204 when none), `GET /api/models/{id}/file` (the original, `Range` supported, for the viewer and downloads)
- `GET/PUT/DELETE /api/models/{id}/listing` (PUT merges fields; `source` = manual | assistant)
- `GET /api/dupes?kind=exact|near`
- `GET /api/collections` (folders + albums), `POST /api/collections` (album: name, model_ids; idempotent by name), `GET/PATCH/DELETE /api/collections/{id}` (PATCH: name, add, remove)
- `POST /api/maintenance/rescan-all | rebuild-fts | refresh-dupes`
- `GET /api/agent/tools` (catalog + instructions), `POST /api/agent/call` (Bearer token from `data/mcp-token`)

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

The instructions shipped with the tools tell the assistant to describe a model only from the geometry data and what the user says (never to invent features), to write listings in the user's voice when asked, to keep tags lower-case without duplicates, and to report the model id back.

## Tests

```bat
venv\Scripts\python -m pytest -q
```

Covers geometry extraction on generated meshes (cube, sphere, open box, two-body file; binary and ASCII STL, OBJ, 3MF), thumbnail rendering (non-blank, deterministic, transparent, adaptive size, decimation, back-face culling, degenerate input, timing), incremental scan with dedupe and near-dupes, the process-pool path with two workers, rate/ETA, thumbnail policy and minimum size, FTS search and filters, listings/tags/notes/albums, the API via TestClient, agent auth, the folder watcher, the request guard, and a subprocess end-to-end test that boots the app and talks to it through the MCP stdio bridge.

## Limits (v1)

- Formats: STL, 3MF, OBJ. STEP/AMF/PLY are not scanned.
- Volume of an open (non-watertight) mesh is the signed-volume estimate; treat it as approximate when `watertight` is false.
- Near-duplicate detection compares triangle count, volume and bounding box; it cannot tell a re-meshed copy from a different model with the same numbers, hence "suggested".
- `file_created_at` is the filesystem birth time where available (Windows, macOS) and the inode change time elsewhere.
- The thumbnail renderer keeps all triangles in memory; a 300 MB STL (~6 M triangles) needs a few hundred MB of RAM for a few seconds, per worker process.
- Two workers can render the thumbnail of the same duplicated file at the same time; the second result is discarded, nothing breaks.

## License

MIT — Luissalet.
