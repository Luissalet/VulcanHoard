# Studio integration status

Work in progress for Fisiones task928. This source has not been integrated into the installed app, published or approved for a data migration.

The studio owns the incorporated public sources of Gepetto, Daedalus, Plato, Heron and Gulliver, with per-file provenance in `vulcan/studio_native/PROVENANCE.json`. Existing Vulcan library code remains intact. The shell starts with all six modules and automation OFF; its API/MCP catalogue is static. Shared project records retain native IDs, revisions and artifact hashes without copying originals.

Verified so far: five synthetic shell tests; temporary native activation/read/shutdown of library, Heron, Gulliver, Daedalus and Gepetto; Gepetto ON/OFF/ON after closing its queue connections. Post-reboot integrity scan:871 text files, no NUL corruption. These checks do not certify every native workflow or installed dependencies.

Open implementation work before review: scoped native URLs and media, complete Plato export callbacks and lifecycle, historical queue recovery only when explicitly selected, joined cancellation/startup/shutdown without blocking the shared loop, all Gepetto inference through the configured global Sparks service, shared object/result operations in the UI, dependency/build/package metadata, native workflow tests and desktop/mobile browser QA. Source compatibility aliases, real data relocation and old-repository retirement belong to task934 after review.

The separate software-studio merger must continue to describe Theseus as incomplete and paused by Luis for usage quota. This 3D integration does not resume that development.
