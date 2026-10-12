
from streamhub.utils import *
from html import unescape

BASE = "https://livetv.sx"
LANG_PATH = "/enx"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_EVENT_BLOCK_RE = re.compile(
	r'<td width=34 align="center" valign="top">\s*'
	r'<img[^>]*alt="([^"]*)"[^>]*>\s*</td>\s*<td>\s*'
	r'<a class="(live|)"\s*href="(/enx/eventinfo/(\d+)_[^"]*)"[^>]*>(.*?)</a>'
	r'(.*?)</td>',
	re.S | re.I,
)
_DESC_RE = re.compile(r'<span class="evdesc">([^<]*)</span>', re.I)
_SCORE_RE = re.compile(r'<span class="live">\s*&nbsp;\s*([^<]*?)\s*</span>', re.I)
_LIVEIMG_RE = re.compile(r'live\.gif', re.I)
_TAG_RE = re.compile(r"<[^>]+>")

_PLAYER_LINK_RE = re.compile(
	r"show_webplayer\('([^']+)',\s*'([^']+)',\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*'([^']+)'\)",
	re.I,
)
_EMB_IFRAME_RE = re.compile(
	r'["\']\s*//\s*([a-z0-9.\-]+)\s*/\s*player/live\.php\?([^"\']*)["\']',
	re.I | re.S)
_WS_RE = re.compile(r"\s+")
_PLINIT_RE = re.compile(r"\.init\(\s*['\"]([^'\"]+)['\"]", re.I)
_M3U8_RE = re.compile(r"https?://[^\s'\"<>]+\.m3u8[^\s'\"<>]*")


def _hdr(referer=None):
	h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
	if referer:
		h["Referer"] = referer
	return h


def _clean(s):
	return unescape(_TAG_RE.sub("", s or "")).replace("&ndash;", "-").strip()


def fetch_events():
	cacheOk, data = get_cache("livetv_events")
	if cacheOk and isinstance(data, list) and data:
		return data
	try:
		r = request("GET", BASE + LANG_PATH + "/", headers=_hdr(BASE),
		            timeout=10, retries=0)
		r.raise_for_status()
		html = r.text
	except Exception as e:
		log("livetv: event list failed: %s" % e)
		return []
	events, seen = [], set()
	for m in _EVENT_BLOCK_RE.finditer(html):
		cat = _clean(m.group(1))
		href = m.group(3)
		eid = m.group(4)
		title = _clean(m.group(5))
		tail = m.group(6) or ""
		if not title or eid in seen:
			continue
		seen.add(eid)
		desc = _DESC_RE.search(tail)
		desc = _clean(desc.group(1)) if desc else ""
		score = _SCORE_RE.search(tail)
		score = _clean(score.group(1)) if score else ""
		is_live = bool(m.group(2)) and bool(_LIVEIMG_RE.search(tail))
		events.append({
			"id": eid,
			"title": title,
			"category": cat,
			"desc": desc,
			"score": score,
			"live": is_live,
			"url": BASE + href,
		})
	if events:
		set_cache("livetv_events", events, timeout=5.0 / 60)
	return events


def fetch_links(event_url):
	try:
		r = request("GET", event_url, headers=_hdr(BASE + LANG_PATH + "/"),
		            timeout=10, retries=0)
		r.raise_for_status()
		html = r.text
	except Exception as e:
		log("livetv: event page failed: %s" % e)
		return []
	links, seen = [], set()
	for m in _PLAYER_LINK_RE.finditer(html):
		t, c, eid, lid, ci, si, lng = m.groups()
		key = (t, c)
		if key in seen:
			continue
		seen.add(key)
		links.append({
			"type": t,
			"c": c,
			"eid": eid,
			"lid": lid,
			"ci": ci,
			"si": si,
			"lang": (lng or "").upper(),
			"ref": event_url,
		})
	return links


def resolve_link(link):
	iframe = ("%s/export/webplayer.iframe.php?t=%s&c=%s&lang=%s"
	          "&eid=%s&lid=%s&ci=%s&si=%s" % (
		          BASE, quote(link["type"], safe=""), quote(link["c"], safe=""),
		          (link.get("lang") or "en").lower(), link["eid"], link["lid"],
		          link["ci"], link["si"]))
	try:
		r = request("GET", iframe, headers=_hdr(link.get("ref") or BASE),
		            timeout=10, retries=0)
		r.raise_for_status()
		html = r.text
	except Exception as e:
		log("livetv: iframe failed: %s" % e)
		return None, None
	m = _EMB_IFRAME_RE.search(html)
	if not m:
		mm = _M3U8_RE.search(html)
		if mm:
			return mm.group(0), _qs(BASE + "/")
		log("livetv: no embed player found")
		return None, None
	emb_host = _WS_RE.sub("", m.group(1))
	emb_qs = _WS_RE.sub("", m.group(2))
	emb_url = "https://%s/player/live.php?%s" % (emb_host, emb_qs)
	try:
		r2 = request("GET", emb_url, headers=_hdr(iframe), timeout=10, retries=0)
		r2.raise_for_status()
		html2 = r2.text
	except Exception as e:
		log("livetv: embed failed: %s" % e)
		return None, None
	mi = _PLINIT_RE.search(html2) or _M3U8_RE.search(html2)
	if not mi:
		log("livetv: no m3u8 in embed")
		return None, None
	url = mi.group(1) if mi.re is _PLINIT_RE else mi.group(0)
	if url.startswith("//"):
		url = "https:" + url
	if ".m3u8" not in url:
		mm = _M3U8_RE.search(html2)
		if not mm:
			return None, None
		url = mm.group(0)
	origin = "https://%s" % urlparse(emb_url).netloc
	return url, _qs(origin + "/")


def _qs(referer):
	return "Referer=%s&User-Agent=%s" % (
		quote(referer, safe=""), quote(UA, safe=""))


def resolve_any(scheme_url):
	try:
		payload = json.loads(base64.b64decode(
			scheme_url[len("livetv://"):].encode()).decode())
	except Exception:
		log(format_exc())
		return None, None
	return resolve_link(payload)


def make_scheme(link):
	return "livetv://" + base64.b64encode(
		json.dumps(link).encode()).decode()
