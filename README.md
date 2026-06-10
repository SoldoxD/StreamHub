# StreamHub for Kodi

Kodi 21+ video add-on. Live TV, movies and series with xstream-style multi-host picker across Vavoo, 67movies, ramoflix, filmpalast and kinox.

## Install

1. Kodi → Settings → System → Add-ons → **Unknown sources** → ON
2. Settings → **File manager** → Add source → paste:
   ```
   https://soldoxd.github.io/StreamHub/
   ```
   Name it `streamhub`.
3. Settings → Add-ons → **Install from zip file** → `streamhub` → `plugin.video.streamhub` → `plugin.video.streamhub-2026.06.09.zip`

That's it — the addon appears under **Video add-ons → StreamHub**.

## First-time setup

Open the addon once, back out to the tile, then **Configure** (right-click → Settings):

- **VAVOO** — on by default, the most reliable source.
- **DLHD (DaddyLive)** — **off by default**. Turn this on to get:
  - `Live → Live - Soccer (DLHD)` (live soccer schedule + streams)
  - DLHD channels under `Live → Live - Gruppen`
- **STALKER** — off by default. Only for users with their own IPTV portal subscription.
- **MOVIES (Multi-Quelle)** — toggle 67movies / ramoflix / filmpalast / kinox individually. Vavoo and filmpalast are the most reliable; the rest are JS-protected hosters that don't always extract server-side.

## What's inside

- **Live TV**: VAVOO + DaddyLive (DLHD with soccer schedule view) + optional Stalker
- **Movies / Series**: xstream-style multi-host dialog. Search queries every enabled provider in parallel, dedupes by title, then on click gathers all working hosts across all providers.
- **Self-contained**: vendored urllib HTTP shim — works on a fresh Kodi without needing `script.module.requests`, `resolveurl`, `dateutil`, or `infotagger`. Uses them if available, falls back gracefully if not.

## Files

- [plugin.video.streamhub/](plugin.video.streamhub/) — the addon
- [addons.xml](addons.xml) / [addons.xml.md5](addons.xml.md5) — manifest

## Updating

When a new addon version is published here, re-run Install steps 2 & 3 with the new zip filename. Kodi overwrites the existing install while keeping your settings.
