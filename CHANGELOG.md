# Changelog

Current release notes are kept here because the Discord `!changelog` command reads this file directly. Older history was archived during the documentation de-bloat pass on 2026-05-31.

Archive: `docs/archive/CHANGELOG-legacy.md`

## Football Tracker Bot v3.4.0

Author: SpikePhD

### Documentation

- Rewrote `README.md` with current project scope, commands, configuration model, runtime state, and development checks.
- Expanded `OPERATIONS.md` with provider diagnostics, enrichment markers, and API-Football quota tuning guidance.
- Expanded `DEVELOPER.md` with the current module architecture and provider/enrichment rules.

### API-Football Enrichment Controls

- Added configurable daily enrichment call budget.
- Added negative ESPN-to-API-Football mapping caching.
- Added cooldown handling for incomplete API-Football event responses.
- Made enrichment retry delays configurable.
- Reduced repeated best-known-event reuse logging for identical fixture states.

### Validation

- Added regression tests for enrichment budget, negative mapping cache, incomplete event cooldown, and repeated-log suppression.
