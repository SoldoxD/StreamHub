import re

from html import unescape
from urllib.parse import quote

from lib.scrapers import register
from lib.scrapers.base import BaseScraper

_TITLE_RE = re.compile(
	r'<h2[^>]*>\s*<a[^>]+href="(?://)?(?:filmpalast\.to)?(/stream/[^"]+)"'
	r'[^>]*title="([^"]+)"', re.I)
_POSTER_RE = re.compile(
	r'<a[^>]+href="(?://)?(?:filmpalast\.to)?(/stream/[^"]+)"[^>]*>\s*'
	r'<img[^>]+src="([^"]+)"', re.I)
_BLOCK_RE = re.compile(
	r'<ul class="currentStreamLinks">'
	r'.*?<p class="hostName">([^<]+)</p>'
	r'.*?<a[^>]+href="(https?://[^"]+)"', re.S | re.I)
_TV_SLUG_RE = re.compile(r"-s\d{1,2}e\d{1,2}\b", re.I)


@register
class FilmpalastScraper(BaseScraper):
	name = "filmpalast"
	base_url = "https://filmpalast.to"
	language = "DE"
	media_types = ("movie",)
	enabled_setting = "filmpalast"

	def search(self, query, media_type="movie"):
		html = self.fetch_text(self.base_url + "/search/title/" + quote(query))
		if not html:
			return []
		posters = {m.group(1): m.group(2) for m in _POSTER_RE.finditer(html)}
		out, seen = [], set()
		for m in _TITLE_RE.finditer(html):
			path, title = m.group(1), unescape(m.group(2)).strip()
			if path in seen or _TV_SLUG_RE.search(path):
				continue
			seen.add(path)
			poster = posters.get(path)
			if poster and poster.startswith("/"):
				poster = self.base_url + poster
			out.append({"title": title, "year": None, "poster": poster,
			            "provider": self.name, "url": self.base_url + path})
			if len(out) >= 20:
				break
		return out

	def get_sources(self, item):
		page = item.get("url")
		if not page:
			return []
		html = self.fetch_text(page)
		if not html:
			return []
		out, seen = [], set()
		for m in _BLOCK_RE.finditer(html):
			hoster, url = m.group(1).strip(), m.group(2).strip()
			if url in seen:
				continue
			seen.add(url)
			s = self.make_source(url, label="%s (filmpalast)" % hoster,
			                     referer=page, quality_text=hoster)
			if s:
				out.append(s)
		return out
