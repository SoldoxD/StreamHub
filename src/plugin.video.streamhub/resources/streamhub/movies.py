
from threading import Event
from streamhub.source_selection import select_diverse

from streamhub.utils import *
from streamhub.playback import configure_playback, split_stream_url, max_height

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _ua_headers(referer=None, **extra):
	h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
	if referer:
		h["Referer"] = referer
	h.update(extra)
	return h


def _normalize_title(t):
	import re as _re
	t = _re.sub(r"[^\w\s]", " ", t or "")
	t = _re.sub(r"\s+", " ", t).strip().lower()
	return t


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7,
          "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13}

_EP_WORDS = ("episode", "episodes", "teil", "part", "chapter", "kapitel",
             "vol", "volume", "season", "staffel")

_NOISE_WORDS = ("german", "dl", "ov", "omu", "uncut", "remastered", "extended",
                "directors", "director", "cut", "special", "edition", "hd",
                "fhd", "uhd", "1080p", "720p", "480p", "4k", "bluray", "brrip",
                "imax", "unrated", "theatrical", "kinofassung", "ts", "cam",
                "the", "der", "die", "das")

_MAX_SEQUEL = 30


def _num_token(tok):
	if tok.isdigit():
		n = int(tok)
		return n if 1 <= n <= _MAX_SEQUEL else None
	return _ROMAN.get(tok)


def _words(text):
	return [w for w in _normalize_title(text).split() if w]


def _title_parts(title):
	"""Split a title into (base, sequel number, subtitle).

	"Star Wars 2"                             -> ("star wars", 2,    "")
	"Star Wars: Episode II - Angriff der ..."  -> ("star wars", 2,    "angriff der klonkrieger")
	"Star Wars: Die letzten Jedi"              -> ("star wars", None, "die letzten jedi")
	"""
	raw = (title or "").replace("–", "-").replace("—", "-")
	head, sep, tail = raw.partition(":")
	if not sep:
		parts = raw.split(" - ", 1)
		head, tail = (parts[0], parts[1]) if len(parts) == 2 else (raw, "")
	hw, tw = _words(head), _words(tail)
	while tw and tw[-1] in _NOISE_WORDS:
		tw.pop()
	while hw and hw[-1] in _NOISE_WORDS:
		hw.pop()

	num = None
	if len(tw) >= 2 and tw[0] in _EP_WORDS:
		n = _num_token(tw[1])
		if n is not None:
			num, tw = n, tw[2:]
	if num is None:
		for i, w in enumerate(hw):
			if w in _EP_WORDS and i + 1 < len(hw):
				n = _num_token(hw[i + 1])
				if n is not None:
					num = n
					tw = hw[i + 2:] + tw
					hw = hw[:i]
					break
	if num is None and len(hw) >= 2:
		n = _num_token(hw[-1])
		if n is not None:
			num, hw = n, hw[:-1]
	return " ".join(hw), num, " ".join(tw)


def _identity_keys(title):
	"""Keys that two differently-worded titles can share when they are the
	same film. Deliberately conservative: the franchise base must match too,
	so "Solo: A Star Wars Story" never merges with "Rogue One: A Star Wars
	Story"."""
	keys = set()
	full = _normalize_title(title)
	if full:
		keys.add("n|" + full)
	base, num, sub = _title_parts(title)
	if len(base) >= 3:
		if num is not None:
			keys.add("e|%s|%d" % (base, num))
		if len(sub) >= 4:
			keys.add("s|%s|%s" % (base, sub))
	return keys


def identity_keys_show(title):
	"""Like _identity_keys but for series: a season number is NOT part of the
	identity, so "Breaking Bad - Staffel 2" and "Breaking Bad" are one show."""
	keys = set()
	full = _normalize_title(title)
	if full:
		keys.add("n|" + full)
	base, num, sub = _title_parts(title)
	if len(base) >= 3:
		if len(sub) >= 4:
			keys.add("s|%s|%s" % (base, sub))
		else:
			keys.add("b|%s" % base)
	return keys


def _year_int(y):
	try:
		y = int(str(y).strip()[:4])
		return y if 1900 <= y <= 2100 else None
	except (TypeError, ValueError):
		return None


def _may_merge(a, b):
	"""Veto obviously-wrong merges even when a key matched."""
	ta, tb = str(a.get("tmdb_id") or ""), str(b.get("tmdb_id") or "")
	if ta and tb and ta != tb:
		return False
	ya, yb = _year_int(a.get("year")), _year_int(b.get("year"))
	if ya and yb and abs(ya - yb) > 1:
		return False
	return True




class Movies67Provider:
	name = "67movies"
	BASE = "https://67movies.net"






	virtual = True

	def search(self, query, max_results=20):
		return []

	def get_hosts(self, movie):
		tmdb = movie.get("tmdb_id")
		if not tmdb:
			return []


		embed = "https://111movies.net/movie/%s" % tmdb
		return [{
			"provider": self.name,
			"host": "111movies",
			"label": "111movies (TMDB %s)" % tmdb,
			"url": embed,
			"resolver": "embed_generic",
			"referer": "https://67movies.net/",
			"lang": "OV",
		}]


class RamoflixProvider:
	name = "ramoflix"
	BASE = "https://ramoflix.net"

	_NON_MOVIE_HINTS = (

		"-season-", "-episode-", "/series/", "/tv-show", "/show/", "/season/",
	)

	_TV_TITLE_HINTS = (
		"andor", "skeleton crew", "bad batch", "mandalorian", "tales of",
		"clone wars", "rebels", "ahsoka", "the acolyte", "obi-wan kenobi",
		"book of boba", "rangers of the new",
	)

	def search(self, query, max_results=20):
		from html import unescape
		try:
			r = request("GET", self.BASE + "/?s=" + quote(query),
			            headers=_ua_headers(self.BASE), timeout=12, retries=0)
			r.raise_for_status()
			html = r.text
		except Exception as e:
			log("ramoflix search failed: %s" % e)
			return []
		out = []
		seen_urls = set()



		card_re = re.compile(
			r'<a[^>]+href="(https?://ramoflix\.net/[^"/]+/)"[^>]*>\s*'
			r'<img[^>]*?(?:data-src="([^"]+)"[^>]*?)?alt="([^"]+)"',
			re.IGNORECASE,
		)
		for m in card_re.finditer(html):
			url = m.group(1)
			poster = m.group(2) or None
			title = unescape(m.group(3)).strip()
			if url in seen_urls:
				continue
			seen_urls.add(url)
			low_url = url.lower()
			if any(h in low_url for h in self._NON_MOVIE_HINTS):
				continue
			if any(h in low_url for h in ("/category/", "/genre/", "/cast/",
			                              "/director/", "/year/")):
				continue
			low_title = title.lower()
			if any(h in low_title for h in self._TV_TITLE_HINTS):
				continue
			out.append({"tmdb_id": None, "title": title, "year": None,
			            "poster": poster,
			            "provider": self.name, "ramoflix_url": url})
			if len(out) >= max_results:
				break
		return out

	def get_hosts(self, movie):
		page = movie.get("ramoflix_url")
		if not page:
			return []
		try:
			r = request("GET", page, headers=_ua_headers(self.BASE),
			            timeout=15, retries=0)
			r.raise_for_status()
			html = r.text
		except Exception as e:
			log("ramoflix page fetch failed: %s" % e)
			return []
		hosts = []


		for prov in ("111movies", "videasy", "vidsrc", "vidwtf",
		             "smashystream", "2embed"):
			if prov in html.lower():

				m = re.search(
					r'(?:src|data-(?:src|link|server))=["\']'
					r'(https?://[^"\']*' + re.escape(prov) + r'[^"\']*)["\']',
					html, re.IGNORECASE)
				if m:
					hosts.append({
						"provider": self.name,
						"host": prov,
						"label": "%s (via ramoflix)" % prov,
						"url": m.group(1),
						"resolver": "embed_generic",
						"referer": page,
						"lang": "DE",
					})

		if not hosts:
			for m in re.finditer(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I):
				src = m.group(1)
				if "about:blank" in src or "/ad" in src or "youtube" in src:
					continue
				hosts.append({
					"provider": self.name,
					"host": urlparse(src).netloc or "iframe",
					"label": "%s (via ramoflix)" % (urlparse(src).netloc or "iframe"),
					"url": src,
					"resolver": "embed_generic",
					"referer": page,
					"lang": "DE",
				})
		return hosts


class FilmpalastProvider:
	name = "filmpalast"
	BASE = "https://filmpalast.to"
	_TV_SLUG_RE = re.compile(r"-s\d{1,2}e\d{1,2}\b", re.I)

	def search(self, query, max_results=20):
		from html import unescape
		try:

			r = request("GET",
			            self.BASE + "/search/title/" + quote(query),
			            headers=_ua_headers(self.BASE), timeout=12, retries=0)
			r.raise_for_status()
			html = r.text
		except Exception as e:
			log("filmpalast search failed: %s" % e)
			return []
		out = []
		seen = set()

		title_re = re.compile(
			r'<h2[^>]*>\s*<a[^>]+href="(?://)?(?:filmpalast\.to)?(/stream/[^"]+)"'
			r'[^>]*title="([^"]+)"', re.I)
		poster_re = re.compile(
			r'<a[^>]+href="(?://)?(?:filmpalast\.to)?(/stream/[^"]+)"[^>]*>\s*'
			r'<img[^>]+src="([^"]+)"', re.I)
		posters = {p.group(1): p.group(2) for p in poster_re.finditer(html)}
		for m in title_re.finditer(html):
			path = m.group(1)
			title = unescape(m.group(2)).strip()
			if path in seen:
				continue
			seen.add(path)
			if self._TV_SLUG_RE.search(path):
				continue
			poster = posters.get(path)
			if poster and poster.startswith("/"):
				poster = self.BASE + poster
			out.append({"tmdb_id": None, "title": title, "year": None,
			            "poster": poster,
			            "provider": self.name,
			            "filmpalast_url": self.BASE + path})
			if len(out) >= max_results:
				break
		return out

	def get_hosts(self, movie):
		page = movie.get("filmpalast_url")
		if not page:
			return []
		try:
			r = request("GET", page, headers=_ua_headers(self.BASE),
			            timeout=15, retries=0)
			r.raise_for_status()
			html = r.text
		except Exception as e:
			log("filmpalast page fetch failed: %s" % e)
			return []
		hosts = []
		block_re = re.compile(
			r'<ul class="currentStreamLinks">'
			r'.*?<p class="hostName">([^<]+)</p>'
			r'.*?<a[^>]+href="(https?://[^"]+)"',
			re.S | re.I,
		)
		seen_urls = set()
		for m in block_re.finditer(html):
			hoster = m.group(1).strip()
			url = m.group(2).strip()
			if url in seen_urls:
				continue
			seen_urls.add(url)
			hosts.append({
				"provider": self.name,
				"host": urlparse(url).netloc or hoster,
				"label": "%s (via filmpalast)" % hoster,
				"url": url,
				"resolver": "embed_generic",
				"referer": page,
				"lang": "DE",
			})
		return hosts


class KinoxProvider:
	name = "kinox"
	BASE = "https://kinox.to"

	def search(self, query, max_results=20):
		from html import unescape
		try:
			r = request("GET",
			            self.BASE + "/Search.html?q=" + quote(query),
			            headers=_ua_headers(self.BASE), timeout=12, retries=0)
			r.raise_for_status()
			html = r.text
		except Exception as e:
			log("kinox search failed: %s" % e)
			return []
		out = []
		seen = set()
		row_re = re.compile(
			r'<a[^>]+href="(/Stream/([^"/]+)\.html)"[^>]*>([^<]+)</a>', re.I)
		for m in row_re.finditer(html):
			path = m.group(1)
			slug = m.group(2)
			text = unescape(m.group(3)).strip()
			if path in seen or not text or text in ("Stream",):
				continue
			seen.add(path)
			title = text if text else slug.replace("_", " ")
			out.append({"tmdb_id": None, "title": title, "year": None,
			            "poster": None,
			            "provider": self.name,
			            "kinox_url": self.BASE + path,
			            "kinox_slug": slug})
			if len(out) >= max_results:
				break
		return out

	def get_hosts(self, movie):
		page = movie.get("kinox_url")
		slug = movie.get("kinox_slug")
		if not page or not slug:
			return []
		try:
			r = request("GET", page, headers=_ua_headers(self.BASE),
			            timeout=15, retries=0)
			r.raise_for_status()
			html = r.text
		except Exception as e:
			log("kinox page fetch failed: %s" % e)
			return []
		hoster_re = re.compile(
			r'<li[^>]+id="Hoster_(\d+)"[^>]+rel="([^"]+)"[^>]*>'
			r'\s*<div class="Named">([^<]+)</div>',
			re.I | re.S,
		)
		hosts = []
		seen_ids = set()
		for hm in hoster_re.finditer(html):
			hid = hm.group(1)
			rel = hm.group(2).replace("&amp;", "&")
			name = hm.group(3).strip()
			if hid in seen_ids:
				continue
			seen_ids.add(hid)
			iframe_url = self._resolve_mirror(rel, page)
			if not iframe_url:
				continue
			hosts.append({
				"provider": self.name,
				"host": name.lower().replace(".", ""),
				"label": "%s (via kinox)" % name,
				"url": iframe_url,
				"resolver": "embed_generic",
				"referer": self.BASE + "/",
				"lang": "DE",
			})
		return hosts

	def _resolve_mirror(self, rel, referer):
		try:
			r = request("GET",
			            "%s/aGET/Mirror/%s" % (self.BASE, rel),
			            headers={**_ua_headers(referer),
			                     "X-Requested-With": "XMLHttpRequest"},
			            timeout=12, retries=0)
			r.raise_for_status()
			data = r.json()
		except Exception as e:
			log("kinox mirror %s failed: %s" % (rel, e))
			return None
		stream = data.get("Stream", "")
		m = re.search(r'src=["\']([^"\']+)["\']', stream)
		if not m:
			return None
		src = m.group(1).replace("\\/", "/")
		if src.startswith("/"):
			src = self.BASE + src
		return src


class VavooProvider:
	name = "vavoo"

	def search(self, query, max_results=20):
		from html import unescape
		try:
			from streamhub.vjackson import callApi2


			q = query.replace(".", "%2E")
			res = callApi2("list", {"id": "movie.popular.search=" + q})
			items = res.get("data") if isinstance(res, dict) else res
			if not items:
				return []
		except Exception as e:
			log("vavoo search failed: %s" % e)
			return []
		out = []
		for it in items[:max_results]:
			tid = it.get("id", "")
			tmdb = tid.split(".")[1] if tid.startswith("movie.") and "." in tid else None
			out.append({"tmdb_id": tmdb,
			            "title": unescape(it.get("name", "")),
			            "year": str(it.get("year", "") or ""),
			            "poster": it.get("poster"),
			            "plot": it.get("description"),
			            "provider": self.name,
			            "vavoo_id": tid})
		return out

	def get_hosts(self, movie):
		tmdb = movie.get("tmdb_id")
		if not tmdb:
			return []
		try:
			from streamhub.vjackson import getAuthSignature
			_headers = {
				"user-agent": "MediaHubMX/2",
				"content-type": "application/json; charset=utf-8",
				"accept-encoding": "gzip",
				"mediahubmx-signature": getAuthSignature(),
			}
			_data = {
				"language": "de", "region": "AT", "type": "movie",
				"ids": {"tmdb_id": tmdb}, "name": movie.get("title", ""),
				"episode": {}, "clientVersion": "3.0.2"
			}
			mirrors = request_json(
				"POST", "https://vavoo.to/mediahubmx-source.json",
				json=_data, headers=_headers, timeout=10, retries=1
			) or []
		except Exception as e:
			log("vavoo get_hosts failed: %s" % e)
			return []
		hosts = []
		for a in mirrors:
			url = a.get("url")
			if not url:
				continue
			host = urlparse(url).netloc or "?"
			if "streamz" in host:
				continue
			quality = a.get("tag", "") or ""
			langs = a.get("languages") or ["de"]
			lang = _norm_lang(langs) or "DE"
			label = "%s%s (vavoo)" % (host, (" " + quality) if quality else "")
			hosts.append({
				"provider": self.name,
				"host": host,
				"label": label,
				"url": url,
				"resolver": "vavoo_chain",
				"quality": quality,
				"lang": lang,
			})
		return hosts


M4K_BASE = "https://movie4k.sx"
M4K_LANG_DE = 2
M4K_LANG_EN = 3

_M4K_DEAD_HOSTS = (
	"mystream.to", "upstream.to", "vidlox", "streamz", "flashx",
	"vivo.sx", "openload", "rapidvideo", "vshare", "gounlimited",
)


def m4k_lang_code():
	return M4K_LANG_DE if (pref_lang() or "DE") == "DE" else M4K_LANG_EN


def m4k_api(path, **params):
	params.setdefault("lang", m4k_lang_code())
	url = "%s/data/%s/?%s" % (M4K_BASE, path, urlencode(params))
	r = request("GET", url, headers=_ua_headers(M4K_BASE + "/",
	                                            Accept="application/json, text/plain, */*"),
	            timeout=12, retries=0)
	r.raise_for_status()
	return r.json()


def m4k_poster(rec):
	p = rec.get("poster_path") or rec.get("backdrop_path") or ""
	if p.startswith("/"):
		return "https://image.tmdb.org/t/p/w500" + p
	return p or rec.get("img_link") or None


_M4K_FAMILIES = (
	("voe", ("voe", "cloudwindow")),
	("doodstream", ("dood", "do7go", "d0000d", "d000d", "ds2play", "doods", "all3do", "do0od",
	                "ds2video", "dsvplay", "vidply", "playmogo")),
	("streamtape", ("streamtape", "stape", "streamadblock", "strtape", "tapewithadblock",
	                "streamta", "streamadblockplus")),
	("filemoon", ("filemoon", "moonmov", "kerapoxy")),
	("veev", ("veev",)),
	("vidsonic", ("vidsonic",)),
	("vinovo", ("vinovo",)),
	("vidara", ("vidara", "vidmatrix")),
	("firestream", ("firestream",)),
	("flyfile", ("flyfile",)),
	("savefiles", ("savefiles",)),
	("streamruby", ("streamruby", "rubystm", "rubyvid")),
	("vidmoly", ("vidmoly",)),
	("mixdrop", ("mixdrop", "mxdrop")),
	("dropload", ("dropload",)),
	("myvidplay", ("myvidplay",)),
)

_M4K_MAX_PER_FAMILY = 2
_M4K_MAX_TOTAL = 16

_M4K_ID_RE = re.compile(r"[A-Za-z0-9]{8,}")
_M4K_DIRECT_RE = re.compile(r"\.(mp4|mkv|m3u8|avi)(\?|$)", re.I)


def _m4k_family(host):
	for fam, needles in _M4K_FAMILIES:
		if any(n in host for n in needles):
			return fam
	return host.split(".")[0] or host


def _m4k_rank(fam):
	for i, (f, _) in enumerate(_M4K_FAMILIES):
		if f == fam:
			return i
	return len(_M4K_FAMILIES)


def _m4k_file_key(fam, url):
	path = urlparse(url).path
	m = _M4K_ID_RE.search(path.replace(".html", ""))
	return "%s:%s" % (fam, m.group(0).lower() if m else path.lower())


def m4k_streams(rec, episode=None):
	lang = "DE" if rec.get("lang") == M4K_LANG_DE else "OV"
	cands = []
	seen = set()
	for s in (rec.get("streams") or []):
		if s.get("deleted"):
			continue
		if episode is not None:
			try:
				if int(s.get("e")) != int(episode):
					continue
			except (TypeError, ValueError):
				continue
		url = (s.get("stream") or "").strip()
		if url.startswith("//"):
			url = "https:" + url
		if not url.startswith("http"):
			continue
		host = urlparse(url).netloc.lower().replace("www.", "")
		if not host or any(d in host for d in _M4K_DEAD_HOSTS):
			continue
		fam = _m4k_family(host)
		key = _m4k_file_key(fam, url)
		if key in seen:
			continue
		seen.add(key)
		rank = -1 if _M4K_DIRECT_RE.search(urlparse(url).path) else _m4k_rank(fam)
		cands.append((rank, fam, s.get("added") or "", host, url))

	cands.sort(key=lambda c: c[2], reverse=True)
	cands.sort(key=lambda c: (c[0], c[1]))

	hosts = []
	per_family = {}
	for rank, fam, added, host, url in select_diverse(cands, _M4K_MAX_TOTAL, _M4K_MAX_PER_FAMILY):
		if per_family.get(fam, 0) >= _M4K_MAX_PER_FAMILY:
			continue
		per_family[fam] = per_family.get(fam, 0) + 1
		direct = bool(_M4K_DIRECT_RE.search(urlparse(url).path))
		hosts.append({
			"provider": "movie4k",
			"host": host,
			"label": "%s%s (via movie4k)" % (fam, " direct" if direct else ""),
			"url": url,
			"resolver": "direct" if direct else "embed_generic",
			"referer": M4K_BASE + "/",
			"lang": lang,
		})
		if len(hosts) >= _M4K_MAX_TOTAL:
			break
	return hosts


class Movie4kProvider:
	name = "movie4k"
	BASE = M4K_BASE

	def search(self, query, max_results=20):
		try:
			data = m4k_api("search", keyword=query, page=1)
		except Exception as e:
			log("movie4k search failed: %s" % e)
			return []
		if not isinstance(data, list):
			return []
		out = []
		for rec in data:
			if rec.get("tv"):
				continue
			if not rec.get("_id") or not rec.get("title"):
				continue
			out.append({
				"tmdb_id": None,
				"title": rec.get("title"),
				"year": rec.get("year") or None,
				"poster": m4k_poster(rec),
				"provider": self.name,
				"movie4k_id": rec["_id"],
			})
			if len(out) >= max_results:
				break
		return out

	def get_hosts(self, movie):
		mid = movie.get("movie4k_id")
		if not mid:
			return []
		try:
			rec = m4k_api("watch", _id=mid)
		except Exception as e:
			log("movie4k watch failed: %s" % e)
			return []
		if not isinstance(rec, dict):
			return []
		return m4k_streams(rec)


_TOKEN = None


def set_token(token):
	global _TOKEN
	_TOKEN = token


def _is_cancelled(exc):
	try:
		from lib.scrapers.base import Cancelled
		return isinstance(exc, Cancelled)
	except Exception:
		return False


def _enabled_providers():
	try:
		from lib.scrapers.adapter import movie_providers
		provs = movie_providers(token=_TOKEN)
		if provs:
			return provs
	except Exception:
		log("scrapers unavailable, using legacy providers\n%s" % format_exc())
	provs = []
	if getSetting("vavoo") == "true":
		provs.append(VavooProvider())
	if getSetting("movie4k") == "true":
		provs.append(Movie4kProvider())
	if getSetting("movies67") == "true":
		provs.append(Movies67Provider())
	if getSetting("ramoflix") == "true":
		provs.append(RamoflixProvider())
	if getSetting("filmpalast") == "true":
		provs.append(FilmpalastProvider())
	if getSetting("kinox") == "true":
		provs.append(KinoxProvider())
	return provs




def _merge_results(per_provider):
	by_key = {}
	merged_order = []
	for provider, results in per_provider:
		for m in results:
			norm = _normalize_title(m.get("title", ""))
			if not norm:
				continue
			keys = _identity_keys(m.get("title", ""))
			entry = None
			for k in keys:
				cand = by_key.get(k)
				if cand is not None and _may_merge(cand, m):
					entry = cand
					break
			if entry is None:
				entry = {
					"title": m.get("title") or "",
					"year": m.get("year"),
					"tmdb_id": m.get("tmdb_id"),
					"poster": m.get("poster"),
					"plot": m.get("plot"),
					"_norm": norm,
					"_keys": set(),
					"_hits": [],
				}
				merged_order.append(entry)

			entry["_hits"].append((provider, m))
			entry["_keys"] |= keys
			for k in keys:
				by_key.setdefault(k, entry)

			if not entry.get("tmdb_id") and m.get("tmdb_id"):
				entry["tmdb_id"] = m["tmdb_id"]
			if not entry.get("year") and m.get("year"):
				entry["year"] = m["year"]
			if not entry.get("poster") and m.get("poster"):
				entry["poster"] = m["poster"]
			if not entry.get("plot") and m.get("plot"):
				entry["plot"] = m["plot"]

			# prefer the most descriptive title for the merged entry
			if len(m.get("title") or "") > len(entry.get("title") or ""):
				entry["title"] = m["title"]
	return merged_order


HIST_BUCKET = "movies_search"


def search(params):
	q = params.get("q")
	if q:
		history_add(HIST_BUCKET, q)
		_search_and_list(q)
		return
	if params.get("new") == "true":
		kb = xbmc.Keyboard("", "StreamHub - Filme Suche", False)
		kb.doModal()
		if not kb.isConfirmed():
			end()
			return
		query = kb.getText().strip()
		if not query:
			end()
			return
		history_add(HIST_BUCKET, query)
		_search_and_list(query)
		return
	_history_menu()


def _history_menu():
	set_content("files")
	set_category("Filme - Suche")
	addDir2("[B][COLOR lime]Neue Suche[/COLOR][/B]", "DefaultAddonsSearch",
	        "movie_search", new="true")
	items = history_load(HIST_BUCKET)
	for q in items:
		cm = [("Diesen Eintrag löschen",
		       "RunPlugin(%s?action=movie_search_del&q=%s)" % (
			       sys.argv[0], quote(q, safe=""))),
		      ("Suchverlauf löschen",
		       "RunPlugin(%s?action=movie_search_clear)" % sys.argv[0])]
		addDir2(q, "DefaultAddonsSearch", "movie_search", context=cm, q=q)
	if items:
		addDir2("[COLOR red]Suchverlauf löschen[/COLOR]", "DefaultAddonsSearch",
		        "movie_search_clear", isFolder=False)
	end()


def _search_and_list(query):
	provs = _enabled_providers()
	if not provs:
		dialog.notification("StreamHub", "Keine Quellen aktiviert", time=3000)
		return
	per_provider = []
	with ThreadPoolExecutor(max_workers=max(len(provs), 1)) as ex:
		futs = {ex.submit(p.search, query, 20): p for p in provs}
		for fut in as_completed(futs):
			p = futs[fut]
			try:
				per_provider.append((p.name, fut.result() or []))
			except Exception:
				log(format_exc())
				per_provider.append((p.name, []))
	merged = _merge_results(per_provider)
	if not merged:
		dialog.notification("StreamHub",
		                    "Keine Treffer für: %s" % query, time=3000)
		return
	set_content("movies")
	set_category("Suche: %s" % query)



	prov_objs = _enabled_providers()
	for m in merged:
		providers_hit = {p for p, _ in m["_hits"]}
		for p in prov_objs:
			if getattr(p, "virtual", False) and m.get("tmdb_id"):
				providers_hit.add(p.name)
		providers_sorted = sorted(providers_hit)
		badge = "+".join(p[:1].upper() for p in providers_sorted)
		year_part = " (%s)" % m["year"] if m.get("year") else ""
		label = "%s%s  [COLOR grey](%s)[/COLOR]" % (
			m.get("title", "?"),
			year_part,
			badge,
		)
		o = ListItem(label)
		o.setProperty("IsPlayable", "true")
		if m.get("poster"):
			o.setArt({"poster": m["poster"], "thumb": m["poster"]})
		info_tag = ListItemInfoTag(o, 'video')
		info_year = None
		if m.get("year"):
			y = str(m["year"]).strip()
			if y.isdigit():
				info_year = int(y)
		_infos = {
			"title": m.get("title", ""),
			"year": info_year,
			"plot": (m.get("plot") or "") +
			        "\n\n[B]Quellen:[/B] " + ", ".join(providers_sorted),
		}
		try:
			from streamhub import resume
			_infos = resume.decorate(o, {"tmdb_id": m.get("tmdb_id"),
			                             "title": m.get("title", "")}, _infos)
			cm = [("StreamHub: gesehen umschalten",
			       "RunPlugin(%s?action=toggle_watched&movie=%s)" % (
				       sys.argv[0], quote(json.dumps(
					       {"tmdb_id": m.get("tmdb_id"),
					        "title": m.get("title", "")}))))]
			o.addContextMenuItems(cm)
		except Exception:
			pass
		info_tag.set_info(_infos)
		params = {
			"action": "movie_play",
			"movie": json.dumps({
				"title": m.get("title", ""),
				"year": m.get("year"),
				"tmdb_id": m.get("tmdb_id"),
				"poster": m.get("poster"),
				"plot": m.get("plot"),
				"hits": [{"provider": pn, **mv} for pn, mv in m["_hits"]],
			}),
		}
		add(params, o, False)
	end()


def index(params):
	set_content("files")
	addDir2("Suche (alle Quellen)", "DefaultAddonsSearch", "movie_search")

	provs = _enabled_providers()
	for p in provs:
		if p.name == "vavoo":

			continue
	end()




def _find_provider_hit(provider, movie):
	for hit in movie.get("hits", []):
		if hit.get("provider") == provider.name:
			return hit
	if getattr(provider, "virtual", False):
		if movie.get("tmdb_id"):
			return movie
		return None
	title = movie.get("title", "")
	target = _normalize_title(title)
	cache_key = _url_key("%s|%s" % (provider.name, target))
	cached = memcache_get("phc", cache_key, "__miss__")
	if cached != "__miss__":
		return cached or None
	try:
		year = movie.get("year")
		results = provider.search(title, max_results=8) or []
	except Exception:
		log(format_exc())
		memcache_set("phc", cache_key, None, ttl=600)
		return None
	best = None
	for r in results:
		rt = _normalize_title(r.get("title", ""))
		if rt == target:
			if not year or not r.get("year") or str(r.get("year")) == str(year):
				memcache_set("phc", cache_key, r, ttl=1800)
				return r
			best = best or r
		elif target in rt or rt in target:
			best = best or r
	memcache_set("phc", cache_key, best, ttl=1800)
	return best


def _check_host_alive(host, timeout=3):
	url = host.get("url", "")
	if not url:
		return False
	if "vavoo.to" in url or host.get("provider") == "vavoo":
		return True
	try:
		headers = {"User-Agent": UA}
		if host.get("referer"):
			headers["Referer"] = host["referer"]
		r = request("HEAD", url, headers=headers, timeout=timeout,
		            retries=0, allow_redirects=True)
		if r.status_code in (405, 501):
			r = request("GET", url, headers={**headers, "Range": "bytes=0-0"},
			            timeout=timeout, retries=0, max_bytes=0,
			            allow_redirects=True)
		return r.status_code < 400
	except Exception:
		return False


_DEAD_SIGNALS = (
	"video not found", "file not found", "video was deleted", "video was removed",
	"file was deleted", "file has been removed", "video removed", "not available",
	"file deleted", "file removed", "file expired", "video expired",
	"deleted by user", "removed by user", "video is unavailable",
	"content unavailable", "no longer available", "video was blocked",
	"404 not found", "410 gone", "media unavailable",
	"diese datei wurde", "video nicht mehr", "nicht verfügbar",
	"wurde gelöscht", "wurde entfernt", "video wurde", "existiert nicht",
	"aviso: e-mail",
)


_LIVE_SIGNALS = (
	".m3u8", "master.m3u8", "index.m3u8", "playlist.m3u8", "manifest.mpd",
	"sources:", "sources :", "sources=", '"file":', "'file':", "file:", "file :",
	"jwplayer", "clappr", "hls.js", "playerinstance", "video_id",
	"videosource", "videohash", "streamurl", "streamurl:",
	"data-vsource", "data-src=", "player.src(", "playersetup",
	"getcursor()", "shakaplayer", "videojs(",
)


def _url_key(url):
	return md5((url or "").encode("utf-8", "ignore")).hexdigest()[:16]


def _check_alive_cache_get(url):
	return memcache_get("hac", _url_key(url), None)


def _check_alive_cache_set(url, verdict, ttl=300):
	memcache_set("hac", _url_key(url), bool(verdict), ttl=ttl, maxsize=150)


def check_host_deep(host, timeout=3):
	url = host.get("url", "") or ""
	if not url:
		return False
	if "vavoo.to" in url or host.get("provider") == "vavoo":
		return True
	cached = _check_alive_cache_get(url)
	if cached is not None:
		return cached
	try:
		headers = {"User-Agent": UA}
		if host.get("referer"):
			headers["Referer"] = host["referer"]
		r = request("GET", url, headers=headers, timeout=timeout,
		            retries=0, max_bytes=80000, allow_redirects=True)
	except Exception:
		_check_alive_cache_set(url, False)
		return False
	if r.status_code >= 400:
		_check_alive_cache_set(url, False)
		return False
	ct = (r.headers.get("content-type", "") or "").lower()
	if "html" not in ct and "text" not in ct:
		verdict = True
		_check_alive_cache_set(url, verdict)
		return verdict
	body = r.text[:80000]
	low = body.lower()
	for sig in _DEAD_SIGNALS:
		if sig in low:
			_check_alive_cache_set(url, False)
			return False
	for sig in _LIVE_SIGNALS:
		if sig in low:
			_check_alive_cache_set(url, True)
			return True
	verdict = len(body) > 8000
	_check_alive_cache_set(url, verdict)
	return verdict


_HOST_SORT = {"vavoo": 0, "filmpalast": 1, "67movies": 2, "ramoflix": 3, "kinox": 4, "hdfilme": 5, "streamkiste": 6, "serienstream": 7, "movie2k": 8, "einschalten": 9}

_LANG_MAP = {
	"de": "DE", "deu": "DE", "ger": "DE", "german": "DE", "deutsch": "DE",
	"en": "EN", "eng": "EN", "english": "EN",
	"fr": "FR", "fra": "FR", "french": "FR",
	"it": "IT", "ita": "IT",
	"es": "ES", "spa": "ES",
	"tr": "TR", "tur": "TR",
	"multi": "MULTI",
}


def _norm_lang(langs):
	if not langs:
		return ""
	if isinstance(langs, str):
		langs = [langs]
	codes = []
	for l in langs:
		c = _LANG_MAP.get(str(l).strip().lower(), str(l)[:2].upper())
		if c and c not in codes:
			codes.append(c)
	if "DE" in codes:
		codes = ["DE"] + [c for c in codes if c != "DE"]
	return "/".join(codes[:3])


def pref_lang():
	p = (getSetting("pref_lang") or "de").strip().lower()
	return "" if p in ("any", "") else p.upper()


_LANG_COUNTRIES = {
	"DE": ("DE", "AT", "CH"),
	"EN": ("UK", "US", "USA", "CA", "AU", "NZ", "IE"),
	"FR": ("FR", "BE", "CA", "CH"),
	"IT": ("IT", "CH"),
	"ES": ("ES", "MX", "AR"),
	"TR": ("TR",),
}


def lang_countries(want=None):
	if want is None:
		want = pref_lang()
	if not want:
		return ()
	return _LANG_COUNTRIES.get(want, (want,))


def _lang_rank(h):
	want = pref_lang()
	if not want:
		return 0
	lang = (h.get("lang") or "").upper()
	if want in lang:
		return 0
	if not lang or lang == "OV":
		return 2
	return 1


_QUALITY_TIERS = (
	(2160, ("2160p", "2160", "4k", "uhd", "ultrahd", "ultra hd")),
	(1440, ("1440p", "1440", "2k", "qhd")),
	(1080, ("1080p", "1080", "fhd", "fullhd", "full hd")),
	(720, ("720p", "720", "hd", "hdrip", "hdtv")),
	(576, ("576p", "576", "pal")),
	(480, ("480p", "480", "sd", "dvdrip")),
	(360, ("360p", "360")),
)

_HDR_MARKERS = ("hdr10+", "hdr10", "hdr", "dolby vision", "dolbyvision", "dovi", "dv ")
_CODEC_MARKERS = (
	("av1", 3), ("hevc", 2), ("h265", 2), ("x265", 2), ("h.265", 2),
	("h264", 1), ("x264", 1), ("h.264", 1),
)
_AUDIO_MARKERS = (
	("atmos", 4), ("truehd", 4), ("dts-hd", 3), ("dtshd", 3), ("dts", 2),
	("eac3", 2), ("ddp", 2), ("ac3", 1), ("aac", 0),
)

_RES_RE = re.compile(r"(\d{3,4})\s*[pP]\b")


def _detect_quality(h):
	if h.get("_qscore") is not None:
		return h["_qscore"], h.get("_qlabel", "")
	hay = " ".join(str(h.get(k) or "") for k in
	               ("quality", "label", "host", "url", "name")).lower()
	score, qlabel = 0, ""
	m = _RES_RE.search(hay)
	if m:
		try:
			val = int(m.group(1))
			score = val
			qlabel = "%dp" % val
		except Exception:
			pass
	if not score:
		for val, markers in _QUALITY_TIERS:
			if any(mk in hay for mk in markers):
				score = val
				qlabel = "%dp" % val if val >= 360 else ""
				break
	if score >= 2160:
		qlabel = "4K"
	hdr = any(mk in hay for mk in _HDR_MARKERS)
	codec_bonus = 0
	for mk, bonus in _CODEC_MARKERS:
		if mk in hay:
			codec_bonus = bonus
			break
	audio_bonus = 0
	for mk, bonus in _AUDIO_MARKERS:
		if mk in hay:
			audio_bonus = bonus
			break
	total = score + (300 if hdr else 0) + codec_bonus * 20 + audio_bonus * 5
	h["_qscore"] = total
	h["_qlabel"] = qlabel
	h["_hdr"] = hdr
	return total, qlabel


def _known_height(host):
	# Only explicit display metadata is safe for a hard limit. URL tokens and
	# host names (e.g. movie4k) can contain resolution-like substrings by chance.
	text = " ".join(str(host.get(k) or "") for k in ("quality", "label", "name"))
	matches = re.findall(r"(?<![a-z0-9])(?:2160p?|1440p?|1080p?|720p?|576p?|480p?|360p?|4k|uhd|fhd|hd)(?![a-z0-9])",
	                     text.lower())
	aliases = {"4k": 2160, "uhd": 2160, "fhd": 1080, "hd": 720}
	return max((aliases[m] if m in aliases else int(m.rstrip("p")) for m in matches), default=0)


def _within_quality_limit(hosts):
	"""Keep unknown renditions; exclude explicitly labelled oversized streams."""
	limit = max_height()
	return [host for host in hosts if _known_height(host) <= limit]



def _sortkey(h):
	qscore, _ = _detect_quality(h)
	return (_lang_rank(h),
	        0 if h.get("_alive") is True else 1,
	        _speed_tier(h),
	        -qscore,
	        _HOST_SORT.get(h.get("provider"), 9),
	        h.get("label", ""))


_PROV_COLOUR = {
	"vavoo": "deepskyblue",
	"movie4k": "violet",
	"filmpalast": "gold",
	"kinox": "orange",
	"ramoflix": "springgreen",
	"67movies": "khaki",
	"hdfilme": "lightblue",
	"streamkiste": "coral",
	"serienstream": "lightgreen",
	"movie2k": "violet",
	"einschalten": "lightskyblue",
}


def _provider_breakdown(hosts):
	counts = {}
	for h in hosts:
		p = h.get("provider") or "?"
		counts[p] = counts.get(p, 0) + 1
	return "   ".join(
		"[COLOR %s]%s %d[/COLOR]" % (_PROV_COLOUR.get(p, "grey"), p, n)
		for p, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def _host_protected(host):
	try:
		from streamhub.resolvers import get_resolve_failure
		protected = get_resolve_failure(host.get("url", "")) == "protected"
	except ImportError:
		protected = host.get("_failure") == "protected"
	if protected:
		host["_failure"] = "protected"
	else:
		host.pop("_failure", None)
	return protected


def _unplayable_message(hosts):
	if any(host.get("_failure") == "protected" for host in hosts):
		return "Kein Hoster spielbar; Zugriff teilweise durch Schutzprüfung blockiert"
	return "Kein Hoster spielbar"


def _host_display(h, index=None):
	qscore, qlabel = _detect_quality(h)
	prov = h.get("provider") or ""
	host = h.get("host") or "?"
	badges = []
	if h.get("_failure") == "protected":
		badges.append("[COLOR orange]Schutzprüfung[/COLOR]")
	elif h.get("_alive") is True:
		badges.append("[COLOR lime]OK[/COLOR]")
	elif h.get("_alive") is False:
		badges.append("[COLOR red]tot[/COLOR]")
	else:
		badges.append("[COLOR grey]?[/COLOR]")
	if qlabel:
		colour = "lime" if qscore >= 2160 else "cyan" if qscore >= 1080 else "white"
		badges.append("[COLOR %s]%s[/COLOR]" % (colour, qlabel))
	if h.get("_hdr"):
		badges.append("[COLOR orange]HDR[/COLOR]")
	if h.get("_direct") or h.get("resolver") == "direct":
		badges.append("[COLOR aqua]MP4[/COLOR]")
	kbps = h.get("_kbps")
	if kbps:
		scol = "lime" if kbps >= _FAST_KBPS else "yellow" if kbps >= _OK_KBPS else "red"
		if kbps >= 1024:
			badges.append("[COLOR %s]%.1f MB/s[/COLOR]" % (scol, kbps / 1024.0))
		else:
			badges.append("[COLOR %s]%d KB/s[/COLOR]" % (scol, kbps))
	lang = (h.get("lang") or "").upper()
	if lang:
		want = pref_lang()
		lcol = "lime" if (want and want in lang) else "yellow"
		badges.append("[COLOR %s]%s[/COLOR]" % (lcol, lang))
	name = "[B]%s[/B]" % host
	if prov:
		name = "%s  [COLOR %s]%s[/COLOR]" % (
			name, _PROV_COLOUR.get(prov, "grey"), prov)
	tag = ("  [COLOR grey][[/COLOR]%s[COLOR grey]][/COLOR]" % " ".join(badges)) if badges else ""
	if index is not None:
		return "[COLOR grey]%2d.[/COLOR] %s%s" % (index, name, tag)
	return "%s%s" % (name, tag)


_GATHER_DEADLINE = 12.0


def _gather_hosts(movie, manual_dialog=False):
	provs_by_name = {p.name: p for p in _enabled_providers()}
	if not provs_by_name:
		return []
	tasks = []
	seen_providers = set()
	for hit in movie.get("hits", []) or []:
		p = provs_by_name.get(hit.get("provider"))
		if p and p.name not in seen_providers:
			tasks.append((p, hit))
			seen_providers.add(p.name)
	for p in provs_by_name.values():
		if p.name in seen_providers:
			continue
		if getattr(p, "virtual", False) and movie.get("tmdb_id"):
			tasks.append((p, movie))
			seen_providers.add(p.name)
	if not tasks:
		return []
	all_hosts = []
	ex = ThreadPoolExecutor(max_workers=max(len(tasks), 1))
	futs = {ex.submit(p.get_hosts, hit): p.name for p, hit in tasks}
	try:
		for fut in as_completed(futs, timeout=_GATHER_DEADLINE):
			if _cancelled():
				break
			try:
				all_hosts.extend(fut.result() or [])
			except Exception as e:
				if _is_cancelled(e):
					break
				log(format_exc())
	except Exception:
		log("gather: deadline reached, using %d hosts" % len(all_hosts))
	_abandon(ex, futs)
	if _cancelled():
		return []
	all_hosts.sort(key=_sortkey)
	return all_hosts


def play(params):
	try:
		movie = json.loads(params.get("movie", "{}"))
	except Exception:
		fail_resolved("Film-Daten ungültig")
		return
	if params.get("manual") == "true":
		movie["_manual"] = "true"
	try:
		from streamhub import resume
		resume.save_playmeta(resume.key_for(movie), "movie",
		                     {"action": "movie_play", "movie": params.get("movie", "{}")},
		                     title=movie.get("title", ""), poster=movie.get("poster", ""))
	except Exception:
		log(format_exc())
	_play_common(movie, movie, movie.get("title", "?"))


def _play_common(movie, host_source, heading_title):
	manual = host_source.get("_manual") == "true"
	host_select = getSetting("movies_host_select") == "true" or manual
	deep_check = getSetting("movies_deep_check") == "true"

	prog = StatusProgress("StreamHub")
	try:
		from lib.scrapers.base import kodi_token
		set_token(kodi_token(prog))
	except Exception:
		pass
	prog.step(5, "Suche Hoster …", heading_title,
	          "[COLOR grey]Quellen: %s[/COLOR]" % ", ".join(
		          p.name for p in _enabled_providers()))

	cache_key = {"_movie_hosts": movie.get("tmdb_id") or movie.get("title", ""),
	             "s": movie.get("_s"), "e": movie.get("_e")}
	cacheOk, cached_hosts = get_cache(cache_key)
	if cacheOk and isinstance(cached_hosts, list) and cached_hosts:
		all_hosts = cached_hosts
		prog.step(20, "Hoster aus Cache", heading_title)
	else:
		try:
			all_hosts = _gather_hosts(host_source, manual_dialog=host_select)
		except Exception as e:
			if _is_cancelled(e):
				prog.close()
				fail_resolved()
				return
			raise
		if all_hosts:
			set_cache(cache_key, all_hosts, timeout=1)

	if not all_hosts:
		prog.close()
		fail_resolved("Keine Hoster gefunden")
		return

	choose_and_play(movie, all_hosts, heading_title, prog,
	                host_select, deep_check)


def choose_and_play(item, all_hosts, heading, prog, host_select, deep_check):
	if getSetting("fresh_start") != "false":
		freed, stopped = reset_playback_state()
		if freed or stopped:
			log("new playback: cleared %d verdict caches%s"
			    % (freed, ", stopped previous stream" if stopped else ""))
	all_hosts = sorted(_within_quality_limit(all_hosts), key=_sortkey)
	if not all_hosts:
		prog.close()
		fail_resolved("Keine Quelle innerhalb der eingestellten Maximalauflösung")
		return
	prog.step(25, "%d Hoster gefunden" % len(all_hosts), heading,
	          _provider_breakdown(all_hosts))

	def _picker_loop(pool, title):
		while pool:
			labels = [_host_display(h, i + 1) for i, (h, _) in enumerate(pool)]
			idx = selectDialog(labels, "%s — %s" % (title, heading))
			if idx < 0:
				fail_resolved()
				return True
			host, stream = pool[idx]
			resolved_at = host.get("_resolved_at")
			if stream is None or (resolved_at is not None and time.monotonic() - resolved_at >= 60):
				stream = _resolve_host(host)
			if stream:
				_start_playback(item, host, stream)
				return True
			protected = _host_protected(host)
			host["_alive"] = None if protected else False
			pool.pop(idx)
			if pool:
				dialog.notification(
					"StreamHub",
					("%s: Schutzprüfung blockiert den Zugriff - anderen Hoster wählen"
					 if protected else "%s nicht abrufbar - anderen Hoster wählen")
					% host.get("host", "?"), time=4000)
			else:
				fail_resolved(_unplayable_message(all_hosts))
				return True
		return False

	if host_select:
		pool = []
		if deep_check and len(all_hosts) > 1:
			working = _resolve_verify_bulk(all_hosts, deadline_s=18.0, prog=prog)
			prog.close()
			if _cancelled():
				fail_resolved()
				return
			pool = list(working)
			title = "Hoster wählen"
			if not pool:
				confirmed_dead = sum(1 for h in all_hosts
				                     if h.get("_alive") is False)
				rest = [h for h in all_hosts if h.get("_alive") is not False]
				if not rest:
					fail_resolved("Alle %d Hoster sind tot" % confirmed_dead)
					return
				pool = [(h, None) for h in rest]
				title = "Hoster wählen (nicht bestätigt)"
		else:
			prog.close()
			pool = [(h, None) for h in all_hosts]
			title = "Hoster wählen"
		_picker_loop(pool, title)
		return

	prog.step(45, "Löse Hoster auf …", heading)
	chosen, stream = _race_hosts(all_hosts, deadline_s=14.0,
	                             verify=deep_check, prog=prog)
	prog.close()
	if _cancelled():
		fail_resolved()
		return
	if not stream:
		rest = [h for h in all_hosts if h.get("_alive") is not False]
		if rest:
			log("auto-pick failed, offering %d hosts manually" % len(rest))
			dialog.notification("StreamHub",
			                    "Automatik fehlgeschlagen - Hoster wählen",
			                    time=4000)
			if _picker_loop([(h, None) for h in rest], "Hoster wählen"):
				return
	if stream:
		_start_playback(item, chosen, stream)
		return
	fail_resolved(_unplayable_message(all_hosts))


_TRUST_RESOLVE_HOSTS = ("dood", "cloudatacdn", "d0000d", "vidply", "playmogo")


_PROBE_BYTES = 65536


def _verify_stream(url, headers_qs, host=None):
	if not url:
		return False
	if host:
		try:
			from streamhub.resolvers import is_dood_url
			if is_dood_url(host.get("url", "")):
				# Keep short-lived Dood playback URLs for the actual player request.
				return None
		except ImportError:
			pass
	url, headers_qs = split_stream_url(url, headers_qs)
	low = url.lower()
	if any(h in low for h in _TRUST_RESOLVE_HOSTS):
		return None
	try:
		hdrs = {"User-Agent": UA}
		if headers_qs:
			hdrs.update(dict(parse_qsl(headers_qs, keep_blank_values=True)))
		path = urlparse(url).path.lower()
		is_hls = path.endswith(".m3u8") or ("hls" in path.split("/")
		                                      and "." not in path.rsplit("/", 1)[-1])
		is_dash = path.endswith(".mpd")
		probe = 8192 if is_hls else _PROBE_BYTES
		hdrs["Range"] = "bytes=0-%d" % (probe - 1)
		t0 = time.time()
		r = request("GET", url, headers=hdrs, timeout=6, retries=0,
		            allow_redirects=True, max_bytes=probe)
		elapsed = max(time.time() - t0, 0.001)
		if r.status_code in (200, 206):
			body = r.content or b""
			response_headers = getattr(r, "headers", {}) or {}
			content_type = (response_headers.get("Content-Type") or
			                response_headers.get("content-type") or "").lower().split(";", 1)[0].strip()
			head = body.lstrip()
			if head.startswith(b"\xef\xbb\xbf"):
				head = head[3:].lstrip()
			if (content_type in ("text/html", "application/xhtml+xml", "application/json")
			        or content_type.endswith("+json")
			        or head[:5].lower() in (b"<html", b"<!doc")):
				return False
			# Master playlists and MPDs can be tiny and need not have an extension.
			# A manifest marker in an error page is not a leading manifest signature.
			if re.match(br"#EXTM3U(?:\s|$)", head):
				return True
			if re.match(br"(?:<\?xml[^>]*>\s*)?(?:<!--.*?-->\s*)*"
			            br"<(?:[A-Za-z_][\w.-]*:)?MPD(?:\s|>)", head[:8192], re.S):
				return True
			manifest_types = ("application/vnd.apple.mpegurl", "application/x-mpegurl",
			                  "audio/mpegurl", "audio/x-mpegurl", "application/dash+xml")
			if (content_type in manifest_types
			        or ((is_hls or is_dash) and not content_type.startswith("video/"))):
				return False
			if len(body) < 1024 or body[:1].isdigit():
				return False
			if host is not None and len(body) >= 16384:
				host["_kbps"] = int((len(body) / 1024.0) / elapsed)
			return True
		if r.status_code in (401, 403, 429):
			return None
		return False
	except Exception:
		return None


_FAST_KBPS = 1500
_OK_KBPS = 500


def _speed_tier(h):
	kbps = h.get("_kbps")
	if not kbps:
		return 1
	if kbps >= _FAST_KBPS:
		return 0
	if kbps >= _OK_KBPS:
		return 1
	return 2


def _cancelled():
	return _TOKEN is not None and _TOKEN.cancelled()


def _resolve_and_verify(host, stop=None):
	if _cancelled() or (stop is not None and stop.is_set()):
		host["_alive"] = None
		return host, None
	stream = _resolve_host(host)
	if not stream:
		host["_alive"] = None
		return host, None
	if _cancelled() or (stop is not None and stop.is_set()):
		host["_alive"] = None
		return host, None
	host["_resolved_at"] = time.monotonic()
	url, hdr = stream
	verdict = _verify_stream(url, hdr, host)
	host["_alive"] = verdict
	if verdict is False:
		return host, None
	return host, stream


def _abandon(ex, futs):
	for f in futs:
		f.cancel()
	try:
		ex.shutdown(wait=False, cancel_futures=True)
	except TypeError:
		ex.shutdown(wait=False)


_ENOUGH_VERIFIED = 8


def _resolve_verify_bulk(hosts, deadline_s=10.0, prog=None, enough=_ENOUGH_VERIFIED):
	working = []
	if not hosts:
		return working
	total = len(hosts)
	done = 0
	dead = 0
	stop = Event()
	ex = ThreadPoolExecutor(max_workers=min(10, total))
	futs = {ex.submit(_resolve_and_verify, h, stop): h for h in hosts}
	pending = set(futs)
	cancelled = False
	deadline = time.time() + deadline_s
	while pending:
		if (prog and prog.iscanceled()) or _cancelled():
			cancelled = True
			break
		if time.time() > deadline:
			break
		if enough and sum(1 for w, _ in working
		                  if w.get("_alive") is True) >= enough:
			log("verify: %d confirmed hosts, skipping the remaining %d"
			    % (enough, len(pending)))
			break
		finished, pending = wait(pending, timeout=0.2,
		                         return_when=FIRST_COMPLETED)
		for fut in finished:
			done += 1
			try:
				h, stream = fut.result()
			except Exception:
				h, stream = futs[fut], None
				h["_alive"] = None
			if stream:
				working.append((h, stream))
			else:
				if h.get("_alive") is False:
					dead += 1
			confirmed = sum(1 for w, _ in working if w.get("_alive") is True)
			if prog:
				prog.step(25 + int(70 * done / total),
				          "Prüfe Hoster %d/%d" % (done, total),
				          "%s  [COLOR %s]%s[/COLOR]" % (
					          h.get("host", "?"),
					          _PROV_COLOUR.get(h.get("provider"), "grey"),
					          h.get("provider", "")),
				          stats_line(ok=confirmed, dead=dead,
				                     pending=total - done))
	if cancelled and _TOKEN is not None:
		_TOKEN.cancel()
	stop.set()
	_abandon(ex, futs)
	if cancelled:
		return []
	working.sort(key=lambda t: _sortkey(t[0]))
	return working


def _bulk_check_alive(hosts, deadline_s=2.5):
	flags = [None] * len(hosts)
	if not hosts:
		return flags
	with ThreadPoolExecutor(max_workers=min(6, len(hosts))) as ex:
		futs = {ex.submit(check_host_deep, h, 2): i for i, h in enumerate(hosts)}
		try:
			for fut in as_completed(futs, timeout=deadline_s):
				i = futs[fut]
				try:
					flags[i] = fut.result()
				except Exception:
					flags[i] = None
		except Exception:
			pass
	return flags


def _race_hosts(hosts, deadline_s=8.0, verify=False, prog=None):
	if not hosts:
		return None, None
	stop = Event()
	def worker(h):
		if stop.is_set() or _cancelled():
			return h, None
		return _resolve_and_verify(h, stop) if verify else (h, _resolve_host(h))
	if len(hosts) == 1:
		if prog:
			prog.step(60, "Löse Hoster auf …",
			          "%s  [COLOR %s]%s[/COLOR]" % (
				          hosts[0].get("host", "?"),
				          _PROV_COLOUR.get(hosts[0].get("provider"), "grey"),
				          hosts[0].get("provider", "")))
		h, stream = worker(hosts[0])
		if stream:
			return h, stream
		return None, None
	best = None
	total = len(hosts)
	done = 0
	ex = ThreadPoolExecutor(max_workers=min(6, total))
	futs = {ex.submit(worker, h): h for h in hosts}
	pending = set(futs)
	winner = None
	cancelled = False
	deadline = time.time() + deadline_s
	while pending and winner is None:
		if (prog and prog.iscanceled()) or _cancelled():
			cancelled = True
			break
		if time.time() > deadline:
			break
		finished, pending = wait(pending, timeout=0.2,
		                         return_when=FIRST_COMPLETED)
		for fut in finished:
			done += 1
			if prog:
				_h = futs[fut]
				prog.step(45 + int(50 * done / total),
				          "Löse Hoster auf %d/%d" % (done, total),
				          "%s  [COLOR %s]%s[/COLOR]" % (
					          _h.get("host", "?"),
					          _PROV_COLOUR.get(_h.get("provider"), "grey"),
					          _h.get("provider", "")),
				          stats_line(pending=total - done))
			try:
				h, stream = fut.result()
			except Exception:
				continue
			if not stream:
				continue
			if _lang_rank(h) == 0:
				winner = (h, stream)
				break
			if best is None or _sortkey(h) < _sortkey(best[0]):
				best = (h, stream)
	if cancelled and _TOKEN is not None:
		_TOKEN.cancel()
	stop.set()
	_abandon(ex, futs)
	if cancelled:
		return None, None
	if winner:
		return winner
	if best:
		return best
	return None, None


def _resolve_host(host):
	resolver = host.get("resolver")
	try:
		if resolver == "vavoo_chain":

			from streamhub.vjackson import resolve as vavoo_resolve
			mirror = {"url": host["url"]}
			url = vavoo_resolve(mirror)
			if not url:
				_host_protected(host)
				return None
			if "|" in url:
				u, hdr = url.split("|", 1)
				return u, hdr
			return url, None
		if resolver == "direct":
			url = host.get("url")
			if not url:
				return None
			ref = host.get("referer") or url
			return url, "Referer=%s&User-Agent=%s" % (quote(ref, safe=""), quote(UA, safe=""))
		return _resolve_embed(host)
	except Exception:
		log(format_exc())
	return None




_HLS_RE = re.compile(r'https?://[^\s\'"<>]+\.m3u8[^\s\'"<>]*')
_PACKED_RE = re.compile(r"eval\(function\(p,a,c,k,e,(?:r|d)\)")
_ATOB_RE = re.compile(r"atob\(\s*[\"']([A-Za-z0-9+/=_-]{40,})[\"']\s*\)")


def _resolve_external(url, referer=None):
	"""Use installed host plugins before fetching the same embed again."""
	try:
		import resolveurl
		# Referer forwarding is part of ResolveURL's documented embed API.
		target = url + ("$$" + referer if referer and referer != url else "")
		media = resolveurl.HostedMediaFile(url=target, include_disabled=False,
		                                 include_popups=False)
		if not media.valid_url():
			return None
		resolved = media.resolve(allow_popups=False)
		if isinstance(resolved, str) and resolved:
			return split_stream_url(resolved)
	except ImportError:
		pass
	except Exception:
		log("optional host resolver failed for %s" % (urlparse(url).hostname or "?"))
	return None


def _resolve_embed(host):
	url = host.get("url", "")
	ref = host.get("referer") or url
	try:
		from streamhub.resolvers import resolve as vendor_resolve, is_blocked
		if _host_protected(host) or is_blocked(url):
			return None
		res = vendor_resolve(url, ref, generic=False)
		if res:
			return res
		if _host_protected(host) or is_blocked(url):
			return None
	except Exception:
		log(format_exc())
	res = _resolve_external(url, ref)
	if res:
		return res
	try:
		from streamhub.host_support import resolve_public_embed
		return resolve_public_embed(url, ref)
	except Exception:
		log(format_exc())
	return None



def _start_playback(movie, host, stream):
	url, headers = stream
	o = ListItem(movie.get("title", "Movie"))
	o.setProperty("IsPlayable", "true")
	url = configure_playback(o, url, headers)
	info_tag = ListItemInfoTag(o, 'video')
	info_tag.set_info({
		"title": movie.get("title", ""),
		"plot": "[B]Quelle:[/B] %s\n%s" % (
			host.get("label", "?"), movie.get("title", "")
		),
	})
	try:
		from streamhub import resume
		resume.apply_resume(o, movie)
		resume.announce_playing(movie)
	except Exception:
		log(format_exc())
	play_directly(url, o)
