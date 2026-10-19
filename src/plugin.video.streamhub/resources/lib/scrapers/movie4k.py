import re

from urllib.parse import urlencode, urlparse

from lib.scrapers import register
from lib.scrapers.base import BaseScraper
from streamhub.source_selection import select_diverse

LANG_DE = 2
LANG_EN = 3

_DEAD_HOSTS = ("mystream.to", "upstream.to", "vidlox", "streamz", "flashx",
               "vivo.sx", "openload", "rapidvideo", "vshare", "gounlimited",
               "streamcloud.club")

_FAMILIES = (
	("voe", ("voe", "cloudwindow")),
	("doodstream", ("dood", "do7go", "d0000d", "d000d", "ds2play", "doods",
	                "all3do", "do0od", "ds2video", "dsvplay", "vidply", "playmogo")),
	("streamtape", ("streamtape", "stape", "streamadblock", "strtape",
	                "tapewithadblock", "streamta")),
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
	("highload", ("highload",)),
	("redload", ("redload",)),
)

_MAX_PER_FAMILY = 2
_MAX_TOTAL = 16
_ID_RE = re.compile(r"[A-Za-z0-9]{8,}")
_DIRECT_RE = re.compile(r"\.(mp4|mkv|m3u8|avi)(\?|$)", re.I)
_SEASON_RE = re.compile(r"\s*-\s*Staffel\s*(\d+)\s*$", re.I)


def base_title(title):
	return _SEASON_RE.sub("", title or "").strip()


def family(host):
	for fam, needles in _FAMILIES:
		if any(n in host for n in needles):
			return fam
	return host.split(".")[0] or host


def family_rank(fam):
	for i, (f, _) in enumerate(_FAMILIES):
		if f == fam:
			return i
	return len(_FAMILIES)


def file_key(fam, url):
	path = urlparse(url).path.replace(".html", "")
	m = _ID_RE.search(path)
	return "%s:%s" % (fam, m.group(0).lower() if m else path.lower())


@register
class Movie4kScraper(BaseScraper):
	name = "movie4k"
	base_url = "https://movie4k.sx"
	media_types = ("movie", "tvshow")
	enabled_setting = "movie4k"

	def lang_code(self):
		try:
			from streamhub.movies import pref_lang
			return LANG_DE if (pref_lang() or "DE") == "DE" else LANG_EN
		except Exception:
			return LANG_DE

	def api(self, path, **params):
		params.setdefault("lang", self.lang_code())
		url = "%s/data/%s/?%s" % (self.base_url, path, urlencode(params))
		return self.fetch_json(url, headers=self.headers(
			**{"Accept": "application/json, text/plain, */*"}))

	def poster(self, rec):
		p = rec.get("poster_path") or rec.get("backdrop_path") or ""
		if p.startswith("/"):
			return "https://image.tmdb.org/t/p/w500" + p
		return p or rec.get("img_link") or None

	def search(self, query, media_type="movie"):
		data = self.api("search", keyword=query, page=1)
		if not isinstance(data, list):
			return []
		want_tv = media_type == "tvshow"
		if not want_tv:
			out = []
			for rec in data:
				if rec.get("tv") or not rec.get("_id"):
					continue
				out.append({"title": rec.get("title"), "year": rec.get("year"),
				            "poster": self.poster(rec), "provider": self.name,
				            "id": rec["_id"], "lang": rec.get("lang"),
				            "url": "%s/watch/%s" % (self.base_url, rec["_id"])})
			return out[:20]
		groups, order = {}, []
		for rec in data:
			if not rec.get("tv") or not rec.get("_id"):
				continue
			base = base_title(rec.get("title"))
			if not base:
				continue
			g = groups.get(base)
			if g is None:
				g = {"title": base, "year": rec.get("year"),
				     "poster": self.poster(rec), "provider": self.name,
				     "base": base, "total_seasons": 0, "seasons": {},
				     "url": "%s/tv/%s" % (self.base_url, base)}
				groups[base] = g
				order.append(base)
			try:
				sn = int(rec.get("s") or 0)
			except (TypeError, ValueError):
				sn = 0
			if sn:
				g["seasons"][str(sn)] = rec["_id"]
			g["total_seasons"] = max(g["total_seasons"],
			                         int(rec.get("totalSeasons") or 0), sn)
		return [groups[b] for b in order[:20]]

	def watch(self, mid):
		rec = self.api("watch", _id=mid)
		return rec if isinstance(rec, dict) else {}

	def build_sources(self, rec, episode=None):
		lang = "DE" if rec.get("lang") == LANG_DE else "OV"
		cands, seen = [], set()
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
			host = (urlparse(url).netloc or "").lower().replace("www.", "")
			if not host or any(d in host for d in _DEAD_HOSTS):
				continue
			fam = family(host)
			key = file_key(fam, url)
			if key in seen:
				continue
			seen.add(key)
			rank = -1 if _DIRECT_RE.search(urlparse(url).path) else family_rank(fam)
			cands.append((rank, fam, s.get("added") or "", host, url))
		cands.sort(key=lambda c: c[2], reverse=True)
		cands.sort(key=lambda c: (c[0], c[1]))
		out, per = [], {}
		for rank, fam, added, host, url in select_diverse(cands, _MAX_TOTAL, _MAX_PER_FAMILY):
			if per.get(fam, 0) >= _MAX_PER_FAMILY:
				continue
			per[fam] = per.get(fam, 0) + 1
			direct = bool(_DIRECT_RE.search(urlparse(url).path))
			s = self.make_source(url, host=host,
			                     label="%s%s (movie4k)" % (fam, " direct" if direct else ""),
			                     language=lang, quality_text=url)
			if s:
				s["added"] = added
				out.append(s)
			if len(out) >= _MAX_TOTAL:
				break
		return out

	def season_ids(self, item):
		base = item.get("base")
		known = dict(item.get("seasons") or {})
		total = int(item.get("total_seasons") or 0)
		missing = [n for n in range(1, total + 1) if str(n) not in known]
		if not missing or not base:
			return known
		from concurrent.futures import ThreadPoolExecutor

		def find(n):
			self.token.check()
			data = self.api("search", keyword="%s - Staffel %d" % (base, n), page=1)
			if not isinstance(data, list):
				return None
			for rec in data:
				if not rec.get("tv"):
					continue
				if base_title(rec.get("title")).lower() != base.lower():
					continue
				try:
					if int(rec.get("s") or 0) != n:
						continue
				except (TypeError, ValueError):
					continue
				return (n, rec.get("_id"))
			return None

		with ThreadPoolExecutor(max_workers=min(6, len(missing))) as ex:
			for res in ex.map(find, missing):
				if res and res[1]:
					known[str(res[0])] = res[1]
		return known

	def get_details(self, item):
		if not item.get("base"):
			return {}
		ids = self.season_ids(item)
		seasons = []
		for n in sorted(int(k) for k in ids):
			seasons.append({"season_number": n, "name": "Staffel %d" % n,
			                "id": ids[str(n)]})
		return {"seasons": seasons, "season_ids": ids}

	def get_sources(self, item):
		if item.get("base"):
			ids = item.get("season_ids") or self.season_ids(item)
			sid = ids.get(str(item.get("season")))
			if not sid:
				return []
			rec = self.watch(sid)
			return self.build_sources(rec, episode=item.get("episode"))
		mid = item.get("id")
		if not mid:
			return []
		return self.build_sources(self.watch(mid))

	def source_key(self, item):
		return "%s|%s|%s|%s" % (item.get("id") or item.get("base") or "",
		                        item.get("season") if item.get("season") is not None else "", item.get("episode") or "",
		                        self.lang_code())
