from html import unescape
from urllib.parse import urlparse

from streamhub.utils import getAuthSignature, request_json, log

from lib.scrapers import register
from lib.scrapers.base import BaseScraper

_API = "https://vavoo.to/mediahubmx-source.json"


def _norm_lang(langs):
	try:
		from streamhub.movies import _norm_lang as nl
		return nl(langs)
	except Exception:
		return (langs or ["de"])[0].upper()


@register
class VavooScraper(BaseScraper):
	name = "vavoo"
	base_url = "https://vavoo.to"
	media_types = ("movie", "tvshow")
	enabled_setting = "vavoo"

	def _catalog(self, query, kind):
		from streamhub.vjackson import callApi2
		q = (query or "").replace(".", "%2E")
		res = callApi2("list", {"id": "%s.popular.search=%s" % (kind, q)})
		items = res.get("data") if isinstance(res, dict) else res
		return items or []

	def search(self, query, media_type="movie"):
		kind = "series" if media_type == "tvshow" else "movie"
		try:
			items = self._catalog(query, kind)
		except Exception:
			log("%s: catalog search failed" % self.name)
			return []
		out = []
		for it in items[:20]:
			tid = it.get("id", "")
			tmdb = tid.split(".")[1] if tid.startswith(kind + ".") and "." in tid else None
			out.append({"title": unescape(it.get("name", "")),
			            "year": str(it.get("year", "") or ""),
			            "poster": it.get("poster"),
			            "plot": it.get("description"),
			            "provider": self.name,
			            "tmdb_id": tmdb,
			            "id": tid,
			            "url": "%s/%s" % (self.base_url, tid)})
		return out

	def get_sources(self, item):
		tmdb = item.get("tmdb_id")
		if not tmdb:
			return []
		season = item.get("season")
		episode = item.get("episode")
		data = {
			"language": "de", "region": "AT",
			"type": "series" if season not in (None, "") else "movie",
			"ids": {"tmdb_id": tmdb},
			"name": item.get("title", ""),
			"clientVersion": "3.0.2",
		}
		if season not in (None, "") and episode:
			data["episode"] = {"season": str(season), "episode": str(episode)}
		headers = {
			"user-agent": "MediaHubMX/2",
			"content-type": "application/json; charset=utf-8",
			"accept-encoding": "gzip",
			"mediahubmx-signature": getAuthSignature(),
		}
		self.token.check()
		try:
			mirrors = request_json("POST", _API, json=data, headers=headers,
			                       timeout=8, retries=1) or []
		except Exception:
			log("%s: source lookup failed" % self.name)
			return []
		out = []
		for a in mirrors:
			url = a.get("url")
			if not url:
				continue
			host = (urlparse(url).netloc or "?").lower()
			if "streamz" in host:
				continue
			quality = a.get("tag", "") or ""
			lang = _norm_lang(a.get("languages") or ["de"]) or "DE"
			s = self.make_source(url, host=host,
			                     label="%s%s (vavoo)" % (host, (" " + quality) if quality else ""),
			                     resolver="vavoo_chain",
			                     language=lang, quality_text=quality)
			if s:
				out.append(s)
		return out

	def source_key(self, item):
		return "%s|%s|%s" % (item.get("tmdb_id"), item.get("season") if item.get("season") is not None else "",
		                     item.get("episode") or "")
