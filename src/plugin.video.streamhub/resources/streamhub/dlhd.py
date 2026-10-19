
from streamhub.utils import *

BASE = "https://dlhd.pk"
CHANNELS_PAGE = BASE + "/24-7-channels.php"
STREAM_FOLDERS = ("stream", "cast", "watch", "plus", "casting", "player")
_RESOLVE_BUDGET = 15.0
_PLAYER_PAGE_LIMIT = 1024 * 1024
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_CARD_RE = re.compile(
    r'<a\s+class="card"\s+href="/watch\.php\?id=(\d+)"\s+data-title="([^"]*)"',
    re.IGNORECASE
)
_IFRAME_RE = re.compile(r'<iframe[^>]+src="([^"]+)"[^>]*id="thatframe"', re.IGNORECASE)
_IFRAME_FALLBACK_RE = re.compile(r'<iframe[^>]+src="([^"]+)"', re.IGNORECASE)
_ATOB_RE = re.compile(r"window\.atob\(\s*['\"]([A-Za-z0-9+/=_-]{40,})['\"]\s*\)")


def _ua_headers(referer=None):
	h = {"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"}
	if referer:
		h["Referer"] = referer
	return h


def fetch_channels():
	cacheOk, data = get_cache("dlhd_channels")
	if cacheOk and isinstance(data, list) and data:
		return data
	log("DLHD: fetching channel list")
	resp = request("GET", CHANNELS_PAGE, headers=_ua_headers(BASE), timeout=15, retries=1)
	resp.raise_for_status()
	html = resp.text
	seen, channels = set(), []
	for cid, title in _CARD_RE.findall(html):
		if cid in seen:
			continue
		seen.add(cid)

		from html import unescape
		channels.append({"id": cid, "name": unescape(title).strip().upper()})
	channels.sort(key=lambda c: c["name"])
	if channels:
		set_cache("dlhd_channels", channels, timeout=6)
	return channels


def get_channels():
	out = {}
	countries = {}
	for c in fetch_channels():
		variants, country = channel_variants(c["name"])
		name = ""
		for v in variants:
			name = filterout(v)
			if name:
				break
		if not name:
			continue
		url = "dlhd://%s" % c["id"]
		lst = out.setdefault(name, [])
		if url not in lst:
			lst.append(url)
		if country:
			cl = countries.setdefault(name, [])
			if country not in cl:
				cl.append(country)
	try:
		home.setProperty("dlhd_channel_countries", json.dumps(countries))
	except Exception:
		pass
	return out


def _startup_timeout(deadline):
	if monitor.abortRequested():
		raise RuntimeError("DLHD: resolution cancelled")
	remaining = deadline - time.monotonic()
	if remaining <= 0:
		raise RuntimeError("DLHD: resolution deadline reached")
	return min(6, remaining)


def _extract_iframe_src(stream_url, deadline=None):
	if deadline is None:
		deadline = time.monotonic() + _RESOLVE_BUDGET
	resp = request("GET", stream_url, headers=_ua_headers(BASE + "/"),
	               timeout=_startup_timeout(deadline), retries=0, max_bytes=_PLAYER_PAGE_LIMIT)
	_startup_timeout(deadline)
	resp.raise_for_status()
	html = resp.text
	m = _IFRAME_RE.search(html) or _IFRAME_FALLBACK_RE.search(html)
	if not m:
		raise RuntimeError("DLHD: no iframe in %s" % stream_url)
	src = m.group(1).strip()
	if src.startswith("//"):
		src = "https:" + src
	elif src.startswith("/"):
		src = BASE + src
	return src


def _extract_m3u8_from_iframe(iframe_url, ref, deadline=None):
	if deadline is None:
		deadline = time.monotonic() + _RESOLVE_BUDGET
	resp = request("GET", iframe_url, headers=_ua_headers(ref),
	               timeout=_startup_timeout(deadline), retries=0, max_bytes=_PLAYER_PAGE_LIMIT)
	_startup_timeout(deadline)
	resp.raise_for_status()
	html = resp.text
	for blob in _ATOB_RE.findall(html):
		try:
			decoded = base64.b64decode(blob + "==").decode("utf-8", "ignore")
		except Exception:
			continue
		if ".m3u8" in decoded and decoded.startswith("http"):
			return decoded
	raise RuntimeError("DLHD: m3u8 not found in iframe page")


def resolve_stream(channel_id):
	cid = str(channel_id).strip()
	if cid.startswith("dlhd://"):
		cid = cid[len("dlhd://"):]
	last_err = None
	deadline = time.monotonic() + _RESOLVE_BUDGET
	for folder in STREAM_FOLDERS:
		if monitor.abortRequested() or time.monotonic() >= deadline:
			last_err = "resolution cancelled or deadline reached"
			break
		stream_url = "%s/%s/stream-%s.php" % (BASE, folder, cid)
		try:
			iframe = _extract_iframe_src(stream_url, deadline=deadline)
			m3u8 = _extract_m3u8_from_iframe(iframe, stream_url, deadline=deadline)

			parsed = urlparse(iframe)
			origin = "%s://%s" % (parsed.scheme, parsed.netloc)
			headers = {
				"Referer": origin + "/",
				"Origin": origin,
				"User-Agent": DEFAULT_UA,
			}
			qs = "&".join("%s=%s" % (k, quote(v, safe="")) for k, v in headers.items())
			log("DLHD: resolved %s via /%s/ → %s" % (cid, folder, m3u8))
			return m3u8, qs
		except Exception as e:
			last_err = e
			log("DLHD: /%s/stream-%s.php failed: %s" % (folder, cid, e))
			continue
	raise RuntimeError("DLHD: all folders failed for channel %s (%s)" % (cid, last_err))




_SCHEDULE_SOURCES = ("extra", "extra_ppv", "extra_backup", "extra_topembed")
_EVENT_RE = re.compile(r'<div class="schedule__event">(.*?)</div>\s*</div>\s*</div>', re.S)
_EVENT_TITLE_RE = re.compile(r'schedule__eventTitle">([^<]+)<')
_EVENT_TIME_RE = re.compile(r'data-time="([^"]+)"')
_EVENT_DAY_RE = re.compile(r'schedule__dayTitle">([^<]+)<')
_CAT_HEADER_RE = re.compile(r'<div class="card__meta">([^<]+)</div>')
_CHANNEL_LINK_RE = re.compile(
	r'<a[^>]+href="([^"]+)"[^>]*?(?:data-ch="([^"]*)")?[^>]*>([^<]+)</a>'
)

_SOCCER_KEYWORDS = ("soccer", "football", "fussball", "fußball", "fifa", "uefa",
                    "champions league", "europa league", "premier league",
                    "bundesliga", "la liga", "serie a", "ligue 1", "mls",
                    "world cup", "wm ", "friendly")
_SOCCER_EXCLUDES = ("american football", "nfl", "afl ", "rugby", "aussie rules",
                    "gridiron")


def _classify_url(href):
	href = href.strip()
	if href.startswith("/"):
		full = BASE + href
	elif href.startswith("http"):
		full = href
	else:
		full = BASE + "/" + href
	m = re.search(r"/watch\.php\?id=(\d+)", full)
	if m:
		return "dlhd://" + m.group(1), True
	m = re.search(r"/watchppv\.php\?id=([^&]+)", full)
	if m:
		return "dlhdppv://" + m.group(1), False
	m = re.search(r"/watchstreamed\.php\?id=([^&]+)", full)
	if m:
		return "dlhdstreamed://" + m.group(1), False
	return None, False


_MARKER_SPLIT_RE = re.compile(
	r'(<div class="schedule__dayTitle">'
	r'|<div class="card__meta">'
	r'|<div class="schedule__event">)'
)


def _parse_schedule_html(html):
	from html import unescape
	parts = _MARKER_SPLIT_RE.split(html)
	if len(parts) < 2:
		return []
	events = []
	last_day = None
	last_cat = None

	for i in range(1, len(parts), 2):
		marker = parts[i]
		body = parts[i + 1] if i + 1 < len(parts) else ""
		if "schedule__dayTitle" in marker:
			m = re.match(r'([^<]+)', body)
			if m:
				last_day = m.group(1).strip()
			continue
		if "card__meta" in marker:




			m = re.match(r'([^<]+)', body)
			if m and "schedule__categoryBody" in body[:600]:
				last_cat = m.group(1).strip()
			continue
		if "schedule__event" in marker:
			title = re.search(r'schedule__eventTitle">([^<]+)<', body)
			time_ = re.search(r'data-time="([^"]+)"', body)
			if not title:
				continue



			channels = []
			seen_urls = set()
			for ch in re.finditer(
				r'<a[^>]+href="([^"]+)"[^>]*?(?:data-ch="([^"]*)")?[^>]*>([^<]+)</a>',
				body):
				href, data_ch, label = ch.group(1), ch.group(2), ch.group(3)
				scheme, playable = _classify_url(href)
				if not scheme or scheme in seen_urls:
					continue
				seen_urls.add(scheme)
				channels.append({
					"label": (label or data_ch or "Stream").strip(),
					"url": scheme,
					"playable": playable,
				})
			events.append({
				"day": last_day or "Today",
				"category": last_cat or "Sports",
				"time": time_.group(1) if time_ else "",
				"title": unescape(title.group(1)).strip(),
				"channels": channels,
			})
	return events


def fetch_schedule(cat=None):
	key = "dlhd_sched_%s" % (cat or "all")
	cacheOk, data = get_cache(key)
	if cacheOk and isinstance(data, list):
		return data
	log("DLHD: fetching schedule for cat=%s" % cat)
	merged = []
	seen_titles = set()
	for src in _SCHEDULE_SOURCES:
		url = "%s/schedule-api.php?source=%s" % (BASE, src)
		if cat:
			url += "&cat=%s" % cat
		try:
			r = request("GET", url, headers=_ua_headers(BASE + "/"), timeout=12, retries=0)
			r.raise_for_status()
			payload = r.json()
		except Exception as e:
			log("DLHD schedule: %s failed: %s" % (src, e))
			continue
		html = payload.get("html") if isinstance(payload, dict) else None
		if not html:
			continue
		for ev in _parse_schedule_html(html):
			ev["source"] = src


			tk = (ev["time"], ev["title"].lower())
			if tk in seen_titles:

				for existing in merged:
					if (existing["time"], existing["title"].lower()) == tk:
						existing_urls = {c["url"] for c in existing["channels"]}
						for c in ev["channels"]:
							if c["url"] not in existing_urls:
								existing["channels"].append(c)
						break
				continue
			seen_titles.add(tk)
			merged.append(ev)

	merged.sort(key=lambda e: (e.get("day", ""), e.get("time", "")))

	if merged:
		set_cache(key, merged, timeout=5.0 / 60)
	return merged


def fetch_soccer_events():


	all_events = fetch_schedule(cat="soccer") + fetch_schedule()
	soccer = []
	seen = set()
	for ev in all_events:
		hay = (ev.get("category", "") + " " + ev.get("title", "")).lower()
		if any(bad in hay for bad in _SOCCER_EXCLUDES):
			continue
		if not any(kw in hay for kw in _SOCCER_KEYWORDS):
			continue
		key = (ev.get("time", ""), ev.get("title", "").lower())
		if key in seen:
			continue
		seen.add(key)
		soccer.append(ev)
	soccer.sort(key=lambda e: (e.get("day", ""), e.get("time", "")))
	return soccer




def _resolve_via_dlhd_wrapper(wrapper_url, ref):
	try:
		deadline = time.monotonic() + _RESOLVE_BUDGET
		iframe = _extract_iframe_src(wrapper_url, deadline=deadline)
		return _extract_m3u8_from_iframe(iframe, wrapper_url, deadline=deadline), iframe
	except Exception as e:
		log("DLHD wrapper resolve failed: %s" % e)
		return None, None


def resolve_ppv(token):
	url = "%s/watchppv.php?id=%s" % (BASE, token)
	m3u8, iframe = _resolve_via_dlhd_wrapper(url, BASE + "/")
	if not m3u8:
		raise RuntimeError("DLHD PPV not extractable (JS-protected): %s" % token)
	parsed = urlparse(iframe)
	origin = "%s://%s" % (parsed.scheme, parsed.netloc)
	qs = "&".join("%s=%s" % (k, quote(v, safe="")) for k, v in
	              {"Referer": origin + "/", "Origin": origin,
	               "User-Agent": DEFAULT_UA}.items())
	return m3u8, qs


def resolve_streamed(token):
	url = "%s/watchstreamed.php?id=%s" % (BASE, token)
	m3u8, iframe = _resolve_via_dlhd_wrapper(url, BASE + "/")
	if not m3u8:
		raise RuntimeError("DLHD streamed not extractable (JS-protected): %s" % token)
	parsed = urlparse(iframe)
	origin = "%s://%s" % (parsed.scheme, parsed.netloc)
	qs = "&".join("%s=%s" % (k, quote(v, safe="")) for k, v in
	              {"Referer": origin + "/", "Origin": origin,
	               "User-Agent": DEFAULT_UA}.items())
	return m3u8, qs


def resolve_any(link):
	if link.startswith("dlhd://"):
		return resolve_stream(link)
	if link.startswith("dlhdppv://"):
		return resolve_ppv(link[len("dlhdppv://"):])
	if link.startswith("dlhdstreamed://"):
		return resolve_streamed(link[len("dlhdstreamed://"):])
	raise RuntimeError("DLHD: unrecognized link scheme: %s" % link)
