"""Shared stream setup for Kodi's native player and optional inputstreams."""

import json
from urllib.parse import parse_qsl, urlencode, urlsplit

import xbmc
from streamhub.utils import getSetting


_ADAPTIVE = "inputstream.adaptive"
_FFMPEG = "inputstream.ffmpegdirect"
_FORMATS = {
    ".m3u8": ("hls", "application/vnd.apple.mpegurl"),
    ".mpd": ("mpd", "application/dash+xml"),
    ".mp4": ("", "video/mp4"),
    ".mkv": ("", "video/x-matroska"),
    ".webm": ("", "video/webm"),
    ".ts": ("", "video/mp2t"),
}


def max_height():
    """Use the same quality ceiling for manifests and direct source selection."""
    return {"0": 2160, "1": 1080, "2": 720}.get(getSetting("stream_quali"), 1080)


def split_stream_url(url, headers=None):
    """Merge headers once, preserving resolver headers over provider defaults.

    Header strings use Kodi's URL-encoded key/value syntax. Dictionaries contain
    raw values. Names are compared case-insensitively; values are never decoded
    twice. The returned URL never contains Kodi's header pipe suffix.
    """
    clean_url, _, inline = url.strip().partition("|")
    merged = {}
    for source in (headers, inline):
        if not source:
            continue
        pairs = source.items() if hasattr(source, "items") else parse_qsl(
            source.lstrip("|"), keep_blank_values=True)
        for name, value in pairs:
            name = str(name).strip()
            if name and value is not None:
                merged[name.lower()] = (name, str(value))
    return clean_url, urlencode(list(merged.values()))


def _stream_format(url):
    # Headers, host names and signed query values do not describe the format.
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return "", ""
    for suffix, result in _FORMATS.items():
        if path.endswith(suffix):
            return result
    # Some resolvers return /hls/<token> without a playlist extension.
    if "hls" in path.split("/") and "." not in path.rsplit("/", 1)[-1]:
        return _FORMATS[".m3u8"]
    return "", ""


def _addon_enabled(addon_id):
    try:
        return bool(xbmc.getCondVisibility("System.AddonIsEnabled(%s)" % addon_id))
    except Exception:
        try:
            reply = json.loads(xbmc.executeJSONRPC(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "Addons.GetAddonDetails",
                "params": {"addonid": addon_id, "properties": ["enabled"]},
            })))
            return reply.get("result", {}).get("addon", {}).get("enabled") is True
        except Exception:
            return False


def configure_playback(item, url, headers=None, live=False):
    """Configure an item and return the URL to pass to the playback helper.

    Optional inputstreams are used only when enabled. Native/FFmpeg playback
    retains Kodi's URL headers; Adaptive receives headers through its documented
    manifest and segment properties instead.
    """
    clean_url, encoded_headers = split_stream_url(url, headers)
    manifest, mime = _stream_format(clean_url)
    engine = ""
    if manifest:
        engine = (_FFMPEG if live and manifest == "hls"
                  and getSetting("hlsinputstream") == "0" else _ADAPTIVE)
    elif live:
        engine = _FFMPEG
    if engine and not _addon_enabled(engine):
        engine = ""

    if mime:
        item.setMimeType(mime)
        item.setContentLookup(False)
    if engine:
        item.setProperty("inputstream", engine)
    if engine == _ADAPTIVE:
        # manifest_type is required only on Kodi <=20; Kodi 21+ detects it.
        try:
            major = int(xbmc.getInfoLabel("System.BuildVersion").split(".", 1)[0])
        except (AttributeError, ValueError):
            major = 21
        if major < 21:
            item.setProperty(_ADAPTIVE + ".manifest_type", manifest)
        item.setProperty(_ADAPTIVE + ".stream_selection_type", "adaptive")
        item.setProperty(_ADAPTIVE + ".chooser_resolution_max",
                         {2160: "4K", 1080: "1080p", 720: "720p"}[max_height()])
        if encoded_headers:
            item.setProperty(_ADAPTIVE + ".manifest_headers", encoded_headers)
            item.setProperty(_ADAPTIVE + ".stream_headers", encoded_headers)
        final_url = clean_url
    else:
        if engine == _FFMPEG:
            item.setProperty(_FFMPEG + ".is_realtime_stream", "true")
            item.setProperty(_FFMPEG + ".open_mode", "ffmpeg")
            if manifest:
                item.setProperty(_FFMPEG + ".manifest_type", manifest)
            # Omitting stream_mode avoids the disk-backed timeshift buffer.
        final_url = clean_url + ("|" + encoded_headers if encoded_headers else "")
    item.setPath(final_url)
    return final_url
