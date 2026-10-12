"""Small public-player fallback; no JavaScript execution or challenge handling.

Domain facts checked against maintained ResolveURL plugins on 2026-10-12:
https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/streamwish.py
https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/filelions.py
https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/lulustream.py
https://github.com/Gujal00/ResolveURL/blob/master/script.module.resolveurl/lib/resolveurl/plugins/uqload.py
The registry identifies families, not verified uptime. Domain migration and
service-specific protocols are delegated to the installed ResolveURL add-on.
"""

import base64
from html import unescape
from html.parser import HTMLParser
from itertools import islice
import re
from urllib.parse import urldefrag, urlencode, urljoin, urlsplit


HOST_FAMILIES = {
    "StreamWish": (
        "streamwish.com", "streamwish.to", "streamwish.fun", "embedwish.com",
        "cdnwish.com", "sfastwish.com", "hlswish.com", "dhcplay.com", "hglink.to",
        "hgcloud.to", "gradehgplus.com", "hgbazooka.com", "swishsrv.com",
        "playerwish.com", "wishonly.site", "strwish.com", "davioad.com",
    ),
    "FileLions / VidHide": (
        "filelions.com", "filelions.to", "filelions.site", "vidhide.com",
        "vidhidehub.com", "vidhideplus.com", "dhtpre.com", "smoothpre.com",
        "callistanise.com", "morencius.com", "minochinos.com", "earnvids.xyz",
        "moflix-stream.click", "kinoger.be", "vidhidefast.com", "peytonepre.com",
    ),
    "LuluStream": (
        "lulustream.com", "luluvdo.com", "lulu.st", "luluvid.com",
        "732eg54de642sa.sbs", "cdn1.site", "streamhihi.com", "luluvdoo.com",
        "d00ds.site", "lulust.com", "luluvido.com",
    ),
    "Uqload": (
        "uqload.com", "uqload.co", "uqload.io", "uqload.to", "uqload.ws",
        "uqload.net", "uqload.cx", "uqload.bz", "uqload.org", "uqload.is",
        "uqload.vc",
    ),
}

MAX_PAGE_BYTES = 1024 * 1024
MAX_PAGES = 3
MAX_IFRAME_DEPTH = 2
MAX_BASE64_CHARS = 65536
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_MEDIA_EXTENSIONS = (".m3u8", ".mpd", ".mp4", ".m4v", ".webm", ".mkv")
_MEDIA_TYPES = ("hls", "mp4", "video/mp4", "video/webm", "application/dash+xml",
                "application/vnd.apple.mpegurl", "application/x-mpegurl")
_PROTECTION = ("cf-browser-verification", "challenge-platform", "cf_chl_opt",
               "enable javascript and cookies to continue", "verify you are human")
_STRING = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.S | re.X)
_COMMENTS = re.compile("(%s)|/\\*.*?\\*/|//[^\\r\\n]*|<!--.*?-->" % _STRING.pattern, re.S | re.X)
_JS_ESCAPE = re.compile(r'''\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[\\/'"])''')
_KEY = re.compile(r'''\s*(?:["']([\w$]+)["']|([\w$]+))\s*:\s*(.*)''', re.S)
_SOURCES = re.compile(r'''(?:\b|["'])sources["']?\s*:\s*\[''')
_SETUP = re.compile(r'''\bjwplayer\s*\([^;\n]*?\)\s*\.\s*setup\s*\(\s*\{''')
_LINKS = re.compile(r'''\b(?:var|let|const)\s+links\s*=\s*\{''')
_ASSIGNMENT = re.compile(
    r'''\b(?:var|let|const)\s+(?:url|file|hls|mp4|source|src|video_url|videoUrl)\s*=\s*''')
_ATOB = re.compile(r'''\batob\s*\(\s*["']([A-Za-z0-9+/=_-]{8,65536})["']\s*\)''')
_PACKED_COUNTS = re.compile(r"}\s*\(\s*'(?:\\.|[^'\\])*'\s*,\s*(\d+)\s*,\s*(\d+)\s*,")


def family_for(url):
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    for family, domains in HOST_FAMILIES.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return family
    return None


def _url(value, base):
    value = unescape(value or "").strip()
    if not value or any(ord(c) < 32 for c in value):
        return None
    try:
        resolved = urldefrag(urljoin(base, value))[0]
        parsed = urlsplit(resolved)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        return resolved
    except ValueError:
        return None


def _literal(value):
    value = value.strip()
    if not _STRING.fullmatch(value):
        return None
    def decode(match):
        token = match.group(1)
        return chr(int(token[1:], 16)) if token[0] in "ux" else token
    return _JS_ESCAPE.sub(decode, value[1:-1])


def _structural(text):
    """Yield punctuation outside quoted strings and comments in one pass."""
    i = 0
    while i < len(text):
        if text[i] in "\"'":
            match = _STRING.match(text, i)
            if not match:
                return
            i = match.end()
        elif text.startswith("//", i):
            end = text.find("\n", i + 2)
            i = len(text) if end < 0 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
        else:
            yield i, text[i]
            i += 1


def _enclosed(text):
    depth = 0
    for index, char in _structural(text):
        if char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
            if depth == 0:
                return text[:index + 1]
    return ""


def _segments(text):
    depth, start = 0, 0
    for index, char in _structural(text):
        if char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
        elif char == "," and depth == 0:
            yield text[start:index]
            start = index + 1
    yield text[start:]


def _fields(value):
    result = {}
    for part in _segments(value[1:-1]):
        match = _KEY.fullmatch(part.strip())
        if match:
            result[match.group(1) or match.group(2)] = match.group(3).strip()
    return result


def _sources(value, depth=0):
    if depth > 8:
        return
    if value.startswith("["):
        for part in _segments(value[1:-1]):
            yield from _sources(part.strip(), depth + 1)
    elif value.startswith("{"):
        fields = _fields(value)
        hint = _literal(fields.get("type", "")) or ""
        for key in ("file", "src"):
            source = _literal(fields.get(key, ""))
            if source:
                yield source, hint
        if "sources" in fields:
            yield from _sources(fields["sources"], depth + 1)
    else:
        source = _literal(value)
        if source:
            yield source, ""


def _script_sources(text, allow_base64=True):
    text = _COMMENTS.sub(lambda match: match.group(1) or " ", text)
    for pattern in (_SOURCES, _SETUP):
        for match in islice(pattern.finditer(text), 32):
            value = _enclosed(text[match.end() - 1:])
            if value:
                yield from _sources(value)
    for match in islice(_LINKS.finditer(text), 32):
        fields = _fields(_enclosed(text[match.end() - 1:]) or "{}")
        for key in ("hls4", "hls3", "hls2", "hls"):
            source = _literal(fields.get(key, ""))
            if source:
                yield source, "hls"
    for match in islice(_ASSIGNMENT.finditer(text), 32):
        literal = _STRING.match(text, match.end())
        if literal:
            # Concatenation/function calls are intentionally not evaluated.
            tail = text[literal.end():].lstrip()
            if not tail or tail[0] in ";,<\r\n":
                yield _literal(literal.group()), ""
    if allow_base64:
        for match in islice(_ATOB.finditer(text), 8):
            try:
                raw = match.group(1)
                decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_",
                                           validate=True).decode("utf-8")
            except (ValueError, UnicodeError):
                continue
            if decoded.strip().startswith(("https://", "http://", "//")):
                yield decoded.strip(), ""
            else:
                yield from _script_sources(decoded, allow_base64=False)
                if decoded.strip().startswith(("[", "{")):
                    yield from _sources(decoded.strip())


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources = []
        self.frames = []
        self.base = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base" and self.base is None:
            self.base = attrs.get("href")
        elif tag in ("source", "video"):
            source = attrs.get("src")
            if source:
                self.sources.append((source, attrs.get("type") or ""))
        elif tag == "iframe":
            self.frames.extend(value for value in (attrs.get("src"), attrs.get("data-src")) if value)


def _headers(referer):
    parsed = urlsplit(referer)
    return {"User-Agent": UA, "Referer": referer,
            "Origin": "%s://%s" % (parsed.scheme, parsed.netloc)}


def _unpacked(text, unpacker):
    counts = [match.groups() for match in islice(_PACKED_COUNTS.finditer(text), 5)]
    if (not counts or len(counts) > 4 or len(text) > 262144
            or any(len(base) > 2 or len(count) > 4 or not 2 <= int(base) <= 62
                   or int(count) > 2048 for base, count in counts)):
        return ""
    try:
        if unpacker is None:
            from streamhub.resolvers import unpack as unpacker
        return (unpacker(text) or "")[:MAX_PAGE_BYTES]
    except Exception:
        return ""


def resolve_public_embed(url, referer=None, *, fetch=None, unpacker=None):
    """Return (media URL, Kodi header string) for a public embedded player.

    At most three page fetches and two iframe hops; each response is capped at
    1 MiB with no retries. Only literal player sources are extracted. Captchas,
    HTTP errors, scripts requiring execution and inaccessible pages return None.
    Fetch/unpacker injection supports deterministic offline fixtures.
    """
    url = _url(url, "")
    if not url:
        return None
    if fetch is None:
        from streamhub.utils import request as fetch
    pending = [(url, referer or url, 0)]
    visited = set()
    requests = 0
    while pending and requests < MAX_PAGES:
        current, parent, depth = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        requests += 1
        response = None
        try:
            response = fetch("GET", current, headers={"User-Agent": UA, "Referer": parent}, timeout=8,
                             retries=0, max_bytes=MAX_PAGE_BYTES, allow_redirects=True)
            if response.status_code >= 400:
                continue
            final_url = _url(response.url or current, current)
            if not final_url or (final_url != current and final_url in visited):
                continue
            visited.add(final_url)
            content_type = response.headers.get("Content-Type", "").lower().split(";", 1)[0].strip()
            if content_type.startswith("video/") or content_type in _MEDIA_TYPES:
                return final_url, urlencode(_headers(parent))
            text = (response.text or "")[:MAX_PAGE_BYTES]
            if text.lstrip().startswith("#EXTM3U"):
                return final_url, urlencode(_headers(parent))
            if any(marker in text[:16384].lower() for marker in _PROTECTION):
                continue
            page = _Page()
            page.feed(text)
            base = _url(page.base, final_url) if page.base else final_url
            base = base or final_url
            candidates = page.sources + list(_script_sources(text))
            unpacked = _unpacked(text, unpacker)
            if unpacked:
                candidates.extend(_script_sources(unpacked))
            accepted = []
            for value, hint in candidates:
                media = _url(value, base)
                if not media:
                    continue
                path = urlsplit(media).path.lower()
                hint = hint.lower()
                if path.endswith(_MEDIA_EXTENSIONS) or hint in _MEDIA_TYPES:
                    rank = 0 if path.endswith(".m3u8") or hint in ("hls", "application/vnd.apple.mpegurl", "application/x-mpegurl") else 1
                    accepted.append((rank, media))
            if accepted:
                accepted.sort(key=lambda entry: entry[0])
                return accepted[0][1], urlencode(_headers(final_url))
            if depth < MAX_IFRAME_DEPTH:
                frames = [_url(frame, base) for frame in page.frames]
                frames = [frame for frame in frames if frame and frame not in visited]
                frames.sort(key=lambda frame: family_for(frame) is None)
                pending.extend((frame, final_url, depth + 1) for frame in frames)
        except Exception:
            continue
        finally:
            if response is not None and hasattr(response, "close"):
                try:
                    response.close()
                except Exception:
                    pass
    return None
