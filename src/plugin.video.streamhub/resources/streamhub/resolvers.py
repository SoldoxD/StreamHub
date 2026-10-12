
from streamhub.utils import request, log, quote, urlparse, base64, re, format_exc
import json as _json
import random as _random
import string as _string
import time as _time
from html import unescape as _unescape
from urllib.parse import urljoin, urlencode, unquote

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _hdr(referer=None, **extra):
	h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
	if referer:
		h["Referer"] = referer
	h.update(extra)
	return h


def _hdr_qs(referer_origin):
	return "Referer=%s&User-Agent=%s" % (
		quote(referer_origin, safe=""), quote(UA, safe=""))


VOE_HOSTS = ("voe.sx", "voe-un", "voe-net", "voe-net", "voeun", "voeunblock",
             "voe-network", "cloudwindow")

# Public Dood aliases documented by the maintained ResolveURL host registry.
DOOD_HOSTS = (
    "dood.watch", "doodstream.com", "dood.to", "dood.so", "dood.cx", "dood.la",
    "dood.ws", "dood.sh", "doodstream.co", "dood.pm", "dood.wf", "dood.re",
    "dood.yt", "dooood.com", "dood.stream", "ds2play.com", "doods.pro",
    "ds2video.com", "d0o0d.com", "do0od.com", "d0000d.com", "d000d.com",
    "dood.li", "dood.work", "dooodster.com", "vidply.com", "all3do.com",
    "do7go.com", "doodcdn.io", "doply.net", "vide0.net", "vvide0.com",
    "d-s.io", "dsvplay.com", "myvidplay.com", "playmogo.com",
)


def _rot13(s):
	out = []
	for c in s:
		o = ord(c)
		if 65 <= o <= 90:
			out.append(chr((o - 65 + 13) % 26 + 65))
		elif 97 <= o <= 122:
			out.append(chr((o - 97 + 13) % 26 + 97))
		else:
			out.append(c)
	return "".join(out)


def _voe_decode(raw):
	step = _rot13(raw)
	for junk in ["@$", "^^", "~@", "%?", "*~", "!!", "#&"]:
		step = step.replace(junk, "_")
	step = step.replace("_", "")
	b = base64.b64decode(step)
	shifted = bytes((x - 3) % 256 for x in b)
	rev = shifted[::-1]
	final = base64.b64decode(rev)
	return _json.loads(final)


def resolve_voe(url):
	try:
		r = request("GET", url, headers=_hdr("https://voe.sx/"),
		            timeout=8, retries=1, allow_redirects=True)
		html = r.text
		for _ in range(3):
			mred = re.search(r"window\.location\.href\s*=\s*'(https?://[^']+)'", html)
			if not mred or mred.group(1) in r.url:
				break
			r = request("GET", mred.group(1), headers=_hdr("https://voe.sx/"),
			            timeout=15, retries=2, allow_redirects=True)
			html = r.text
		m = re.search(
			r'<script type="application/json"[^>]*>\s*(\[.*?\]|".*?")\s*</script>',
			html, re.S)
		if m:
			raw = _json.loads(m.group(1))
			if isinstance(raw, list):
				raw = raw[0]
			data = _voe_decode(raw)
			direct = data.get("direct_access_url")
			hls = data.get("source")
			# Let the player adapt to available bandwidth when a playlist exists.
			src = hls or direct
			if src:
				origin = "https://%s" % urlparse(r.url).netloc
				return src, _hdr_qs(origin + "/")
		mm = re.search(r"'hls'\s*:\s*'([^']+\.m3u8[^']*)'", html) \
			or re.search(r'"hls"\s*:\s*"([^"]+\.m3u8[^"]*)"', html) \
			or re.search(r"(https?://[^\s'\"<>]+\.m3u8[^\s'\"<>]*)", html)
		if mm:
			cand = mm.group(1)
			if "\\/" in cand:
				cand = cand.replace("\\/", "/")
			origin = "https://%s" % urlparse(r.url).netloc
			return cand, _hdr_qs(origin + "/")
	except Exception:
		log(format_exc())
	return None


def is_dood_url(url):
	try:
		parsed = urlparse(url)
		host = (parsed.hostname or "").lower()
		return parsed.scheme in ("http", "https") and any(
			host == domain or host.endswith("." + domain) for domain in DOOD_HOSTS)
	except ValueError:
		return False


def _dood_page_url(url):
	# Old aliases share file IDs with the current public player domain.
	parsed = urlparse(url)
	if is_dood_url(url) and re.match(r"^/[de]/[A-Za-z0-9]+(?:/|$)", parsed.path):
		if parsed.hostname not in ("doodstream.com", "myvidplay.com", "playmogo.com"):
			return parsed._replace(scheme="https", netloc="playmogo.com").geturl()
	return url


def _dood_headers(referer, media_url):
	headers = {"User-Agent": UA, "Referer": referer}
	# Transfer only cookies whose domain/path/security policy matches this CDN.
	try:
		from streamhub.utils import session
		from urllib.request import Request
		cookie_request = Request(media_url)
		session.cookies.add_cookie_header(cookie_request)
		cookie = cookie_request.get_header("Cookie")
		if cookie:
			headers["Cookie"] = cookie
	except Exception:
		pass
	return urlencode(headers)


def _dood_media_url(prefix, page_html, pass_url):
	prefix = _unescape(prefix.strip())
	parsed = urlparse(prefix)
	if (parsed.scheme not in ("http", "https") or not parsed.hostname
	        or re.search(r"[\s|]", prefix)):
		return None
	# The endpoint can now return a complete, already signed storage URL.
	# Appending the legacy random suffix would invalidate its signature/path.
	if (parsed.query or parsed.fragment or "cloudflarestorage." in parsed.hostname
	        or parsed.path.lower().endswith((".mp4", ".m3u8", ".mkv", ".webm"))):
		return prefix
	player_script = page_html.partition("function makePlay")[2] or page_html
	query = re.search(r"""(['"])(\?[^'"\r\n]*\bexpiry=)\1""", player_script)
	if query:
		suffix = _unescape(query.group(2))
	else:
		token_match = re.search(r"""[?&]token=([^&'"\s]+)""", player_script)
		if token_match:
			token = unquote(_unescape(token_match.group(1)))
		else:
			token = unquote(urlparse(pass_url).path.rstrip("/").rsplit("/", 1)[-1])
		if not token:
			return None
		suffix = "?" + urlencode({"token": token}) + "&expiry="
	rand = "".join(_random.choice(_string.ascii_letters + _string.digits) for _ in range(10))
	return prefix + rand + suffix + str(int(_time.time() * 1000))


def resolve_dood(url):
	try:
		page_url = _dood_page_url(url)
		origin = urlparse(page_url)._replace(path="/", params="", query="", fragment="").geturl()
		referer = origin
		player_ref = None
		seen = set()
		for _ in range(3):
			if page_url in seen:
				return None
			seen.add(page_url)
			r = request("GET", page_url, headers=_hdr(referer), timeout=8,
			            retries=1, allow_redirects=True, max_bytes=1024 * 1024)
			if _challenged(r):
				mark_blocked(r.url)
				return None
			if r.status_code >= 400:
				return None
			page_url = r.url
			if player_ref is None:
				# Dood uses the top-level player/download page as the Referer,
				# including when its video player is loaded through an iframe.
				player_ref = page_url
			html = _unescape(r.text or "").replace("\\/", "/")
			match = re.search(r"""(['"])([^'"\s]*?/pass_md5/[^'"\s]+)\1""", html)
			if match:
				pass_url = urljoin(page_url, match.group(2))
				if urlparse(pass_url).scheme not in ("http", "https"):
					return None
				pr = request("GET", pass_url, headers=_hdr(player_ref), timeout=8,
				             retries=1, max_bytes=16384)
				if _challenged(pr):
					mark_blocked(pr.url)
					return None
				if pr.status_code >= 400:
					return None
				final = _dood_media_url(pr.text, html, pass_url)
				if final:
					mark_ok(url)
					return final, _dood_headers(player_ref, final)
				return None
			next_page = None
			for frame in re.finditer(r"""<iframe\b[^>]*\bsrc\s*=\s*(['"])(.*?)\1""", html, re.I | re.S):
				candidate = urljoin(page_url, frame.group(2))
				if (urlparse(candidate).scheme in ("http", "https")
				        and (is_dood_url(candidate)
				             or urlparse(candidate).netloc == urlparse(page_url).netloc)):
					next_page = candidate
					break
			if next_page is None:
				parsed = urlparse(page_url)
				if parsed.path.startswith("/d/"):
					next_page = parsed._replace(path="/e/" + parsed.path[3:]).geturl()
			if not next_page:
				return None
			referer, page_url = player_ref, next_page
	except Exception:
		log(format_exc())
	return None


_HLS_RE = re.compile(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*')
_MP4_RE = re.compile(r'https?://[^\s\'"<>]+\.mp4[^\s\'"<>]*')
_ATOB_RE = re.compile(r"atob\(\s*[\"']([A-Za-z0-9+/=_-]{40,})[\"']\s*\)")


def resolve_generic(url, referer=None):
	from streamhub.host_support import resolve_public_embed
	return resolve_public_embed(url, referer)


def _match(url, hosts):
	low = url.lower()
	return any(h in low for h in hosts)


_CF_MARKERS = ("just a moment", "cf-browser-verification", "challenge-platform",
               "cf_chl_opt", "enable javascript and cookies to continue")

_BLOCKED = set()


def _family(url):
	net = (urlparse(url).netloc or "").lower()
	parts = [p for p in net.split(".") if p not in ("www",)]
	return parts[-2] if len(parts) >= 2 else net


def is_blocked(url):
	fam = _family(url)
	if fam in _BLOCKED:
		return True
	try:
		from streamhub.utils import memcache_get
		if memcache_get("cfblock", fam, False):
			_BLOCKED.add(fam)
			return True
	except Exception:
		pass
	return False


_STRIKES = {}
_STRIKE_LIMIT = 3


def mark_blocked(url, why="cloudflare"):
	fam = _family(url)
	if fam in _BLOCKED:
		return
	n = _STRIKES.get(fam, 0) + 1
	_STRIKES[fam] = n
	if n < _STRIKE_LIMIT:
		return
	_BLOCKED.add(fam)
	log("%s: %d %s challenges in a row, skipping its remaining links" % (fam, n, why))
	try:
		from streamhub.utils import memcache_set
		memcache_set("cfblock", fam, True, ttl=300, maxsize=40)
	except Exception:
		pass


def mark_ok(url):
	fam = _family(url)
	if _STRIKES.pop(fam, None):
		log("%s: recovered, strike count reset" % fam)


def _challenged(resp):
	try:
		if resp.status_code not in (403, 503, 429):
			return False
		body = (resp.text or "")[:4000].lower()
		return any(m in body for m in _CF_MARKERS)
	except Exception:
		return False


STREAMTAPE_HOSTS = ("streamtape", "stape", "strtape", "streamadblock",
                    "tapewithadblock", "streamta.pe", "tapeblocker",
                    "streamtapeadblock")

MIXDROP_HOSTS = ("mixdrop", "mxdrop", "mixdrp")

PACKED_HOSTS = ("supervideo", "vidoza", "vinovo",
                "streamruby", "rubystm", "vidmoly", "savefiles", "dropload",
                "highload", "redload", "streamvid", "veev", "vidsonic",
                "flyfile", "myvidplay", "upbolt", "clipwatching",
                "streamwish", "filelions", "dhtpre", "swiftplayers",
                "lulustream", "kinoger", "luluvdo", "vidhide", "smoothpre")

_PACKED_ARGS_RE = re.compile(
	r"\}\s*\(\s*'(.*?)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\s*\.split\('\|'\)",
	re.S)

_SUB_RE = re.compile(r"\.substring\(\s*(\d+)\s*\)")
_LIT_RE = re.compile(
	r"""(['"])(.*?)\1\s*\)?((?:\s*\.substring\(\s*\d+\s*\))*)""")
_NOROBOT_RE = re.compile(
	r"ById\('norobotlink'\)\.innerHTML\s*=\s*([^;\n]+)")
_MIXDROP_RE = re.compile(r"""MDCore\.wurl\s*=\s*["']([^"']+)["']""")


def _b62(n, base):
	digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
	if n < base:
		out = ""
	else:
		out = _b62(n // base, base)
	return out + digits[n % base]


def unpack(js):
	out = []
	for a in _PACKED_ARGS_RE.finditer(js or ""):
		try:
			payload = a.group(1)
			base, count = int(a.group(2)), int(a.group(3))
			words = a.group(4).split("|")
		except Exception:
			continue
		payload = (payload.replace("\\\\", "\\").replace("\\'", "'")
		           .replace('\\"', '"').replace("\\/", "/"))
		res = payload
		for i in range(count - 1, -1, -1):
			if i < len(words) and words[i]:
				res = re.sub(r"\b" + re.escape(_b62(i, base)) + r"\b",
				             words[i].replace("\\", "\\\\"), res)
		out.append(res)
	return "\n".join(out)


def _eval_concat(expr):
	out = []
	for _q, lit, subs in _LIT_RE.findall(expr):
		s = lit
		for n in _SUB_RE.findall(subs):
			s = s[int(n):]
		out.append(s)
	return "".join(out)


def resolve_streamtape(url):
	try:
		r = request("GET", url, headers=_hdr(url), timeout=8, retries=1)
		if r.status_code >= 400:
			return None
		m = _NOROBOT_RE.search(r.text)
		if not m:
			return None
		link = _eval_concat(m.group(1))
		if not link:
			return None
		if link.startswith("//"):
			link = "https:" + link
		elif link.startswith("/"):
			link = "https:/" + link
		if "get_video" not in link:
			return None
		origin = "https://%s" % urlparse(r.url).netloc
		return link, _hdr_qs(origin + "/")
	except Exception:
		log(format_exc())
	return None


def resolve_mixdrop(url):
	try:
		if "/f/" in url:
			url = url.replace("/f/", "/e/")
		r = request("GET", url, headers=_hdr(url), timeout=8, retries=1)
		if r.status_code >= 400:
			return None
		body = r.text
		m = _MIXDROP_RE.search(body) or _MIXDROP_RE.search(unpack(body))
		if not m:
			return None
		link = m.group(1)
		if link.startswith("//"):
			link = "https:" + link
		return link, _hdr_qs(r.url)
	except Exception:
		log(format_exc())
	return None


def resolve_packed(url, referer=None):
	for ref in (None, referer):
		try:
			r = request("GET", url, headers=_hdr(ref), timeout=8, retries=1)
			if r.status_code >= 400:
				continue
			origin = "https://%s" % urlparse(r.url).netloc
			for blob in (unpack(r.text), r.text):
				if not blob:
					continue
				for rx in (_HLS_RE, _MP4_RE):
					m = rx.search(blob)
					if m:
						return m.group(0).rstrip('"\\)\''), _hdr_qs(origin + "/")
		except Exception:
			log(format_exc())
	return None


VIDARA_HOSTS = ("vidara", "vidaraa", "vidmatrix", "vidmatrixa")
FIRESTREAM_HOSTS = ("firestream",)

_FC_RE = re.compile(r"/(?:e|d|v|f|w)/([A-Za-z0-9_-]{6,})")
_BLOB_RE = re.compile(r"""id=["']token-blob["'][^>]*>([^<]+)<""")


def _filecode(url):
	m = _FC_RE.search(urlparse(url).path)
	if m:
		return m.group(1)
	path = urlparse(url).path.strip("/")
	return path.split("/")[-1].split(".")[0] if path else ""


def resolve_vidara(url):
	try:
		origin = "https://%s" % urlparse(url).netloc
		fc = _filecode(url)
		if not fc:
			return None
		h = _hdr(url)
		h["Content-Type"] = "application/json"
		h["X-Requested-With"] = "XMLHttpRequest"
		r = request("POST", origin + "/api/stream", headers=h,
		             json={"filecode": fc, "device": "web"},
		             timeout=12, retries=1)
		if r.status_code >= 400:
			return None
		data = r.json()
		src = data.get("streaming_url") or data.get("file") or data.get("url")
		if not src:
			return None
		if src.startswith("//"):
			src = "https:" + src
		return src, _hdr_qs(origin + "/")
	except Exception:
		log(format_exc())
	return None


def resolve_firestream(url):
	try:
		origin = "https://%s" % urlparse(url).netloc
		slug = _filecode(url)
		if not slug:
			return None
		r = request("GET", url, headers=_hdr(None), timeout=12, retries=1,
		             max_bytes=400000)
		if r.status_code >= 400:
			return None
		m = _BLOB_RE.search(r.text or "")
		if not m:
			return None
		h = _hdr(url)
		h["Content-Type"] = "application/json"
		pr = request("POST", "%s/api/videos/%s/resolve" % (origin, quote(slug)),
		              headers=h, json={"blob": m.group(1).strip()},
		              timeout=12, retries=1)
		if pr.status_code >= 400:
			return None
		src = (pr.json() or {}).get("signedVideoUrl")
		if not src:
			return None
		if src.startswith("//"):
			src = "https:" + src
		return src, _hdr_qs(origin + "/")
	except Exception:
		log(format_exc())
	return None


FILEMOON_HOSTS = ("filemoon", "moonmov", "kerapoxy", "moviesm4u", "fmoonembed")

_FM_CODE_RE = re.compile(r"/(?:e|d|v)/([A-Za-z0-9]+)")


def _b64u(s):
	s = s.replace("-", "+").replace("_", "/")
	s += "=" * (-len(s) % 4)
	return base64.b64decode(s)


def _select_filemoon_source(sources, limit):
	"""Keep adaptive playlists and select direct renditions within the quality cap."""
	def number(source, key):
		try:
			return max(0, int(source.get(key) or 0))
		except (TypeError, ValueError):
			return 0

	known, unknown = [], []
	for source in sources:
		if not isinstance(source, dict) or not isinstance(source.get("url"), str) or not source["url"]:
			continue
		height = number(source, "height")
		if not height:
			unknown.append(source)
		elif height <= limit:
			known.append(source)
	# A playlist without a fixed height can contain multiple adaptive renditions.
	# Keep it intact so the player's resolution ceiling applies to its variants.
	for source in unknown:
		try:
			if urlparse(source["url"].split("|", 1)[0]).path.lower().endswith(".m3u8"):
				return source
		except ValueError:
			continue
	if known:
		return max(known, key=lambda source: (number(source, "height"),
		                                    number(source, "bitrate_kbps")))
	# Unknown metadata must not be guessed from signed URLs or opaque IDs.
	return unknown[0] if unknown else None


def resolve_filemoon(url):
	try:
		m = _FM_CODE_RE.search(urlparse(url).path)
		if not m:
			return None
		code = m.group(1)
		origin = "https://%s" % urlparse(url).netloc
		h = _hdr(url)
		h["Accept"] = "application/json, text/plain, */*"
		data = None
		for base in (origin, "https://filemoon.to", "https://filemoon.sx"):
			r = request("GET", "%s/api/videos/%s" % (base, code),
			            headers=_hdr(base + "/", Accept="application/json"),
			            timeout=10, retries=1)
			if r.status_code == 200:
				try:
					j = r.json()
				except Exception:
					continue
				if isinstance(j, dict) and j.get("playback"):
					data = j
					origin = base
					break
		if not data:
			return None
		pb = data["playback"]
		kp = pb.get("key_parts") or []
		ver = int(pb.get("version") or 0)
		if not (kp and 1 <= ver <= len(kp) and (30 - ver) < len(kp)):
			return None
		key = _b64u(kp[ver - 1]) + _b64u(kp[30 - ver])
		from streamhub.aesgcm import decrypt
		plain = decrypt(key, _b64u(pb["iv"]), _b64u(pb["payload"]))
		info = _json.loads(plain.decode("utf-8", "ignore"))
		sources = info.get("sources") or []
		if not sources:
			return None
		from streamhub.playback import max_height
		best = _select_filemoon_source(sources, max_height())
		src = best.get("url") if best else None
		if not src:
			return None
		return src, _hdr_qs(origin + "/")
	except Exception:
		log(format_exc())
	return None


def resolve(url, referer=None, generic=True):
	if not url:
		return None
	if is_blocked(url):
		return None
	if _match(url, FILEMOON_HOSTS):
		res = resolve_filemoon(url)
		if res:
			return res
	if _match(url, VOE_HOSTS):
		res = resolve_voe(url)
		if res:
			return res
	if is_dood_url(url):
		res = resolve_dood(url)
		if res:
			return res
	if _match(url, VIDARA_HOSTS):
		res = resolve_vidara(url)
		if res:
			return res
	if _match(url, FIRESTREAM_HOSTS):
		res = resolve_firestream(url)
		if res:
			return res
	if _match(url, STREAMTAPE_HOSTS):
		res = resolve_streamtape(url)
		if res:
			return res
	if _match(url, MIXDROP_HOSTS):
		res = resolve_mixdrop(url)
		if res:
			return res
	if _match(url, PACKED_HOSTS):
		res = resolve_packed(url, referer)
		if res:
			return res
	return resolve_generic(url, referer) if generic else None
