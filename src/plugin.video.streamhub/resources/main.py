



if __name__ == "__main__":
	from streamhub.utils import *
	from streamhub.provider_defaults import apply as apply_provider_defaults
	apply_provider_defaults()
	from streamhub import vjackson, stalker, vavoo_tv, vjlive, soccer, movies, series, sport
	params = dict(parse_qsl(sys.argv[2][1:]))
	tv = params.get("name")
	action = params.pop("action", None)
	actions = {
		"choose": lambda: vavoo_tv.choose(),
		"get_genres": lambda: stalker.get_genres(),
		"choose_portal": lambda: stalker.choose_portal(),
		"new_mac": lambda: stalker.new_mac(),
		"clear": lambda: clear(),
		"buffer_status": lambda: _buffer_status(),
		"host_support_status": lambda: _host_support_status(),
		"toggle_watched": lambda: _toggle_watched(params),
		"resume_remove": lambda: _resume_remove(params),
		"delete_search": lambda: delete_search(params),
		"channels": lambda: vjlive.channels(params.get('items'), params.get('type'), params.get('group')),
		"settings": lambda: openSettings(sys.argv[1]),
		"favchannels": lambda: vjlive.favchannels(),
		"makem3u": lambda: vjlive.makem3u(),
		"dlhd_clear_cache": lambda: (_remove_cache_entry("dlhd_channels.json", "dlhd_channels"), dialog.notification("StreamHub", "DLHD-Cache geleert", time=2000)),
		"soccer_index": lambda: soccer.index(params),
		"soccer_day":   lambda: soccer.day(params),
		"soccer_play":  lambda: soccer.play(params),
		"sport_index":  lambda: sport.index(params),
		"sport_list":   lambda: sport.listing(params),
		"sport_play":   lambda: sport.play(params),
		"movies_index": lambda: movies.index(params),
		"movie_search": lambda: movies.search(params),
		"movie_play":   lambda: movies.play(params),
		"movie_search_del":   lambda: (history_remove(movies.HIST_BUCKET, params.get("q", "")), execute("Container.Refresh")),
		"movie_search_clear": lambda: (history_clear(movies.HIST_BUCKET), execute("Container.Refresh")),
		"series_search_del":   lambda: (history_remove(series.HIST_BUCKET, params.get("q", "")), execute("Container.Refresh")),
		"series_search_clear": lambda: (history_clear(series.HIST_BUCKET), execute("Container.Refresh")),
		"series_search":   lambda: series.search(params),
		"series_seasons":  lambda: series.seasons(params),
		"series_episodes": lambda: series.episodes(params),
		"series_play":     lambda: series.play(params),
		"check_update":    lambda: _manual_update_check(),
	}
	def _resume_remove(params):
		try:
			from streamhub import resume
			resume.clear(params.get("key", ""))
			execute("Container.Refresh")
		except Exception:
			log(format_exc())

	def _toggle_watched(params):
		try:
			from streamhub import resume
			mv = json.loads(params.get("movie", "{}"))
			now = resume.toggle_watched(resume.key_for(mv), mv.get("title", ""))
			dialog.notification("StreamHub",
			                    "Als gesehen markiert" if now else "Gesehen-Markierung entfernt",
			                    time=2500)
			execute("Container.Refresh")
		except Exception:
			log(format_exc())

	def _host_support_status():
		try:
			available = getCondV("System.AddonIsEnabled(script.module.resolveurl)")
			version = xbmcaddon.Addon("script.module.resolveurl").getAddonInfo("version") if available else ""
		except Exception:
			available, version = False, ""
		status = ("ResolveURL aktiv: " + version if available else
		          "ResolveURL nicht aktiv. Das optionale Kodi-Modul erweitert die Hoster-Unterstützung.")
		dialog.ok("StreamHub - Hoster-Unterstützung",
		          "Integriert: Doodstream, Dropload, VOE, Filemoon, Streamtape, Mixdrop und weitere.\n"
		          "Erweiterte Player-Erkennung: StreamWish/StreamHG, FileLions/VidHide, LuluStream, Uqload.\n\n"
		          + status + "\nEinzelne Seiten können sich ändern oder den Zugriff blockieren.")

	def _buffer_status():
		try:
			from streamhub import buffer
			buffer.apply_if_changed()
			buffer.show_status()
		except Exception:
			log(format_exc())
			dialog.notification("StreamHub", "Puffer-Status fehlgeschlagen", time=3000)

	def _manual_update_check():
		try:
			sys.path.insert(0, os.path.join(addonpath, "resources"))
			import service as _svc
			if not _svc.check_for_update(force=True):
				dialog.notification("StreamHub", "Bereits aktuell", time=3000)
		except Exception:
			log(format_exc())
			dialog.notification("StreamHub", "Update-Pruefung fehlgeschlagen", time=3000)
	if tv:
		if action == "addTvFavorit": vjlive.change_favorit(tv)
		elif action == "delTvFavorit": vjlive.change_favorit(tv, True)
		else: vjlive.livePlay(tv, params.get('type'), params.get('group'))
	elif action is None:
		vjackson.menu(params)
	elif action == "delallTvFavorit":
		setSetting("favs", "[]")
		execute('Container.Refresh')
	elif action in actions:
		actions[action]()
	else:
		handler = getattr(vjackson, action, None)
		if callable(handler) and not action.startswith("_"):
			handler(params)
		else:
			log("Unbekannte action: %s" % action)
			vjackson.menu(params)
