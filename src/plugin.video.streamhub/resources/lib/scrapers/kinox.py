import json
import re

from html import unescape
from urllib.parse import quote, urljoin, urlparse

from lib.scrapers import register
from lib.scrapers.base import BaseScraper

_RESULT_RE = re.compile(
	r'<a[^>]*\btitle="([^"]*)"[^>]*\bhref="(/Stream/([^"/]+)\.html)"', re.I)
_RESULT_ALT_RE = re.compile(
	r'<a[^>]*\bhref="(/Stream/([^"/]+)\.html)"[^>]*\btitle="([^"]*)"', re.I)

_HOSTER_RE = re.compile(
	r'<li[^>]*id="Hoster_(\d+)"[^>]*rel="([^"]+)"[\s\S]{0,120}?'
	r'<div class="Named">([^<]+)</div>'
	r'[\s\S]{0,160}?<b>Vom</b>:\s*([\d.]+)', re.I)

_IFRAME_RE = re.compile(r'src="(/redirect/[^"]+)"')
_XOR_RE = re.compile(r'var\s+_\w+\s*=\s*"([^"]*)"\s*,\s*_\w+\s*=\s*"([0-9a-fA-F]{20,})"')
_TV_HINT = re.compile(r"\b(staffel|season)\b", re.I)


def _xor_payload(html):
	m = _XOR_RE.search(html)
	if not m:
		return ""
	key, hexs = m.group(1), m.group(2)
	if not key:
		return ""
	try:
		return "".join(
			chr(int(hexs[i:i + 2], 16) ^ ord(key[(i // 2) % len(key)]))
			for i in range(0, len(hexs), 2))
	except Exception:
		return ""


@register
class KinoxScraper(BaseScraper):
	name = "kinox"
	base_url = "https://kinox.to"
	language = "DE"
	media_types = ("movie",)
	enabled_setting = "kinox"
	delay_range = (0.0, 0.2)
	timeout = 6
	retries = 1

	def search(self, query, media_type="movie"):
		html = self.fetch_text(self.base_url + "/Search.html?q=" + quote(query))
		if not html:
			return []
		out, seen = [], set()
		for rx, order in ((_RESULT_RE, "th"), (_RESULT_ALT_RE, "ht")):
			for m in rx.finditer(html):
				if order == "th":
					title, path, slug = m.group(1), m.group(2), m.group(3)
				else:
					path, slug, title = m.group(1), m.group(2), m.group(3)
				title = unescape(title or "").strip() or slug.replace("_", " ")
				if path in seen or not title:
					continue
				seen.add(path)
				if _TV_HINT.search(title):
					continue
				out.append({"title": title, "year": None, "poster": None,
				            "provider": self.name,
				            "url": urljoin(self.base_url, path),
				            "slug": slug})
				if len(out) >= 20:
					break
			if out:
				break
		return out

	def _hosters(self, page_url):
		html = self.fetch_text(page_url)
		if not html:
			return []
		out = []
		for m in _HOSTER_RE.finditer(html):
			hid, rel, named, date = m.groups()
			out.append({"id": hid, "rel": unescape(rel),
			            "name": named.strip(), "date": date.strip()})
		return out

	def _mirror_target(self, rel, page_url):
		body = self.fetch_text(
			"%s/aGET/Mirror/%s" % (self.base_url, rel),
			headers=self.headers(referer=page_url,
			                     **{"X-Requested-With": "XMLHttpRequest"}))
		if not body:
			return None
		try:
			data = json.loads(body)
		except Exception:
			return None
		stream = data.get("Stream") or ""
		m = _IFRAME_RE.search(stream)
		if not m:
			return None
		url = urljoin(self.base_url, m.group(1))
		for _ in range(2):
			self.token.check()
			html = self.fetch_text(url, headers=self.headers(referer=page_url))
			if not html:
				return None
			js = _xor_payload(html)
			if js and "reload" in js:
				continue
			ext = self._external(html)
			if ext:
				return ext
		return None

	def _external(self, html):
		for m in re.finditer(r'(?:href|src|location(?:\.href)?\s*=\s*)["\']?'
		                     r'(https?://[^\s"\'<>]+)', html):
			u = m.group(1)
			host = (urlparse(u).netloc or "").lower()
			if "kinox" in host or not host:
				continue
			if any(b in host for b in ("google", "facebook", "twitter", "jsdelivr",
			                           "gstatic", "cloudflare", "doubleclick")):
				continue
			return u
		return None

	def get_sources(self, item):
		page = item.get("url")
		if not page:
			return []
		hosters = self._hosters(page)
		if not hosters:
			return []
		sources = []
		for h in hosters:
			self.token.check()
			target = self._mirror_target(h["rel"], page)
			if not target:
				continue
			s = self.make_source(target,
			                     label="%s (kinox)" % h["name"],
			                     referer=page,
			                     quality_text=h["name"])
			if s:
				s["added"] = h.get("date")
				sources.append(s)
		return sources
