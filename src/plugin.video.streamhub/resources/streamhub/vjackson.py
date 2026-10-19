
from streamhub.utils import *
from streamhub.playback import configure_playback, split_stream_url, max_height


BASEURL = "https://vavoo.to/ccapi/"

def menu(params):
	if getSetting("fresh_start") != "false":
		freed = memcache_clear()
		if freed:
			log("addon reopened: cleared %d cached session buckets" % freed)
	set_content("files")
	try:
		from streamhub import resume
		if resume.enabled():
			n = len(resume.continue_list())
			label = "Weiterschauen" if not n else "Weiterschauen  [COLOR grey](%d)[/COLOR]" % n
			addDir2(label, "DefaultInProgressShows", "continue_watching")
	except Exception:
		log(format_exc())
	if getSetting("show_live") == "true":
		addDir2("Live", "DefaultAddonPVRClient", "live")
	addDir2("Filme", "DefaultMovies", "indexMovie")
	addDir2("Serien", "DefaultTVShows", "indexSerie")
	end()

def continue_watching(params):
	from streamhub import resume
	kind = params.get("kind")
	if not kind:
		set_content("files")
		set_category("Weiterschauen")
		nf = len(resume.continue_list(kind="movie"))
		ns = len(resume.continue_list(kind="series"))
		if not nf and not ns:
			o = ListItem("[COLOR grey]Noch nichts angefangen — starte einen Film oder eine Episode[/COLOR]")
			ListItemInfoTag(o, 'video').set_info({"title": "Weiterschauen"})
			add({"action": "indexMovie"}, o, True)
			end()
			return
		addDir2("Filme%s" % ("  [COLOR grey](%d)[/COLOR]" % nf if nf else ""),
		        "DefaultMovies", "continue_watching", kind="movie")
		addDir2("Serien%s" % ("  [COLOR grey](%d)[/COLOR]" % ns if ns else ""),
		        "DefaultTVShows", "continue_watching", kind="series")
		end()
		return
	set_content("episodes" if kind == "series" else "movies")
	set_category("Weiterschauen - %s" % ("Serien" if kind == "series" else "Filme"))
	entries = resume.continue_list(kind=kind)
	if not entries:
		o = ListItem("[COLOR grey]Nichts angefangen[/COLOR]")
		ListItemInfoTag(o, 'video').set_info({"title": "Weiterschauen"})
		add({"action": "continue_watching"}, o, True)
		end()
		return
	for key, ent in entries:
		total = int(ent.get("total") or 0)
		pos = int(ent.get("pos") or 0)
		title = ent.get("title") or "?"
		if total:
			label = "%s  [COLOR grey](%d%%)[/COLOR]" % (title, int(100 * pos / total))
		else:
			mins = pos // 60
			label = "%s  [COLOR grey](bei %d:%02d)[/COLOR]" % (title, mins, pos % 60)
		o = ListItem(label)
		o.setProperty("IsPlayable", "true")
		if ent.get("poster"):
			o.setArt({"poster": ent["poster"], "thumb": ent["poster"]})
		if total:
			o.setProperty("ResumeTime", str(pos))
			o.setProperty("TotalTime", str(total))
		info_tag = ListItemInfoTag(o, 'video')
		info_tag.set_info({"title": title})
		cm = [("StreamHub: aus Weiterschauen entfernen",
		       "RunPlugin(%s?action=resume_remove&key=%s)" % (
			       sys.argv[0], quote(key)))]
		o.addContextMenuItems(cm)
		add(ent.get("params") or {}, o, False)
	end()


def indexMovie(params):
	set_content("movies")
	addDir2("Suche", "DefaultAddonsSearch", "movie_search")
	addDir2("Beliebte Filme", "DefaultMovies", "show", id="movie.popular")
	addDir2("Angesagte Filme", "DefaultMovies", "show", id="movie.trending")
	addDir2("Genres", "DefaultGenre", "genres", id="movie.popular")
	end()

def indexSerie(params):
	set_content("tvshows")
	addDir2("Suche", "DefaultAddonsSearch", "series_search")
	addDir2("Beliebte Serien", "DefaultTVShows", "show", id="series.popular")
	addDir2("Angesagte Serien", "DefaultTVShows", "show", id="series.trending")
	addDir2("Genres", "DefaultGenre", "genres", id="series.popular")
	end()

def live(params):
	try: lines = json.loads(getSetting("favs"))
	except (TypeError, ValueError):
		lines = []
	if len(lines)>0: addDir2("Favoriten", "DefaultAddonPVRClient", "favchannels")
	addDir2("Kanäle", "DefaultAddonPVRClient", "channels")
	addDir2("Sport", "DefaultAddonPVRClient", "sport_index")
	end()

def group_tv(params):


	entries = []
	if getSetting("vavoo") == "true":
		try:
			from streamhub.vavoo_tv import vavoo_groups
			vgroups, _ = vavoo_groups()
			for g in vgroups:
				label = "VAVOO  %s" % g.encode().decode("ascii", errors="ignore")
				entries.append((label, "vavoo", g))
		except Exception:
			log(format_exc())
	entries.append(("DLHD  All channels", "dlhd", None))
	if getSetting("stalker") == "true":
		try:
			from streamhub.stalker import StalkerPortal
			portal = StalkerPortal(get_cache_or_setting("stalkerurl"), get_cache_or_setting("mac"))
			for title, gid in portal.genres().items():
				label = "STALKER  %s" % title.encode().decode("ascii", errors="ignore")
				entries.append((label, "stalker", gid))
		except Exception:
			log(format_exc())
	entries.sort(key=lambda e: e[0])
	for label, t, group in entries:
		if group is None:
			addDir2(label, "DefaultAddonPVRClient", "channels", type=t)
		else:
			addDir2(label, "DefaultAddonPVRClient", "channels", type=t, group=group)
	end()

def a_z_tv(params):
	from collections import defaultdict
	from streamhub import vjlive
	results = vjlive.getchannels()
	res = defaultdict(dict)
	for key, val in results.items():
		prefix, number = key[:1].upper() if key[:1].isalpha() else "#", key
		res[prefix][number] = val
	res = dict(sorted(res.items()))
	for key, val in res.items():
		addDir2(key, "DefaultAddonPVRClient", "channels", items=json.dumps(val))
	end()

def _multihost_payload(e):
	is_movie = e["id"].startswith("movie")
	tmdb = e["id"].split(".")[1] if "." in e["id"] else None
	year = e.get("year")
	year_str = str(year) if year else ""
	vavoo_hit = {
		"provider": "vavoo",
		"vavoo_id": e["id"],
		"tmdb_id": tmdb,
		"title": e.get("name", ""),
		"year": year_str,
		"poster": e.get("poster"),
		"plot": e.get("description"),
	}
	return is_movie, {
		"title": e.get("name", ""),
		"year": year_str,
		"tmdb_id": tmdb,
		"poster": e.get("poster"),
		"plot": e.get("description"),
		"hits": [vavoo_hit],
	}


def show(params):
	data = cachedcall("list", params)
	content, next = "seasons", data["next"]
	data = [i for i in data["data"] if i.get("description")]
	cat = "Beliebte Serien" if "popular" in params["id"] else "Angesagte Serien"
	if params["id"].startswith("movie"):
		cat = cat.replace("Serien", "Filme")
		content = "movies"
	set_content(content)
	set_category(cat)





	paramslist = []
	for e in data:
		is_movie, payload = _multihost_payload(e)
		base = {"id": e["id"], "n": e["name"]}
		if is_movie:
			base["action"] = "movie_play"
			base["movie"] = json.dumps(payload)
		else:
			base["action"] = "series_seasons"
			base["series"] = json.dumps(payload)
		paramslist.append(base)
	with ThreadPoolExecutor(max_workers=max(len(paramslist), 1)) as executor:
		future_to_url = {executor.submit(createListItem, urlparams):urlparams for urlparams in paramslist}
		for future in as_completed(future_to_url):
			urlparams = future_to_url[future]
			o = future.result()
			if o:
				isFolder = False if o.getProperty("IsPlayable") == "true" else True
				add(urlparams, o, isFolder)
	if next: addDir(">>> Weiter", {"action": "show", "id": next})
	end()


def search(params):
	type = "SERIEN" if params["id"].startswith("serie") else "FILM"
	cacheOk, history = get_cache("seriesearch" if type == "SERIEN" else "moviesearch")
	if not cacheOk: history = {}
	if not history or params.get("newsearch"):
		a = list(history.keys())[-1] if history else ""
		heading="VAVOO.TO - %s SUCHE" % type
		kb = xbmc.Keyboard(a, heading, False)
		kb.doModal()
		if (kb.isConfirmed()):
			query = kb.getText().replace(".", "%2E")
			para = "%s.search=%s" % (params["id"], query)
			history[query] = para
			set_cache("seriesearch" if type == "SERIEN" else "moviesearch", history, False)
			show({"id" : para})
		else: return
	else:
		addDir2("Neue Suche", "DefaultAddonsSearch", "search", id=params["id"], newsearch=True)
		for a in history:
			cm = [("Suchverlauf löschen", "RunPlugin(%s?action=delete_search&id=%s)" % (sys.argv[0], params["id"])), ("Suche löschen", "RunPlugin(%s?action=delete_search&id=%s&single=%s)" % (sys.argv[0], params["id"], a))]
			addDir2(a, "DefaultAddonsSearch", "show", context=cm, id=history[a])
		end()

def genres(params):
	serie_genrelist = [
		{"genre": "Action & Adventure", "icon":"Adventure"}, {"genre": "Animation", "icon":"Animation"}, {"genre": "Komödie", "icon":"Comedy"}, {"genre": "Krimi", "icon":"Crime"},
		{"genre": "Dokumentarfilm", "icon":"Documentary"}, {"genre": "Drama", "icon":"Drama"}, {"genre": "Familie", "icon":"Family"}, {"genre": "Kids", "icon":"Children"},
		{"genre": "Mystery", "icon":"Mystery"}, {"genre": "News", "icon":"News"}, {"genre": "Reality", "icon":"Reality-TV"}, {"genre": "Sci-Fi & Fantasy", "icon":"Sci-Fi"},
		{"genre": "Soap", "icon":"Sitcom"}, {"genre": "Talk", "icon":"Biography"}, {"genre": "War & Politics", "icon":"War"}, {"genre": "Western", "icon":"Western"}]
	movie_genrelist = [
		{"genre": "Action", "icon":"Action"}, {"genre": "Abenteuer", "icon":"Adventure"}, {"genre": "Animation", "icon":"Animation"}, {"genre": "Komödie", "icon":"Comedy"},
		{"genre": "Krimi", "icon":"Crime"}, {"genre": "Dokumentarfilm", "icon":"Documentary"}, {"genre": "Drama", "icon":"Drama"}, {"genre": "Familie", "icon":"Family"},
		{"genre": "Fantasy", "icon":"Fantasy"}, {"genre": "Historie", "icon":"History"}, {"genre": "Horror", "icon":"Horror"}, {"genre": "Musik", "icon":"Music"},
		{"genre": "Mystery", "icon":"Mystery"}, {"genre": "Liebesfilm", "icon":"Romance"}, {"genre": "Science Fiction", "icon":"Sci-Fi"}, {"genre": "TV-Film", "icon":"Mini-Series"},
		{"genre": "Thriller", "icon":"Thriller"}, {"genre": "Kriegsfilm", "icon":"War"}, {"genre": "Western", "icon":"Western"}]
	genrelist= serie_genrelist if params["id"].startswith("serie") else movie_genrelist
	for genre in genrelist: addDir2(genre["genre"], genre["icon"], "show", id="%s.genre=%s" % (params["id"], genre["genre"]))
	end()

def seasons(params):
	set_content("seasons")
	set_category("Staffeln")
	_seasons = get_meta(params)["seasons"]
	if len(_seasons) == 1:
		params["s"] = "1"
		episodes(params)
	params["action"] = "episodes"
	for season in _seasons:
		params["s"] = str(season["season_number"])
		o = createListItem(params)
		add(params, o, True)
	end()

def episodes(params):
	set_content("episodes")
	set_category("Staffel %s/Episoden" %params["s"])
	_episodes = get_meta(params)["infos"]["sortepisode"]
	params["action"], i = "get", 1
	while i <= _episodes:
		params["e"] = str(i)
		o = createListItem(params)
		o.addContextMenuItems([("Manuelle Stream Auswahl", "RunPlugin(%s&manual=true)" % url_for(params))])
		add(params, o)
		i+=1
	end()

def resolve(mirror):
	log(mirror, header="Try to resolve:")

	try:
		from streamhub.resolvers import resolve as vendor_resolve, get_resolve_failure
		res = vendor_resolve(mirror["url"], mirror.get("url"))
		if res:
			url, hdr = res
			return url + ("|" + hdr if hdr else "")
		if get_resolve_failure(mirror["url"]) == "protected":
			return None
	except Exception:
		log(format_exc())

	if resolveurl is not None:
		try:
			resolved = resolveurl.resolve(mirror["url"])
			if resolved:
				return resolved
		except Exception:
			log(format_exc())

	try:
		_headers={"user-agent": "MediaHubMX/2", "content-type": "application/json; charset=utf-8", "content-length": "102", "accept-encoding": "gzip", "mediahubmx-signature": getAuthSignature()}
		_data = {"language":"de","region":"AT","url":mirror["url"],"clientVersion":"3.0.2"}
		url = request_json("POST", "https://vavoo.to/mediahubmx-resolve.json", json=_data, headers=_headers, timeout=4, retries=0)["data"]["url"]
		try:
			from streamhub.resolvers import resolve as vendor_resolve, get_resolve_failure
			res = vendor_resolve(url, mirror.get("url"))
			if res:
				u, hdr = res
				return u + ("|" + hdr if hdr else "")
			if get_resolve_failure(url) == "protected":
				return None
		except Exception:
			log(format_exc())
		if resolveurl is not None:
			resolved = resolveurl.resolve(url)
			if resolved:
				return resolved
	except Exception:
		log(format_exc())

	try:
		res = callApi2('open', {'link': mirror["url"]})
		if not res:
			raise ValueError("callApi2 returned nothing")
		if isinstance(res, list):
			res = res[-1] if res else None
		if not isinstance(res, dict) or "url" not in res:
			raise ValueError("bad shape")
		headers = res.get('headers', {})
		resolved = request("GET", res['url'], headers=headers, max_bytes=0, timeout=4, retries=0).url
		if resolved:
			return resolved + ("|" + urlencode(headers) if headers else "")
	except Exception:
		log(format_exc())

	try:
		from streamhub.movies import _resolve_embed
		host = {"url": mirror["url"], "referer": mirror.get("url")}
		fallback = _resolve_embed(host)
		if fallback:
			url, hdr = fallback
			return url + ("|" + hdr if hdr else "")
	except Exception:
		log(format_exc())
	return None

def checkstream(url):
	if not url: return
	log(url, header="Checking Stream:")
	try:
		newurl, headers = split_stream_url(url)
		res = request("GET", newurl, headers=dict(parse_qsl(headers)),
		              max_bytes=0, timeout=10, retries=0)
		res.raise_for_status()
		if "text" in res.headers.get("Content-Type","text"): raise Exception("Keine Videodatei")
	except Exception:
		log(format_exc())
		return
	else: return url

def get(params):
	from streamhub.movies import _known_height
	manual = True if params.get("manual") == "true" else False
	find = True if params.get("find") == "true" else False
	params["site"] = "vavoo"
	cacheOk, mirrors = get_cache(params)
	_headers={"user-agent": "MediaHubMX/2", "content-type": "application/json; charset=utf-8", "content-length": "1898", "accept-encoding": "gzip", "mediahubmx-signature": getAuthSignature()}
	if not cacheOk:
		name = params.get("n")
		if not name:
			b = get_meta(params)
			if params.get("e"):
				name = b["infos"]["tvshowtitle"] if params.get("e") else b["infos"]["title"]
		if params.get("e"):_data={"language":"de","region":"AT","type":"series","ids":{"tmdb_id":params["id"].split(".")[1]},"name":name,"episode":{"season":params["s"],"episode":params["e"]},"clientVersion":"3.0.2"}
		else: _data={"language":"de","region":"AT","type":"movie","ids":{"tmdb_id":params["id"].split(".")[1]},"name":name,"episode":{},"clientVersion":"3.0.2"}
		url = "https://vavoo.to/mediahubmx-source.json"
		mirrors = request_json("POST", url, json=_data, headers=_headers, timeout=10, retries=1)
		set_cache(params, mirrors, 1)
	if not mirrors:
		log("Keine Mirrors gefunden")
		if not find: fail_resolved("Keine Mirrors gefunden")
		return
	else:
		newurllist, streamurl =[], None
		for i ,a in enumerate(mirrors, 1):
			if not "de" in a.get('languages', []): continue
			a["hoster"] = urlparse(a["url"]).netloc
			if "streamz" in a["hoster"]: continue
			quali = a.get("tag", "SD")
			if _known_height({"quality": quali}) > max_height(): continue
			if quali == "1080p" or quali == "FHD":
				a["name"], a["weight"] = "%s %s" %(a["hoster"], "1080p"), 1080+random.randint(0, 9)
			elif quali == "720p" or quali == "HD":
				a["name"], a["weight"] = "%s %s" %(a["hoster"], "720p"), 720+random.randint(0, 9)
			else: a["name"], a["weight"] = a["hoster"], random.randint(0, 9)
			newurllist.append({"name":a["name"], "weight":a["weight"], "hoster": a["hoster"], "url":a["url"]})
		mirrors = list(sorted(newurllist, key=lambda x: x["weight"], reverse=True)) if newurllist else None
		if not mirrors:
			log("Keine Mirrors gefunden")
			if not find: fail_resolved("Keine Mirrors gefunden")
			return





		for i, a in enumerate(mirrors, 1): a["name"] = "%s. %s" % (i, a["name"])
		log(mirrors)
		if manual or getSetting("movies_host_select") == "true":
			index = dialog.select("StreamHub - Hoster wählen", [ mirror["name"] for mirror in mirrors ])
			if index == -1:
				fail_resolved()
				return
			mirrors = mirrors[index:]
		for mirror in mirrors:
			streamurl = resolve(mirror)
			if streamurl: break
		if find and streamurl: return streamurl
		elif not streamurl:
			if find: return
			fail_resolved("Stream not resolvable or down")
			return
		else:
			log("Spiele :%s" % streamurl)
			o = ListItem(getInfoLabel("ListItem.Title"))
			o.setProperty("IsPlayable", "true")
			streamurl = configure_playback(o, streamurl)
			if int(sys.argv[1]) > 0: set_resolved(o)
			else:
				from streamhub.player import cPlayer
				player().play(streamurl, o)
				return cPlayer().startPlayer()

def cachedcall(action, params, timeout=24):
	cacheOk, content = get_cache(params)
	if cacheOk: return content
	else:
		content = callApi2(action, params)
		set_cache(params, content, timeout=timeout)
		return content

def callApi(action, params, method="GET", headers=None, **kwargs):
	log(params, header="Action:%s params:" % action)
	if not headers: headers = dict()
	headers["auth-token"] = getAuthSignature()
	resp = request(method, (BASEURL + action), params=params, headers=headers, retries=1, timeout=10, **kwargs)
	resp.raise_for_status()
	data = resp.json()
	log(data, header="callApi res:")
	return data

def callApi2(action, params):
	res = callApi(action, params, verify=False)
	while True:
		if type(res) is not dict or "id" not in res or "data" not in res:
			return res
		data = res["data"]
		if type(data) is dict and data.get("type") == "fetch":
			fetch_params = data.get("params", {})
			body = fetch_params.get("body")
			headers = fetch_params.get("headers")
			decoded_body = base64.b64decode(body) if body else None
			try:
				resp = request(
					fetch_params.get("method", "GET").upper(),
					data["url"],
					headers={k: v[0] if type(v) in (list, tuple) else v for k, v in headers.items()} if headers else None,
					data=decoded_body,
					max_bytes=None if body else 0,
					allow_redirects=fetch_params.get("redirect", "follow") == "follow",
					timeout=10,
					retries=1
				)
			except Exception:
				log(format_exc())
				return
			headers = dict(resp.headers)
			resData = {"status": resp.status_code, "url": resp.url, "headers": headers, "data": base64.b64encode(resp.content).decode("utf-8").replace("\n", "") if body else None}
			log(resData)
			res = callApi("res", {"id": res["id"]}, method="POST", json=resData, verify=False)
		elif type(data) is dict and data.get("error"):
			log(data.get("error"))
			return
		else: return data
