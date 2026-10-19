
from streamhub.utils import *
from streamhub.playback import configure_playback, split_stream_url, max_height

chanicons = ['13thstreet.png', '3sat.png', 'animalplanet.png', 'anixe.png', 'ard.png', 'ardalpha.png', 'arte.png', 'atv.png', 'atv2.png', 'automotorsport.png', 'axnblack.png', 'axnwhite.png', 'br.png', 'cartoonito.png', 'cartoonnetwork.png', 'comedycentral.png', 'curiositychannel.png', 'fix&foxi.png', 'dazn1.png', 'dazn2.png', 'deluxemusic.png', 'nationalgeographic.png', 'dmax.png', 'eurosport1.png', 'eurosport2.png', 'nickjunior.png', 'superrtl.png', 'heimatkanal.png', 'history.png', 'hr.png', 'jukebox.png', 'kabel1doku.png', 'pro7.png', 'pro7maxx.png', 'pro7fun.png', 'rtl2.png', 'kika.png', 'kinowelt.png', 'mdr.png', 'universaltv.png', 'discovery.png', 'mtv.png', 'n24doku.png', 'natgeowild.png', 'sky1.png', 'ndr.png', 'nickelodeon.png', 'nitro.png', 'romancetv.png', 'ntv.png', 'one.png', 'orf1.png', 'orf2.png', 'orf3.png', 'orfsportplus.png', 'phoenix.png', 'geotv.png', 'puls24.png', 'puls4.png', 'rbb.png', 'ric.png', 'motorvision.png', 'rtl.png', 'rtlcrime.png', 'rtlliving.png', 'kabel1.png', 'rtlpassion.png', 'rtlup.png', 'sat1.png', 'sat1emotions.png', 'sat1gold.png', 'servustv.png', 'silverline.png', 'sixx.png', 'skyatlantic.png', 'skycinemaaction.png', 'skycinemaclassics.png', 'skycinemafamily.png', 'skycinemahighlights.png', 'skycinemapremieren.png', 'skycrime.png', 'skydocumentaries.png', 'skykrimi.png', 'skynature.png', 'skyreplay.png', 'skyshowcase.png', 'spiegelgeschichte.png', 'kabel1classics.png', 'sport1.png', 'sportdigital.png', 'swr.png', 'syfy.png', 'tagesschau24.png', 'tele5.png', 'tlc.png', 'toggoplus.png', 'crime+investigation.png', 'vox.png', 'voxup.png', 'warnertvcomedy.png', 'warnertvfilm.png', 'warnertvserie.png', 'wdr.png', 'welt.png', 'weltderwunder.png', 'zdf.png', 'zdfinfo.png', 'zdfneo.png', 'zeeone.png', 'skycinemathriller.png']

def resolve_link(link):
	"""A bad live mirror must not prevent trying the remaining candidates."""
	if not isinstance(link, str) or not link:
		return None, None
	try:
		if link.startswith(("dlhd://", "dlhdppv://", "dlhdstreamed://")):
			from streamhub.dlhd import resolve_any
			return resolve_any(link)
		if "vavoo" not in link:
			from streamhub.stalker import StalkerPortal
			streamurl, headers = StalkerPortal(
				get_cache_or_setting("stalkerurl"), get_cache_or_setting("mac")).get_tv_stream_url(link)
		else:
			signature = getAuthSignature()
			if not signature:
				return None, None
			api_headers = {"user-agent": "MediaHubMX/2", "accept": "application/json",
			               "content-type": "application/json; charset=utf-8",
			               "mediahubmx-signature": signature}
			payload = {"language": "de", "region": "AT", "url": link, "clientVersion": "3.0.2"}
			result = request_json("POST", "https://vavoo.to/mediahubmx-resolve.json",
			                      json=payload, headers=api_headers, timeout=8, retries=0,
			                      max_bytes=256 * 1024)
			if not isinstance(result, list) or not result or not isinstance(result[0], dict):
				return None, None
			streamurl = result[0].get("url")
			headers = result[0].get("headers") or None
		if not isinstance(streamurl, str) or not streamurl:
			return None, None
		streamurl, encoded = split_stream_url(streamurl, headers)
		if urlparse(streamurl).scheme not in ("http", "https"):
			return None, None
		response = request("GET", streamurl, headers=dict(parse_qsl(encoded)),
		                   timeout=5, max_bytes=0, retries=0)
		if response.status_code < 400:
			return streamurl, encoded or None
	except Exception:
		log(format_exc())
	return None, None


def get_stalker_channels(genres=False):
	if genres == False: cacheOk, genres = get_cache("stalker_groups")
	from streamhub.stalker import StalkerPortal, get_genres, new_mac
	if not genres: genres = get_genres()
	cacheOk, chan = get_cache("sta_channels")
	if not cacheOk:
		url, mac = get_cache_or_setting("stalkerurl"), get_cache_or_setting("mac")
		if not url or not mac:
			dialog.notification('VAVOO.TO', 'Kein Stalkerportal gewählt, deaktiviere Stalker', xbmcgui.NOTIFICATION_ERROR, 2000)
			setSetting("stalker", "false")
			return {}
		portal = StalkerPortal(url, mac)
		check = portal.check()
		if check == True: cacheOk, chan = get_cache("sta_channels")
		elif check == "IP BLOCKED":
			dialog.notification('VAVOO.TO', 'IP BLOCKED anderes Portal auswählen, deaktiviere Stalker', xbmcgui.NOTIFICATION_ERROR, 2000)
			setSetting("stalker", "false")
			return {}
		else:
			m = new_mac(True)
			if m == False:
				dialog.notification('VAVOO.TO', 'Keine funktionierende Mac gefunden, anderes Portal auswählen, deaktiviere Stalker', xbmcgui.NOTIFICATION_ERROR, 2000)
				setSetting("stalker", "false")
				return {}
		cacheOk, chan = get_cache("sta_channels")
		if not cacheOk: return {}
	sta_channels = {}
	for item in chan:
		if item["tv_genre_id"] not in genres: continue
		name = item["name"].upper()
		if any(ele in name for ele in ["***", "###", "---"]): continue
		name = filterout(name)
		if not name: continue
		if name not in sta_channels: sta_channels[name] = []
		if item["cmd"] not in sta_channels[name]:
			sta_channels[name].append(item["cmd"])
	return sta_channels

def getchannels(type=None, group=None):
	if getSetting("stalker") == "true" and type in (None, "stalker"):
		allchannels = get_stalker_channels() if type is None else get_stalker_channels([group])
	else: allchannels = {}
	if getSetting("vavoo") == "true" and type in (None, "vavoo"):
		from streamhub.vavoo_tv import get_vav_channels
		vav_channels = get_vav_channels() if type is None else get_vav_channels([group])
	else: vav_channels = {}
	for k, v in vav_channels.items():
		if k not in allchannels: allchannels[k] = []
		for n in v:
			if n not in allchannels[k]:
				allchannels[k].append(n)
	if type in (None, "dlhd"):
		try:
			from streamhub.dlhd import get_channels as get_dlhd_channels
			dlhd_channels = get_dlhd_channels()
		except Exception:
			log(format_exc())
			dlhd_channels = {}
	else: dlhd_channels = {}
	for k, v in dlhd_channels.items():
		if k not in allchannels: allchannels[k] = []
		for n in v:
			if n not in allchannels[k]:
				allchannels[k].append(n)
	return allchannels

def _live_breakdown(candidates):
	from streamhub.hosters import _LIVE_SRC_COLOUR
	counts = {}
	for c in candidates:
		s = c.get("source") or "?"
		counts[s] = counts.get(s, 0) + 1
	return "   ".join(
		"[COLOR %s]%s %d[/COLOR]" % (_LIVE_SRC_COLOUR.get(s, "grey"), s, n)
		for s, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def livePlay(name, type=None, group=None):
	prog = StatusProgress("StreamHub")
	prog.step(5, "Lade Kanal …", name)
	m = getchannels(type, group).get(name)
	if not m:
		prog.close()
		fail_resolved("Kein Stream für %s" % name)
		return
	from streamhub.hosters import label_live_streams, filter_alive, live_display

	candidates = label_live_streams(m)
	prog.step(20, "%d Quellen gefunden" % len(candidates), name,
	          _live_breakdown(candidates))

	if getSetting("live_alive_check") == "true" and len(candidates) > 1:
		alive = filter_alive(candidates, timeout=5, prog=prog)
		if alive:
			candidates = alive
		else:
			log("live_alive_check: all candidates failed, falling back to full list")

	if prog.iscanceled() or monitor.abortRequested():
		prog.close()
		fail_resolved()
		return

	title = None
	host_select = getSetting("live_host_select") == "true"
	if len(candidates) > 1 and host_select:
		prog.close()
		labels = [live_display(c, n + 1) for n, c in enumerate(candidates)]
		i = selectDialog(labels, "Quelle wählen — %s" % name)
		if i < 0:
			fail_resolved()
			return
		prog = StatusProgress("StreamHub")
	else:
		i = 0
		if len(candidates) > 1:
			cacheOk, last = get_cache("last")
			if cacheOk and last.get("idn") == name:
				i = (last.get("num", -1) + 1) % len(candidates)
	if len(candidates) > 1:
		title = "%s [%s]" % (name, candidates[i]["label"])


	url, headers = None, None
	tried = 0
	while tried < len(candidates):
		if prog.iscanceled() or monitor.abortRequested():
			prog.close()
			fail_resolved()
			return
		prog.step(80, "Verbinde …", candidates[i].get("label", ""),
		          stats_line(pending=len(candidates) - tried))
		url, headers = resolve_link(candidates[i]["url"])
		if url: break
		log("resolve_link failed for %s, trying next" % candidates[i]["label"])
		i = (i + 1) % len(candidates)
		tried += 1
	if prog.iscanceled() or monitor.abortRequested():
		prog.close()
		fail_resolved()
		return
	prog.close()

	if not url:
		fail_resolved("Stream nicht abrufbar")
		return

	title = "%s [%s]" % (name, candidates[i]["label"]) if len(candidates) > 1 else name
	try:
		orig_i = m.index(candidates[i]["url"])
	except ValueError:
		orig_i = 0
	i = orig_i
	set_cache("last", {"idn": name, "num": i}, 2)
	title = title if title else name
	infoLabels = {"title": title, "plot": "[B]%s[/B] - Stream %s/%s" % (name, i + 1, len(m))}
	o = ListItem(name)
	log("Spiele %s" % url)
	url = configure_playback(o, url, headers, live=True)
	o.setProperty("IsPlayable", "true")
	info_tag = ListItemInfoTag(o, 'video')
	info_tag.set_info(infoLabels)
	set_resolved(o)
	end()

def makem3u():
	m3u = ["#EXTM3U\n"]
	for name in getchannels(): m3u.append('#EXTINF:-1 group-title="Standart",%s\nplugin://plugin.video.streamhub/?name=%s\n' % (name.strip(), name.replace("&", "%26").replace("+", "%2b").strip()))
	m3uPath = os.path.join(addonprofile, "streamhub.m3u")
	with open(m3uPath, "w") as a:
		a.writelines(m3u)
	ok = dialog.ok('StreamHub', 'm3u erstellt in %s' % m3uPath)


def channels(items=None, type=None, group=None):
	try: lines = json.loads(getSetting("favs"))
	except (TypeError, ValueError):
		lines = []
	results = json.loads(items) if items else getchannels(type, group)
	groups_map = {}
	for prop in ("vav_channel_groups", "dlhd_channel_countries"):
		try:
			part = json.loads(home.getProperty(prop) or "{}")
		except Exception:
			part = {}
		for k, v in part.items():
			cur = groups_map.setdefault(k, [])
			for code in (v or []):
				if code not in cur:
					cur.append(code)
	from streamhub.movies import pref_lang, lang_countries
	_want = pref_lang()
	_want_codes = lang_countries(_want)
	if _want_codes and not items:
		kept = {}
		for name in results:
			codes = groups_map.get(name) or []
			if any(c in _want_codes for c in codes):
				kept[name] = results[name]
		if kept:
			results = kept
	for name in results:
		index = len(results[name])
		title = name if getSetting("stream_count") == "false" or index == 1 else "%s  (%s)" % (name, index)
		codes = groups_map.get(name) or []
		if codes:
			if _want_codes:
				codes = ([c for c in codes if c in _want_codes] +
				         [c for c in codes if c not in _want_codes])
			title = "%s  [COLOR grey][%s][/COLOR]" % (title, "/".join(codes[:3]))
		o = ListItem(name)
		img = "%s.png" % name.replace(" ", "").lower()
		iconimage = "DefaultTVShows.png"
		if img in chanicons: iconimage = "https://michaz1988.github.io/logos/%s" % img
		o.setArt({"icon": iconimage, "thumb": iconimage, "poster": iconimage})
		cm = []
		if not name in lines:
			cm.append(("zu TV Favoriten hinzufügen", "RunPlugin(%s?action=addTvFavorit&name=%s)" % (sys.argv[0], name.replace("&", "%26").replace("+", "%2b"))))
			plot = ""
		else:
			plot = "[COLOR gold]TV Favorit[/COLOR]"
			cm.append(("von TV Favoriten entfernen", "RunPlugin(%s?action=delTvFavorit&name=%s)" % (sys.argv[0], name.replace("&", "%26").replace("+", "%2b"))))
		cm.append(("Einstellungen", "RunPlugin(%s?action=settings)" % sys.argv[0]))
		cm.append(("m3u erstellen", "RunPlugin(%s?action=makem3u)" % sys.argv[0]))
		o.addContextMenuItems(cm)
		infoLabels = {"title": title, "plot": plot}
		info_tag = ListItemInfoTag(o, 'video')
		info_tag.set_info(infoLabels)
		o.setProperty("IsPlayable", "true")
		param = {"name": name, "type": type, "group": group} if type else {"name": name}
		add(param, o)
	sort_method()
	end()

def favchannels():
	try: lines = json.loads(getSetting("favs"))
	except (TypeError, ValueError):
		return
	for name in getchannels():
		if not name in lines: continue
		o = ListItem(name)
		img = "%s.png" % name.replace(" ", "").lower()
		iconimage = "DefaultTVShows.png"
		if img in chanicons: iconimage = "https://michaz1988.github.io/logos/%s" % img
		o.setArt({"icon": iconimage, "thumb": iconimage, "poster": iconimage})
		cm = []
		cm.append(("von TV Favoriten entfernen", "RunPlugin(%s?action=delTvFavorit&name=%s)" % (sys.argv[0], name.replace("&", "%26").replace("+", "%2b"))))
		cm.append(("Einstellungen", "RunPlugin(%s?action=settings)" % sys.argv[0]))
		o.addContextMenuItems(cm)
		infoLabels = {"title": name, "plot": "[COLOR gold]Liste der eigene Live Favoriten[/COLOR]"}
		info_tag = ListItemInfoTag(o, 'video')
		info_tag.set_info(infoLabels)
		o.setProperty("IsPlayable", "true")
		add({"name": name}, o)
	sort_method()
	end()

def change_favorit(name, delete=False):
	try:lines = json.loads(getSetting("favs"))
	except (TypeError, ValueError):
		lines = []
	if delete:
		if name in lines:
			lines.remove(name)
	else:
		if name not in lines:
			lines.append(name)
	setSetting("favs", json.dumps(lines))
	if len(lines) == 0: execute("Action(ParentDir)")
	else: execute("Container.Refresh")
