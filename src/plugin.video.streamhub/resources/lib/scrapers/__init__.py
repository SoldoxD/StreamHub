from streamhub.utils import getSetting, log, format_exc

from lib.scrapers.base import (
	BaseScraper, CancelToken, Cancelled, NULL_TOKEN, kodi_token,
	sort_sources, score_source, detect_quality, prefilter_dead,
	head_alive, cache_clear,
)

_REGISTRY = []


def register(cls):
	if cls not in _REGISTRY:
		_REGISTRY.append(cls)
	return cls


def _load():
	if _REGISTRY:
		return
	for mod in ("vavoo", "movie4k", "filmpalast", "kinox", "ramoflix", "movies67",
	            "hdfilme", "streamkiste", "serienstream", "movie2k", "einschalten"):
		try:
			__import__("lib.scrapers." + mod)
		except Exception:
			log("scrapers: failed to load %s\n%s" % (mod, format_exc()))


def all_scrapers():
	_load()
	return list(_REGISTRY)


def get_scrapers(media_type="movie", token=None):
	out = []
	for cls in all_scrapers():
		if media_type not in getattr(cls, "media_types", ()):
			continue
		key = getattr(cls, "enabled_setting", None)
		if key and getSetting(key) != "true":
			continue
		try:
			out.append(cls(token=token))
		except Exception:
			log("scrapers: cannot init %s\n%s" % (cls, format_exc()))
	return out
