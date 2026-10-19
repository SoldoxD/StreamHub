import json
import os
import re
import time

from streamhub.utils import translatePath, log, home, format_exc, getSetting

_DIR = translatePath("special://profile/addon_data/plugin.video.streamhub/")
_FILE = os.path.join(_DIR, "resume.json")
_PROP = "streamhub_playing"

_MAX = 500
_WATCHED_PCT = 0.90
_MIN_RESUME = 30
_END_MARGIN = 60

_NORM_RE = re.compile(r"[^a-z0-9]+")


def enabled():
	return getSetting("resume_enabled") != "false"


def _norm(s):
	return _NORM_RE.sub("", (s or "").lower())[:60]


def key_for(item):
	tmdb = str(item.get("tmdb_id") or "").strip()
	s = item.get("season") or item.get("_s")
	e = item.get("episode") or item.get("_e")
	if tmdb and tmdb.lower() != "none":
		base = "t" + tmdb
	else:
		base = "n" + _norm(item.get("title") or item.get("name") or "")
	if not base or base in ("t", "n"):
		return ""
	if s and e:
		base += ":s%se%s" % (s, e)
	return base


def _load():
	try:
		with open(_FILE, "r", encoding="utf-8") as f:
			d = json.load(f)
			return d if isinstance(d, dict) else {}
	except Exception:
		return {}


def _save(store):
	try:
		if len(store) > _MAX:
			old = sorted(store, key=lambda k: store[k].get("ts", 0))
			for k in old[:len(store) - _MAX]:
				store.pop(k, None)
		if not os.path.isdir(_DIR):
			os.makedirs(_DIR)
		tmp = _FILE + ".tmp"
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(store, f)
		os.replace(tmp, _FILE)
	except Exception:
		log("resume save failed\n%s" % format_exc())


def get(key):
	if not key:
		return None
	return _load().get(key)


def resume_seconds(key):
	ent = get(key)
	if not ent or ent.get("watched"):
		return 0
	pos = int(ent.get("pos") or 0)
	return pos if pos >= _MIN_RESUME else 0


def is_watched(key):
	ent = get(key)
	return bool(ent and ent.get("watched"))


def record(key, pos, total, title=""):
	if not key:
		return
	pos = max(0, int(pos or 0))
	total = int(total or 0)
	store = _load()
	ent = store.get(key) or {}
	# keep the best known total (never overwrite a real duration with 0)
	ent["total"] = total or int(ent.get("total") or 0)
	ent["title"] = title or ent.get("title", "")
	ent["ts"] = int(time.time())
	tot = ent["total"]
	if tot > 0 and (pos >= tot * _WATCHED_PCT or pos >= tot - _END_MARGIN):
		ent["watched"] = True
		ent["pos"] = 0
	else:
		ent["watched"] = ent.get("watched", False)
		ent["pos"] = pos
	store[key] = ent
	_save(store)


def mark_watched(key, title=""):
	if not key:
		return
	store = _load()
	ent = store.get(key) or {}
	ent["watched"] = True
	ent["pos"] = 0
	ent["title"] = title or ent.get("title", "")
	ent["ts"] = int(time.time())
	store[key] = ent
	_save(store)


def clear(key):
	if not key:
		return
	store = _load()
	if store.pop(key, None) is not None:
		_save(store)


def toggle_watched(key, title=""):
	if is_watched(key):
		clear(key)
		return False
	mark_watched(key, title)
	return True


def save_playmeta(key, kind, params, title="", poster="",
                  season=None, episode=None, episode_count=None):
	"""Remember enough to re-launch this title from Continue Watching and to
	auto-advance to the next episode. Merges into the existing entry."""
	if not key or not enabled():
		return
	try:
		store = _load()
		ent = store.get(key) or {}
		ent["kind"] = kind
		ent["params"] = params
		ent["title"] = title or ent.get("title", "")
		if poster:
			ent["poster"] = poster
		if season is not None:
			ent["season"] = season
		if episode is not None:
			ent["episode"] = episode
		if episode_count is not None:
			ent["episode_count"] = episode_count
		ent.setdefault("ts", int(time.time()))
		store[key] = ent
		_save(store)
	except Exception:
		log("resume save_playmeta failed\n%s" % format_exc())


def continue_list(limit=40, kind=None):
	if kind == "series":
		kind = "episode"
	items = []
	for key, ent in _load().items():
		if ent.get("watched"):
			continue
		if int(ent.get("pos") or 0) < _MIN_RESUME:
			continue
		if not ent.get("params"):
			continue
		if kind and ent.get("kind") != kind:
			continue
		items.append((key, ent))
	items.sort(key=lambda kv: kv[1].get("ts", 0), reverse=True)
	return items[:limit]


def get_meta(key):
	return _load().get(key) or {}


def _se(ent):
	try:
		return (int(ent.get("season") or 0), int(ent.get("episode") or 0))
	except (TypeError, ValueError):
		return (0, 0)


def next_up(tmdb_id, title=None):
	"""Return the episode to resume/continue for a series, or None."""
	if tmdb_id and str(tmdb_id).lower() != "none":
		prefix = "t%s:s" % tmdb_id
	elif title:
		prefix = "n%s:s" % _norm(title)
	else:
		return None
	entries = [ent for key, ent in _load().items()
	           if key.startswith(prefix) and ent.get("kind") == "episode"]
	if not entries:
		return None
	inprog = [e for e in entries
	          if not e.get("watched") and int(e.get("pos") or 0) >= _MIN_RESUME]
	if inprog:
		inprog.sort(key=lambda e: e.get("ts", 0), reverse=True)
		e = inprog[0]
		return {"season": str(_se(e)[0]), "episode": str(_se(e)[1]), "resume": True}
	watched = [e for e in entries if e.get("watched")]
	if not watched:
		return None
	watched.sort(key=_se)
	last = watched[-1]
	s, ep = _se(last)
	cnt = int(last.get("episode_count") or 0)
	from streamhub.episode_navigation import next_episode_number
	available = (last.get("params") or {}).get("episode_numbers")
	next_number = next_episode_number(ep, cnt, available)
	if next_number is not None:
		return {"season": str(s), "episode": str(next_number), "resume": False}
	if available is not None:
		return None
	if cnt and ep >= cnt:
		return {"season": str(s + 1), "episode": "1", "resume": False}
	return None


def announce_playing(item):
	try:
		key = key_for(item)
		if not key:
			return
		payload = {"key": key,
		           "title": item.get("title") or item.get("name") or "",
		           "resume": resume_seconds(key)}
		home.setProperty(_PROP, json.dumps(payload))
	except Exception:
		log("resume announce failed\n%s" % format_exc())


def apply_resume(o, item):
	"""Set ResumeTime/TotalTime on the resolved ListItem so Kodi seeks."""
	if not enabled():
		return
	try:
		ent = get(key_for(item))
		if not ent:
			return
		pos = resume_seconds(key_for(item))
		total = int(ent.get("total") or 0)
		if pos and total:
			o.setProperty("ResumeTime", str(pos))
			o.setProperty("TotalTime", str(total))
	except Exception:
		pass


def decorate(o, item, infos=None):
	"""Mark a browse ListItem: playcount if watched, resume props + label badge
	if partly watched. Returns the (possibly modified) infos dict."""
	if not enabled():
		return infos
	try:
		key = key_for(item)
		ent = get(key)
		if not ent:
			return infos
		total = int(ent.get("total") or 0)
		if ent.get("watched"):
			if isinstance(infos, dict):
				infos = dict(infos)
				infos["playcount"] = 1
		else:
			pos = resume_seconds(key)
			if pos and total:
				o.setProperty("ResumeTime", str(pos))
				o.setProperty("TotalTime", str(total))
				try:
					pct = int(100 * pos / total)
					lbl = o.getLabel()
					if lbl and "[COLOR" not in lbl:
						o.setLabel("[COLOR deepskyblue]▶[/COLOR] %s  [COLOR grey](%d%%)[/COLOR]" % (lbl, pct))
				except Exception:
					pass
	except Exception:
		pass
	return infos
