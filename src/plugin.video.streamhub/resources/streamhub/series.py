
from streamhub.utils import *

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _ua_headers(referer=None, **extra):
	h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
	if referer:
		h["Referer"] = referer
	h.update(extra)
	return h


def _normalize_title(t):
	t = re.sub(r"[^\w\s]", " ", t or "")
	t = re.sub(r"\s+", " ", t).strip().lower()
	return t




class VavooSeriesProvider:
	name = "vavoo"

	def search(self, query, max_results=20):
		from html import unescape
		try:
			from streamhub.vjackson import callApi2
			q = query.replace(".", "%2E")
			res = callApi2("list", {"id": "series.popular.search=" + q})
			items = res.get("data") if isinstance(res, dict) else res
			if not items:
				return []
		except Exception as e:
			log("vavoo series search failed: %s" % e)
			return []
		out = []
		for it in items[:max_results]:
			tid = it.get("id", "")
			tmdb = tid.split(".")[1] if tid.startswith("series.") and "." in tid else None
			out.append({"tmdb_id": tmdb,
			            "title": unescape(it.get("name", "")),
			            "year": str(it.get("year", "") or ""),
			            "poster": it.get("poster"),
			            "plot": it.get("description"),
			            "provider": self.name,
			            "vavoo_id": tid})
		return out

	def _vid(self, series):
		tmdb = series.get("tmdb_id")
		if not tmdb:
			return None
		return series.get("vavoo_id") or ("series.%s" % tmdb)

	def get_seasons(self, series):
		vid = self._vid(series)
		if not vid:
			return []
		meta = get_meta({"id": vid})
		if not isinstance(meta, dict):
			return []
		seasons = meta.get("seasons") or []
		out = []


		poster_base = "https://image.tmdb.org/t/p/w342"
		for s in seasons:
			pp = s.get("poster_path")
			out.append({
				"season_number": s.get("season_number"),
				"name": s.get("name") or "Staffel %s" % s.get("season_number"),
				"episode_count": s.get("episode_count"),
				"poster": (poster_base + pp) if pp else series.get("poster"),
			})
		return out

	def get_episode_count(self, series, season_number):
		vid = self._vid(series)
		if not vid:
			return 0
		meta = get_meta({"id": vid, "s": str(season_number)})
		if not isinstance(meta, dict):
			return 0
		return int(meta.get("infos", {}).get("sortepisode") or 0)

	def get_episode_hosts(self, series, season, episode):
		tmdb = series.get("tmdb_id")
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
				"language": "de", "region": "AT", "type": "series",
				"ids": {"tmdb_id": tmdb},
				"name": series.get("title", ""),
				"episode": {"season": str(season), "episode": str(episode)},
				"clientVersion": "3.0.2",
			}
			mirrors = request_json(
				"POST", "https://vavoo.to/mediahubmx-source.json",
				json=_data, headers=_headers, timeout=10, retries=1) or []
		except Exception as e:
			log("vavoo episode hosts failed: %s" % e)
			return []
		from streamhub.movies import _norm_lang
		hosts = []
		for a in mirrors:
			url = a.get("url")
			if not url:
				continue
			host = urlparse(url).netloc or "?"
			if "streamz" in host:
				continue
			q = a.get("tag", "") or ""
			lang = _norm_lang(a.get("languages") or ["de"]) or "DE"
			label = "%s%s (vavoo)" % (host, (" " + q) if q else "")
			hosts.append({
				"provider": self.name,
				"host": host,
				"label": label,
				"url": url,
				"resolver": "vavoo_chain",
				"quality": q,
				"lang": lang,
			})
		return hosts


class Movies67SeriesProvider:
	name = "67movies"
	virtual = True

	def search(self, query, max_results=20):
		return []

	def get_seasons(self, series):
		return []

	def get_episode_count(self, series, season_number):
		return 0

	def get_episode_hosts(self, series, season, episode):
		tmdb = series.get("tmdb_id")
		if not tmdb:
			return []
		embed = "https://111movies.net/tv/%s/%s/%s" % (tmdb, season, episode)
		return [{
			"provider": self.name,
			"host": "111movies",
			"label": "111movies (TMDB %s S%sE%s)" % (tmdb, season, episode),
			"url": embed,
			"resolver": "embed_generic",
			"referer": "https://67movies.net/",
			"lang": "OV",
		}]


_M4K_SEASON_RE = re.compile(r"\s*-\s*Staffel\s*(\d+)\s*$", re.I)


def _m4k_base_title(title):
	return _M4K_SEASON_RE.sub("", title or "").strip()


class Movie4kSeriesProvider:
	name = "movie4k"

	def search(self, query, max_results=20):
		from streamhub.movies import m4k_api, m4k_poster
		try:
			data = m4k_api("search", keyword=query, page=1)
		except Exception as e:
			log("movie4k series search failed: %s" % e)
			return []
		if not isinstance(data, list):
			return []
		groups = {}
		order = []
		for rec in data:
			if not rec.get("tv") or not rec.get("_id"):
				continue
			base = _m4k_base_title(rec.get("title"))
			if not base:
				continue
			g = groups.get(base)
			if g is None:
				g = {"tmdb_id": None,
				     "title": base,
				     "year": rec.get("year") or None,
				     "poster": m4k_poster(rec),
				     "plot": rec.get("storyline") or rec.get("overview"),
				     "provider": self.name,
				     "movie4k_base": base,
				     "movie4k_total": int(rec.get("totalSeasons") or 0),
				     "movie4k_seasons": {}}
				groups[base] = g
				order.append(base)
			try:
				sn = int(rec.get("s") or 0)
			except (TypeError, ValueError):
				sn = 0
			if sn:
				g["movie4k_seasons"][str(sn)] = rec["_id"]
			g["movie4k_total"] = max(g["movie4k_total"], int(rec.get("totalSeasons") or 0), sn)
		return [groups[b] for b in order[:max_results]]

	def _season_ids(self, series):
		base = series.get("movie4k_base")
		if not base:
			return {}
		cached = memcache_get("m4k_seasons", base, None)
		if isinstance(cached, dict) and cached:
			return cached
		known = dict(series.get("movie4k_seasons") or {})
		total = int(series.get("movie4k_total") or 0)
		missing = [n for n in range(1, total + 1) if str(n) not in known]
		if missing:
			from streamhub.movies import m4k_api

			def _find(n):
				try:
					data = m4k_api("search", keyword="%s - Staffel %d" % (base, n), page=1)
				except Exception:
					return None
				if not isinstance(data, list):
					return None
				for rec in data:
					if not rec.get("tv"):
						continue
					if _m4k_base_title(rec.get("title")).lower() != base.lower():
						continue
					try:
						if int(rec.get("s") or 0) != n:
							continue
					except (TypeError, ValueError):
						continue
					return (n, rec.get("_id"))
				return None

			with ThreadPoolExecutor(max_workers=min(6, len(missing))) as ex:
				for res in ex.map(_find, missing):
					if res and res[1]:
						known[str(res[0])] = res[1]
		if known:
			memcache_set("m4k_seasons", base, known, ttl=1800, maxsize=40)
		return known

	def _watch(self, sid):
		from streamhub.movies import m4k_api
		cached = memcache_get("m4k_watch", sid, None)
		if isinstance(cached, dict) and cached:
			return cached
		try:
			rec = m4k_api("watch", _id=sid)
		except Exception as e:
			log("movie4k season watch failed: %s" % e)
			return {}
		if not isinstance(rec, dict):
			return {}
		slim = {"lang": rec.get("lang"),
		        "streams": [s for s in (rec.get("streams") or []) if not s.get("deleted")]}
		memcache_set("m4k_watch", sid, slim, ttl=900, maxsize=8)
		return slim

	def get_seasons(self, series):
		ids = self._season_ids(series)
		out = []
		for n in sorted(int(k) for k in ids):
			out.append({"season_number": n,
			            "name": "Staffel %d" % n,
			            "episode_count": None,
			            "poster": series.get("poster")})
		return out

	def get_episode_count(self, series, season_number):
		ids = self._season_ids(series)
		sid = ids.get(str(season_number))
		if not sid:
			return 0
		rec = self._watch(sid)
		eps = set()
		for s in rec.get("streams") or []:
			try:
				eps.add(int(s.get("e")))
			except (TypeError, ValueError):
				continue
		return max(eps) if eps else 0

	def get_episode_hosts(self, series, season, episode):
		from streamhub.movies import m4k_streams
		ids = self._season_ids(series)
		sid = ids.get(str(season))
		if not sid:
			return []
		rec = self._watch(sid)
		if not rec:
			return []
		return m4k_streams(rec, episode=episode)


def _enabled_providers():
	provs = []
	if getSetting("vavoo") == "true":
		provs.append(VavooSeriesProvider())
	if getSetting("movie4k") == "true":
		provs.append(Movie4kSeriesProvider())
	if getSetting("movies67") == "true":
		provs.append(Movies67SeriesProvider())
	return provs




def _merge_results(per_provider):
	from streamhub.movies import identity_keys_show, _may_merge
	by_key = {}
	merged_order = []
	for provider, results in per_provider:
		for m in results:
			norm = _normalize_title(m.get("title", ""))
			if not norm:
				continue
			keys = identity_keys_show(m.get("title", ""))
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
					"_hits": [],
				}
				merged_order.append(entry)
			entry["_hits"].append((provider, m))
			for k in keys:
				by_key.setdefault(k, entry)
			for k in ("tmdb_id", "year", "poster", "plot"):
				if not entry.get(k) and m.get(k):
					entry[k] = m[k]
			entry["title"] = _better_show_title(entry.get("title"), m.get("title"))
	return merged_order


def _has_season_marker(title):
	from streamhub.movies import _title_parts
	return _title_parts(title)[1] is not None


def _better_show_title(cur, new):
	"""For a show, a title without a season marker beats one with it;
	otherwise prefer the more descriptive (longer) one."""
	if not cur:
		return new or ""
	if not new:
		return cur
	cs, ns = _has_season_marker(cur), _has_season_marker(new)
	if cs != ns:
		return new if cs else cur
	return new if len(new) > len(cur) else cur


HIST_BUCKET = "series_search"


def search(params):
	q = params.get("q")
	if q:
		history_add(HIST_BUCKET, q)
		_search_and_list(q)
		return
	if params.get("new") == "true":
		kb = xbmc.Keyboard("", "StreamHub - Serien Suche", False)
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
	set_category("Serien - Suche")
	addDir2("[B][COLOR lime]Neue Suche[/COLOR][/B]", "DefaultAddonsSearch",
	        "series_search", new="true")
	items = history_load(HIST_BUCKET)
	for q in items:
		cm = [("Diesen Eintrag löschen",
		       "RunPlugin(%s?action=series_search_del&q=%s)" % (
			       sys.argv[0], quote(q, safe=""))),
		      ("Suchverlauf löschen",
		       "RunPlugin(%s?action=series_search_clear)" % sys.argv[0])]
		addDir2(q, "DefaultAddonsSearch", "series_search", context=cm, q=q)
	if items:
		addDir2("[COLOR red]Suchverlauf löschen[/COLOR]", "DefaultAddonsSearch",
		        "series_search_clear", isFolder=False)
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
		                    "Keine Serien für: %s" % query, time=3000)
		return
	set_content("tvshows")
	set_category("Serien-Suche: %s" % query)
	prov_objs = _enabled_providers()
	for m in merged:
		providers_hit = {p for p, _ in m["_hits"]}
		for p in prov_objs:
			if getattr(p, "virtual", False) and m.get("tmdb_id"):
				providers_hit.add(p.name)
		badge = "+".join(p[:1].upper() for p in sorted(providers_hit))
		year_part = " (%s)" % m["year"] if m.get("year") else ""
		label = "%s%s  [COLOR grey](%s)[/COLOR]" % (
			m.get("title", "?"), year_part, badge)
		o = ListItem(label)
		if m.get("poster"):
			o.setArt({"poster": m["poster"], "thumb": m["poster"]})
		info_year = None
		if m.get("year"):
			y = str(m["year"]).strip()
			if y.isdigit():
				info_year = int(y)
		info_tag = ListItemInfoTag(o, 'video')
		info_tag.set_info({
			"title": m.get("title", ""),
			"year": info_year,
			"plot": (m.get("plot") or "") +
			        "\n\n[B]Quellen:[/B] " + ", ".join(sorted(providers_hit)),
		})
		params = {
			"action": "series_seasons",
			"series": json.dumps({
				"title": m.get("title", ""),
				"year": m.get("year"),
				"tmdb_id": m.get("tmdb_id"),
				"poster": m.get("poster"),
				"plot": m.get("plot"),
				"hits": [{"provider": pn, **mv} for pn, mv in m["_hits"]],
			}),
		}
		add(params, o, True)
	end()




_META_PROVIDERS = (("vavoo", VavooSeriesProvider), ("movie4k", Movie4kSeriesProvider))


def _meta_hit(series):
	hits = series.get("hits", [])
	for name, cls in _META_PROVIDERS:
		for h in hits:
			if h.get("provider") == name:
				return h, cls()
	return None, None


def seasons(params):
	try:
		series = json.loads(params.get("series", "{}"))
	except Exception:
		showFailedNotification("Series-Daten ungültig")
		return

	meta_hit, provider = _meta_hit(series)
	if not meta_hit:
		dialog.notification("StreamHub",
		                    "Keine Saison-Metadaten verfügbar",
		                    time=4000)
		return
	seasons_list = provider.get_seasons(meta_hit)
	if not seasons_list:
		dialog.notification("StreamHub", "Keine Staffeln gefunden", time=3000)
		return

	try:
		from streamhub import resume
		nu = resume.next_up(series.get("tmdb_id"), series.get("title"))
		if nu:
			verb = "Fortsetzen" if nu["resume"] else "Weiter mit"
			o = ListItem("[COLOR deepskyblue]▶ %s S%sE%s[/COLOR]" %
			             (verb, nu["season"], nu["episode"]))
			o.setProperty("IsPlayable", "true")
			if series.get("poster"):
				o.setArt({"poster": series["poster"], "thumb": series["poster"]})
			ListItemInfoTag(o, 'video').set_info({"title": "%s S%sE%s" %
			                                      (verb, nu["season"], nu["episode"])})
			add({"action": "series_play", "series": json.dumps(series),
			     "season": nu["season"], "episode": nu["episode"]}, o, False)
	except Exception:
		log(format_exc())

	if len(seasons_list) == 1:
		s = seasons_list[0]
		_list_episodes(series, meta_hit, s.get("season_number"))
		return
	set_content("seasons")
	set_category(series.get("title", "Serie"))
	for s in seasons_list:
		sn = s.get("season_number")
		label = s.get("name") or "Staffel %s" % sn
		if s.get("episode_count"):
			label = "%s  [COLOR grey](%s Episoden)[/COLOR]" % (label, s["episode_count"])
		o = ListItem(label)
		if s.get("poster"):
			o.setArt({"poster": s["poster"], "thumb": s["poster"]})
		info_tag = ListItemInfoTag(o, 'video')
		info_tag.set_info({
			"title": label,
			"season": int(sn) if sn is not None else None,
		})
		params = {
			"action": "series_episodes",
			"series": json.dumps(series),
			"season": str(sn),
		}
		add(params, o, True)
	end()




def episodes(params):
	try:
		series = json.loads(params.get("series", "{}"))
	except Exception:
		showFailedNotification("Series-Daten ungültig")
		return
	season = params.get("season")
	if not season:
		showFailedNotification("Staffel fehlt")
		return
	meta_hit, _prov = _meta_hit(series)
	if not meta_hit:
		showFailedNotification("Keine Staffel-Metadaten")
		return
	_list_episodes(series, meta_hit, season)


def _list_episodes(series, meta_hit, season):
	_, provider = _meta_hit(series)
	if provider is None:
		provider = VavooSeriesProvider()
	count = provider.get_episode_count(meta_hit, season)
	if count <= 0:
		dialog.notification("StreamHub", "Keine Episoden gefunden", time=3000)
		return
	set_content("episodes")
	set_category("%s - Staffel %s" % (series.get("title", ""), season))
	for ep in range(1, count + 1):
		label = "S%sE%s" % (str(season).zfill(2), str(ep).zfill(2))
		o = ListItem(label)
		o.setProperty("IsPlayable", "true")
		if series.get("poster"):
			o.setArt({"poster": series["poster"], "thumb": series["poster"]})
		info_tag = ListItemInfoTag(o, 'video')
		_info = {
			"title": label,
			"season": int(season) if str(season).isdigit() else None,
			"episode": ep,
			"tvshowtitle": series.get("title", ""),
		}
		try:
			from streamhub import resume
			_info = resume.decorate(o, {"tmdb_id": series.get("tmdb_id"),
			                            "title": series.get("title"),
			                            "season": str(season), "episode": str(ep)},
			                        _info)
		except Exception:
			pass
		info_tag.set_info(_info)
		params = {
			"action": "series_play",
			"series": json.dumps(series),
			"season": str(season),
			"episode": str(ep),
			"ep_count": str(count),
		}
		add(params, o, False)
	end()




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


def _sortkey(h):
	from streamhub.movies import _sortkey as msk
	return msk(h)


def _gather_episode_hosts(series, season, episode):
	provs = _enabled_providers()
	hits_by_provider = {}
	for p in provs:
		hit = None
		for h in series.get("hits", []):
			if h.get("provider") == p.name:
				hit = h
				break
		if hit is None and getattr(p, "virtual", False) and series.get("tmdb_id"):
			hit = series
		if hit is not None:
			hits_by_provider[p.name] = (p, hit)
	all_hosts = []
	if hits_by_provider:
		from streamhub import movies
		ex = ThreadPoolExecutor(max_workers=max(len(hits_by_provider), 1))
		futs = {ex.submit(p.get_episode_hosts, hit, season, episode): pname
		        for pname, (p, hit) in hits_by_provider.items()}
		try:
			for fut in as_completed(futs, timeout=movies._GATHER_DEADLINE):
				if movies._cancelled():
					break
				try:
					all_hosts.extend(fut.result() or [])
				except Exception as e:
					if movies._is_cancelled(e):
						break
					log(format_exc())
		except Exception:
			log("episode gather: deadline reached, using %d hosts" % len(all_hosts))
		movies._abandon(ex, futs)
		if movies._cancelled():
			return []
	all_hosts.sort(key=_sortkey)
	return all_hosts


def play(params):
	try:
		series = json.loads(params.get("series", "{}"))
	except Exception:
		fail_resolved("Series-Daten ungültig")
		return
	season = params.get("season")
	episode = params.get("episode")
	if not season or not episode:
		fail_resolved("Episoden-Daten fehlen")
		return

	manual = params.get("manual") == "true"
	host_select = getSetting("movies_host_select") == "true" or manual
	deep_check = getSetting("movies_deep_check") == "true"

	from streamhub import movies
	prog = StatusProgress("StreamHub")
	heading = "%s S%sE%s" % (series.get("title", ""), season, episode)
	try:
		from lib.scrapers.base import kodi_token
		movies.set_token(kodi_token(prog))
	except Exception:
		pass
	prog.step(5, "Suche Hoster …", heading)

	cache_key = {"_ep_hosts": series.get("tmdb_id") or series.get("title", ""),
	             "s": season, "e": episode}
	cacheOk, cached_hosts = get_cache(cache_key)
	if cacheOk and isinstance(cached_hosts, list) and cached_hosts:
		all_hosts = cached_hosts
		prog.step(20, "Hoster aus Cache", heading)
	else:
		try:
			all_hosts = _gather_episode_hosts(series, season, episode)
		except Exception as e:
			if movies._is_cancelled(e):
				prog.close()
				fail_resolved()
				return
			raise
		if all_hosts:
			set_cache(cache_key, all_hosts, timeout=1)

	if not all_hosts:
		prog.close()
		fail_resolved("Keine Hoster für diese Episode")
		return

	ep_movie = {
		"title": "%s - S%sE%s" % (series.get("title", ""), season, episode),
		"year": series.get("year"),
		"poster": series.get("poster"),
		"plot": series.get("plot"),
		"tmdb_id": series.get("tmdb_id"),
		"season": season,
		"episode": episode,
	}
	try:
		from streamhub import resume
		ep_count = 0
		try:
			ep_count = int(params.get("ep_count") or 0)
		except (TypeError, ValueError):
			ep_count = 0
		resume.save_playmeta(
			resume.key_for(ep_movie), "episode",
			{"action": "series_play", "series": params.get("series", "{}"),
			 "season": str(season), "episode": str(episode)},
			title=ep_movie["title"], poster=series.get("poster", ""),
			season=str(season), episode=str(episode),
			episode_count=ep_count or None)
	except Exception:
		log(format_exc())
	movies.choose_and_play(ep_movie, all_hosts, heading, prog,
	                       host_select, deep_check)
