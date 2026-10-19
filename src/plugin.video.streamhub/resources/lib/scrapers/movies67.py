from lib.scrapers import register
from lib.scrapers.base import BaseScraper

_EMBEDS = (
	("111movies", "https://111movies.com/movie/%s", "https://111movies.com/tv/%s/%s/%s"),
	("videasy", "https://player.videasy.net/movie/%s", "https://player.videasy.net/tv/%s/%s/%s"),
	("vidsrc", "https://vidsrc.cc/v2/embed/movie/%s", "https://vidsrc.cc/v2/embed/tv/%s/%s/%s"),
)


@register
class Movies67Scraper(BaseScraper):
	name = "67movies"
	base_url = "https://67movies.net"
	language = "OV"
	media_types = ("movie", "tvshow")
	enabled_setting = "movies67"
	virtual = True

	def search(self, query, media_type="movie"):
		return []

	def get_sources(self, item):
		tmdb = item.get("tmdb_id")
		if not tmdb:
			return []
		season = item.get("season")
		episode = item.get("episode")
		out = []
		for name, movie_tpl, tv_tpl in _EMBEDS:
			if season not in (None, "") and episode:
				url = tv_tpl % (tmdb, season, episode)
			else:
				url = movie_tpl % tmdb
			s = self.make_source(url, host=name,
			                     label="%s (67movies)" % name,
			                     referer=self.base_url + "/")
			if s:
				out.append(s)
		return out

	def source_key(self, item):
		return "%s|%s|%s" % (item.get("tmdb_id"), item.get("season") if item.get("season") is not None else "",
		                     item.get("episode") or "")
