import json
import re

from html import unescape
from urllib.parse import quote

from lib.scrapers import register
from lib.scrapers.base import BaseScraper

_CARD_RE = re.compile(
	r'<a[^>]+href="(https?://ramoflix\.net/[^"/]+/)"[^>]*>\s*'
	r'<img[^>]*?(?:data-src="([^"]+)"[^>]*?)?alt="([^"]+)"',
	re.I)

_SERVERS_RE = re.compile(r'var\s+Servers\s*=\s*(\{.*?\})\s*;', re.S)

_SKIP_URL = ("/category/", "/genre/", "/cast/", "/director/", "/year/",
             "/tv-show", "/series", "/episode")
_SKIP_TITLE = ("staffel", "season", "episode", " s0")

# A ramoflix movie page carries `var Servers = {...}` with the embed links,
# a series page carries `var Episodes = ...` instead. That is the only
# reliable type marker - the search listing itself looks identical for both.
_KIND_TTL = 86400
_KIND_BYTES = 30000

_META_KEYS = {"post_id", "id", "imdb_id", "image", "vote_average", "site",
              "domain", "youtube_id", "autoembed", "premium"}

_SERVER_LABELS = {
	"embedru": "soap2night",
	"vidlink": "videasy",
	"superembed": "vidfast",
	"vidsrc": "vidking",
	"vidsrc2": "vidzee",
	"movieclub": "111movies",
	"svetacdn": "svetacdn",
	"openvids": "openvids",
}


@register
class RamoflixScraper(BaseScraper):
	name = "ramoflix"
	base_url = "https://ramoflix.net"
	language = "DE"
	media_types = ("movie",)
	enabled_setting = "ramoflix"

	def page_kind(self, url):
		"""'movie', 'series', 'other', or None when it could not be determined."""
		from lib.scrapers.base import cache_get, cache_set
		ck = "k|ramoflix|" + url
		hit = cache_get(ck)
		if hit is not None:
			return hit
		try:
			r = self.fetch(url, max_bytes=_KIND_BYTES)
			html = (r.text or "") if r is not None else ""
		except Exception:
			html = ""
		if not html:
			return None
		if "var Episodes" in html:
			kind = "series"
		elif "var Servers" in html:
			kind = "movie"
		else:
			kind = "other"
		cache_set(ck, kind, _KIND_TTL)
		return kind

	def search(self, query, media_type="movie"):
		html = self.fetch_text(self.base_url + "/?s=" + quote(query))
		if not html:
			return []
		cands, seen = [], set()
		for m in _CARD_RE.finditer(html):
			url, poster, title = m.group(1), m.group(2), unescape(m.group(3)).strip()
			if url in seen:
				continue
			seen.add(url)
			low = url.lower()
			if any(h in low for h in _SKIP_URL):
				continue
			if any(h in title.lower() for h in _SKIP_TITLE):
				continue
			cands.append({"title": title, "year": None, "poster": poster,
			              "provider": self.name, "url": url})
			if len(cands) >= 20:
				break
		if not cands:
			return []

		from concurrent.futures import ThreadPoolExecutor
		want = "series" if media_type == "tvshow" else "movie"
		with ThreadPoolExecutor(max_workers=min(8, len(cands))) as ex:
			kinds = list(ex.map(lambda c: self.page_kind(c["url"]), cands))
		out = []
		for c, k in zip(cands, kinds):
			# unknown (network hiccup) -> keep, so a hiccup never empties results
			if k is None or k == want:
				out.append(c)
		return out

	def _servers(self, page_url):
		html = self.fetch_text(page_url)
		if not html:
			return {}
		m = _SERVERS_RE.search(html)
		if not m:
			return {}
		try:
			return json.loads(m.group(1))
		except Exception:
			return {}

	def get_details(self, item):
		data = self._servers(item.get("url"))
		return {"tmdb_id": data.get("id"), "imdb_id": data.get("imdb_id")}

	def get_sources(self, item):
		data = self._servers(item.get("url"))
		if not data:
			return []
		sources = []
		for key, val in data.items():
			if key in _META_KEYS or not isinstance(val, str):
				continue
			if not val.startswith("http"):
				continue
			label = _SERVER_LABELS.get(key, key)
			s = self.make_source(val, label="%s (ramoflix)" % label,
			                     referer=item.get("url"))
			if s:
				s["tmdb_id"] = data.get("id")
				s["imdb_id"] = data.get("imdb_id")
				sources.append(s)
		return sources
