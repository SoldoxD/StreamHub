# StreamHub Kodi Repository

A Kodi 21+ video add-on that aggregates multiple sources behind one xstream-style
host picker.

## Install in Kodi

1. **Allow unknown sources** if you haven't already
   - Kodi → Settings (gear) → System → Add-ons → **Unknown sources** → ON

2. **Add this repository as a file source**
   - Settings → File manager → Add source
   - Path: `https://soldoxd.github.io/StreamHub/`
   - Name: `streamhub`

3. **Install from zip file**
   - Settings → Add-ons → Install from zip file → `streamhub` →
     `repository.streamhub/repository.streamhub-1.0.0.zip`

4. **Install StreamHub**
   - Settings → Add-ons → Install from repository → **StreamHub Repository**
     → Video add-ons → **StreamHub** → Install

Updates from here on are pulled automatically.

## What's inside

- **Live TV**: Vavoo + DLHD (with soccer schedule view) + optional Stalker
- **Movies/Series** (xstream-style multi-host picker): Vavoo + 67movies + ramoflix + filmpalast + kinox
- **Self-contained**: vendored urllib HTTP client; works on a fresh Kodi without
  needing `script.module.requests`, `resolveurl`, `dateutil`, or `infotagger`
  (uses them if present, but doesn't require them).

## Direct zip install

If you just want the plugin without the repository:
[plugin.video.streamhub-2026.06.09.zip](plugin.video.streamhub/plugin.video.streamhub-2026.06.09.zip)
