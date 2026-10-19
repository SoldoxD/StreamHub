# StreamHub for Kodi

Kodi 21+ video add-on. Live TV, movies and series with xstream-style multi-host picker across Vavoo, 67movies, ramoflix, filmpalast, kinox, HDfilme, StreamKiste, Movie2k, Einschalten and SerienStream.

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


## Playback update 2026.10.12

The editable add-on is now in `src/plugin.video.streamhub/`. `build.py` builds
that source into the installable ZIP and updates the repository manifests.
The separate Desktop source directory is no longer used for builds.

```sh
python -m unittest discover -s tests -v
python build.py
```

This update fixes these concrete playback issues:

- Kodi 21+ cache presets now use the supported `filecache.*` settings API.
  The old XML cache tags were ignored on those versions. Presets are
  64/128/256 MiB with adaptive reading; the default for new installs is 64 MiB.
  Kodi 19/20 retains XML support. Original settings are backed up and restored
  when the StreamHub preset is disabled, preserving later manual changes.
- All movie, episode, legacy Vavoo, live TV and sport playback paths share the
  same header and inputstream setup. Adaptive receives authentication headers
  for manifests and media segments, including headers embedded in resolver URLs.
- Optional inputstreams are checked before use; absent or disabled components
  fall back to Kodi's native player. Live playback no longer forces disk timeshift.
- Status checks read only headers. Deep host checks and stream probes have body
  limits, and both HTTP implementations close unused connections. Background
  resolver workers skip verification after a host has already been selected.
- Adaptive HLS/DASH honors the chosen resolution ceiling on Kodi 20+.
  Explicitly labelled direct sources honor the same ceiling; unknown-quality
  sources remain available. New installs default to 1080p and automatic HLS
  selection. Existing explicit playback preferences are retained.
- VOE prefers its adaptive playlist when available, with direct MP4 as fallback.

### Fire TV setup and verification

Install `plugin.video.streamhub/plugin.video.streamhub-2026.10.12.zip` in Kodi
and restart Kodi once to load the updated background service. In StreamHub's
settings, select **64 MB**, **maximum 1080p** (720p if needed), and **HLS: Auto**.
Enable Kodi's **InputStream Adaptive** add-on for adaptive playback. Existing
installations may retain their previously selected values; an update does not
reset those preferences. **Show buffer status** displays the actual Kodi cache
settings. InputStream add-ons have their own buffers; changing Kodi's file cache
does not resize the Adaptive buffer.

Offline regression tests cover cache migration/rollback, bounded downloads,
connection closure, quality limits, header propagation and playback setup.
There has been no live playback test on the user's Fire TV. Verify a film and an
episode on the previously affected hosts, plus a live stream if used, for at least
10 minutes each and test seeking/resuming. If sound continues while frames
stutter, note the Fire TV model, codec and resolution; this may be a decoding or
frame-rate issue rather than a download problem. Slow or unavailable hosts and
insufficient network throughput can still cause pauses; this update cannot
guarantee uninterrupted playback on every host.

Technical references: [Kodi cache settings](https://kodi.wiki/view/Settings/Services/Caching),
[InputStream Adaptive integration](https://github.com/xbmc/inputstream.adaptive/wiki/Integration),
[stream selection properties](https://github.com/xbmc/inputstream.adaptive/wiki/Stream-selection-types-properties),
[FFmpegdirect documentation](https://github.com/xbmc/inputstream.ffmpegdirect/blob/Omega/README.md).


## Provider and host update 2026.10.12.1

Install `plugin.video.streamhub/plugin.video.streamhub-2026.10.12.1.zip` and
restart Kodi once. This package includes the playback fixes above.

**New movie providers:** HDfilme (`hd-filme.lat`) and StreamKiste
(`streamkiste.bid`). Both are enabled by default under Sources and participate in
normal movie search and the combined host picker. They are deliberately
movie-only; no unsupported series/episode mapping is advertised. Search pages
and host-list extraction were checked online on 2026-10-12 without downloading
video content. Full playback on the user's Fire TV has not been tested.

**Doodstream:** Updated public domain aliases include `do7go.com`, `ds2video.com`,
`dsvplay.com`, `d-s.io` and `myvidplay.com`. Complete signed storage URLs are
preserved, legacy tokens retain their query/expiry format, and public download
pages can lead to their embed player. Playback keeps the full parent Referer and
CDN-scoped cookies. Dood playback URLs are not consumed by download probes;
pre-resolved links older than 60 seconds are refreshed when selected in the
host picker. These fixes address URL/authentication failures, not server bitrate
or throughput limits.

**Broader host support:** Additional player parsing covers StreamWish/StreamHG,
FileLions/VidHide, LuluStream and Uqload families. This handles public video/source
tags, JWPlayer configuration, literal media URLs, packed source data, and at most
two iframe hops. Page sizes, request counts and parser work are bounded.
Protection/login pages are not automatically solved. The host family list is a
verified domain mapping, not a guarantee that every mirror is currently online.

If the optional **ResolveURL** Kodi add-on is installed and enabled, its host
plugins are now tried before fetching the same generic embed again, with the
provider Referer preserved. Disabled host plugins and popup-dependent resolution
are not enabled automatically. **Sources -> Show host support** shows whether
ResolveURL is active. StreamHub still has its built-in resolvers when it is absent.

The Movie4k source cap stays at 16, but now keeps one entry per family before
adding a second entry from the same family, so more alternatives fit in the list.

Research references: [Dood public link formats](https://doodstream.com/api-docs),
[current Dood resolver behavior](https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/doodstream.py),
[ResolveURL integration](https://github.com/Gujal00/ResolveURL),
[StreamWish](https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/streamwish.py),
[FileLions/VidHide](https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/filelions.py),
[LuluStream](https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/lulustream.py),
[Uqload](https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/uqload.py).

## Playback hotfix 2026.10.12.2

This fixes two reproducible issues investigated after the report that
Spider-Man: Brand New Day (2026) showed hosts but would not start:

- The VOE mirror returned by StreamKiste uses `goofy-banana.com` and redirects
  to `megankiddifference.com`. These domains now reach the existing VOE decoder
  before the generic player parser, so its public example-video URL is not
  selected instead of the film's encoded source.
- Stream verification now recognizes short HLS/DASH manifests from their
  leading content signatures, including extensionless playlist URLs. It no
  longer rejects a valid master playlist merely because it is under 1 KiB,
  or mistakes a signed MP4 query parameter containing `.m3u8` for HLS.
  HTML/JSON error responses remain rejected and request limits are retained.

Live metadata checks found the correct StreamKiste film with four host links.
After the routing fix, its VOE mirror returned HTTP 200 with a 694-byte HLS master
playlist containing `#EXTM3U` and `#EXT-X-STREAM-INF`, using the resolver headers.
The Mixdrop resolver produced a video URL accepted with HTTP 200 and video/mp4
in a HEAD-only check. The offered Dood and Dropload pages presented protection
checks, so those mirrors could not be verified as playable. No film segments
were downloaded and full playback on the user's Fire TV is still untested.

Install the newest update, restart Kodi, and select the StreamKiste VOE mirror
(`goofy-banana.com`) or Mixdrop (`mxdrop.to`) when retesting this title.

Alias reference: [maintained VOE host resolver](https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/voesx.py).


## Provider and host update 2026.10.19

Install `plugin.video.streamhub/plugin.video.streamhub-2026.10.19.zip` or use
StreamHub's update check, then restart Kodi. The three new providers are enabled
by default and can be disabled individually under Sources.

- **SerienStream (`serienstream.to`)**: series search, observed seasons including
  season 0/specials, actual episode numbers/titles, and language-labelled host
  links. Public host redirects are resolved without requesting media. Series
  navigation now supports additional providers and falls back when another
  provider has no season metadata. Automatic-next and resume use listed episode
  numbers, so gaps do not create fictitious episodes. Disabled series providers
  are excluded from both lookup and the episode-host cache key.
- **Movie2k (`movie2k.cx`)**: film search and explicit player mirror links.
- **Einschalten (`einschalten.in`)**: film search and player links through its
  public catalog API. These two additions are movie-only.
- **Dropload**: dedicated support for public player pages on `dropload.io`,
  `dropload.tv`, `dropload.pro`, `dropload.co`, and `dr0pstream.com`; normalized
  embed paths, bounded HTML requests, literal/packed player sources and media
  request headers. Commented-out sources and unrelated advertising are ignored.
- **Dood / Dropload protection handling**: HTTP-200 captcha forms are detected
  as well as HTTP-403 protection pages. Dood aliases for the same file share a
  short-lived diagnostic without marking unrelated files on the host dead.
  The picker explains the protection response, and movie/Vavoo routes avoid
  passing the same blocked page through more resolvers. No challenge forms are
  submitted or solved by StreamHub.

Validation on 2026-10-19: 194 offline tests passed. Actual SerienStream methods
returned Breaking Bad, seasons 0–5, seven episodes in season 1 and German/English
VOE links for episode 1. Movie2k and Einschalten returned an Inception result and
host links. Independent Movie2k/SerienStream resolution reached HTTP-200 HLS
master playlists with the returned headers (694/695 bytes); no film segments
were downloaded. Full playback on the user's Fire TV remains untested.

**Current limitation:** the previously reported Spider-Man Dood link still
returns a Cloudflare HTTP-403 page, and its Dropload link returns a Turnstile
form with HTTP 200. Both were rechecked using the updated resolver and are
reported as protected. This update does not make those two gated links playable;
public Dropload player parsing is covered by offline fixtures. Einschalten's
observed `vide0.net` link was verified only as a catalog/host link, not as playable
media. Browser-protected KinoGer/Moflix pages were not added as new providers.

Research sources: [the supplied FMHY German list](https://fmhy.net/non-english#german-deutsch),
[SerienStream](https://serienstream.to),
[Movie2k](https://movie2k.cx), [Einschalten](https://einschalten.in),
[maintained Dood resolver](https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/doodstream.py),
[maintained Dropload resolver](https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/dropload.py).
