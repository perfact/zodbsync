# Compression only collapses the fallback layer

Layer compression (the `fs_write` pass that removes an object from a layer when a lower-priority layer holds identical content) is scoped to the fallback layer: it removes a copy only when that copy currently lives in the fallback layer, and never removes a copy that lives in a named layer.

We accept losing automatic de-duplication across named layers in exchange for stable, deliberate layer placement. An object moved into a named layer (e.g. to prepare an override before its content diverges from a lower layer) must survive `record`/`watch`/`playback` even while it is still byte-identical to the layer below it; the previous behaviour silently collapsed it back down. The fallback layer keeps its original behaviour — ad-hoc recordings there still collapse into an identical base layer — because it is the scratch layer where duplication is noise, not intent.

This also removed the need for the `zodbsync copy` command (whose whole purpose was to defeat compression by forcing a content difference); `copy` was dropped before merge. The realistic override workflow is captured as future work (`zodbsync stash-and-drop`) in the PRD.
