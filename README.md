# StreamHub for Kodi

Kodi 21+ video add-on. Live TV, movies and series with xstream-style multi-host picker across Vavoo, 67movies, ramoflix, filmpalast and kinox.

## Install

1. Kodi → Settings → System → Add-ons → **Unknown sources** → ON
2. Settings → **File manager** → Add source → paste:
   ```
   https://soldoxd.github.io/StreamHub/
   ```
   Name it `streamhub`.
3. Settings → Add-ons → **Install from zip file** → `streamhub` → `plugin.video.streamhub` → the newest `plugin.video.streamhub-*.zip`

The addon appears under **Video add-ons → StreamHub**. From then on it **auto-updates** — see below.

## Auto-update

Once installed, StreamHub runs a small background service that checks this page once a day and pulls new versions automatically.

- **Settings → UPDATES → Auto-Update** (on by default) — installs without asking
- **Settings → UPDATES → Bei jedem Kodi-Start auf Updates pruefen** (on by default)
- **Settings → UPDATES → Jetzt nach Update suchen** — manual check anytime

After an update is installed, restart Kodi for the new code to take effect.

## First-time setup

Open the addon once, back out to the tile, then **Configure** (right-click → Settings):

- **VAVOO** — on by default, the most reliable source.
- **DLHD (DaddyLive)** — **off by default**. Turn this on to get:
  - `Live → Live - Soccer (DLHD)` (live soccer schedule + streams)
  - DLHD channels under `Live → Live - Gruppen`
- **STALKER** — off by default. Only for users with their own IPTV portal.
- **MOVIES (Multi-Quelle)** — toggle 67movies / ramoflix / filmpalast / kinox individually. Vavoo and filmpalast are the most reliable; the rest are JS-protected hosters that don't always extract server-side.

## What's inside

- **Live TV**: VAVOO + DaddyLive (DLHD with soccer schedule) + optional Stalker
- **Movies / Series**: xstream-style multi-host dialog. Search queries every enabled provider in parallel, dedupes by title, then on click gathers all working hosts across all providers.
- **Self-contained**: vendored urllib HTTP shim — works on a fresh Kodi without needing `script.module.requests`, `resolveurl`, `dateutil`, or `infotagger`. Uses them if available.

## Files

- [plugin.video.streamhub/](plugin.video.streamhub/) — the addon zips
- [addons.xml](addons.xml) / [addons.xml.md5](addons.xml.md5) — manifest the auto-updater reads
