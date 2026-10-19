from streamhub.utils import log, format_exc

from lib.scrapers import get_scrapers
from lib.scrapers.base import Cancelled


class MovieAdapter:
	def __init__(self, scraper):
		self._s = scraper
		self.name = scraper.name
		self.virtual = scraper.virtual

	def search(self, query, max_results=20):
		out = []
		for r in self._s.cached_search(query, "movie")[:max_results]:
			m = dict(r)
			m.setdefault("tmdb_id", None)
			m["provider"] = self.name
			out.append(m)
		return out

	def get_hosts(self, movie):
		item = dict(movie)
		item["provider"] = self.name
		try:
			return self._s.cached_sources(item)
		except Cancelled:
			raise
		except Exception:
			log("%s: get_hosts failed\n%s" % (self.name, format_exc()))
			return []


class SeriesAdapter:
	def __init__(self, scraper):
		self._s = scraper
		self.name = scraper.name
		self.virtual = scraper.virtual

	def search(self, query, max_results=20):
		out = []
		for r in self._s.cached_search(query, "tvshow")[:max_results]:
			m = dict(r)
			m.setdefault("tmdb_id", None)
			m["provider"] = self.name
			out.append(m)
		return out

	def get_seasons(self, series):
		try:
			det = self._s.get_details(dict(series)) or {}
		except Cancelled:
			raise
		except Exception:
			return []
		out = []
		for s in det.get("seasons") or []:
			out.append({"season_number": s.get("season_number"),
			            "name": s.get("name") or "Staffel %s" % s.get("season_number"),
			            "episode_count": s.get("episode_count"),
			            "poster": series.get("poster"),
			            "_id": s.get("id")})
		return out

	def get_episodes(self, series, season_number):
		reader = getattr(self._s, "get_episodes", None)
		try:
			if callable(reader):
				return reader(dict(series), season_number) or []
			count = self.get_episode_count(series, season_number)
			return [{"episode_number": number} for number in range(1, count + 1)]
		except Cancelled:
			raise
		except Exception:
			log("%s: episodes failed\n%s" % (self.name, format_exc()))
			return []

	def get_episode_count(self, series, season_number):
		reader = getattr(self._s, "get_episodes", None)
		if callable(reader):
			return len(self.get_episodes(series, season_number))
		item = dict(series)
		item["season"] = season_number
		try:
			det = self._s.get_details(item) or {}
			ids = det.get("season_ids") or {}
			sid = ids.get(str(season_number))
			if not sid:
				return 0
			rec = self._s.watch(sid)
		except Cancelled:
			raise
		except Exception:
			return 0
		eps = set()
		for s in rec.get("streams") or []:
			try:
				eps.add(int(s.get("e")))
			except (TypeError, ValueError):
				continue
		return max(eps) if eps else 0

	def get_episode_hosts(self, series, season, episode):
		item = dict(series)
		item["season"] = season
		item["episode"] = episode
		try:
			return self._s.cached_sources(item)
		except Cancelled:
			raise
		except Exception:
			log("%s: episode hosts failed\n%s" % (self.name, format_exc()))
			return []


def movie_providers(token=None):
	return [MovieAdapter(s) for s in get_scrapers("movie", token=token)]


def series_providers(token=None):
	out = []
	for s in get_scrapers("tvshow", token=token):
		if not hasattr(s, "get_details"):
			continue
		out.append(SeriesAdapter(s))
	return out
