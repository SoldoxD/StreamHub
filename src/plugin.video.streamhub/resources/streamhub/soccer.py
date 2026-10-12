
from streamhub.utils import *
from streamhub.playback import configure_playback, split_stream_url, max_height


def _humanize_event(ev):
	t = ev.get("time") or ""
	title = ev.get("title") or "?"
	cat = ev.get("category") or ""
	parts = [t, title]
	if cat and cat.lower() not in title.lower():
		parts.append("[%s]" % cat)
	return " - ".join(p for p in parts if p)


def index(params):
	from streamhub.dlhd import fetch_soccer_events
	try:
		events = fetch_soccer_events()
	except Exception:
		log(format_exc())
		events = []
	if not events:
		dialog.notification("StreamHub Soccer",
		                    "Keine Spiele gefunden (DLHD-Schedule leer oder nicht erreichbar)",
		                    time=4000)
		end()
		return
	set_content("videos")
	set_category("Live Soccer")


	by_day = {}
	for ev in events:
		by_day.setdefault(ev.get("day", "Today"), []).append(ev)

	if len(by_day) == 1:
		day = next(iter(by_day))
		_list_events(by_day[day])
	else:
		for day in by_day:
			label = "[B]%s[/B]  (%d Spiele)" % (day, len(by_day[day]))
			addDir2(label, "DefaultAddonPVRClient", "soccer_day", day=day)
		end()


def day(params):
	from streamhub.dlhd import fetch_soccer_events
	target = params.get("day", "")
	events = [e for e in fetch_soccer_events() if e.get("day", "") == target]
	set_content("videos")
	set_category("Live Soccer - %s" % target)
	_list_events(events)


def _list_events(events):
	for idx, ev in enumerate(events):
		ch_count = len(ev.get("channels", []))
		playable = any(c.get("playable") for c in ev.get("channels", []))
		label = _humanize_event(ev)
		if ch_count == 0:
			label = "[COLOR grey]%s  (no streams)[/COLOR]" % label
		elif not playable:
			label = "%s  [COLOR orange](only PPV/streamed — may not play)[/COLOR]" % label
		else:
			label = "%s  [COLOR green](%d streams)[/COLOR]" % (label, ch_count)

		o = ListItem(label)
		o.setArt({"icon": "DefaultAddonPVRClient.png"})
		o.setProperty("IsPlayable", "true")
		info_tag = ListItemInfoTag(o, 'video')
		info_tag.set_info({
			"title": ev.get("title", ""),
			"plot": "[B]%s %s[/B]\n%s\n\n%d Stream(s) verfügbar" % (
				ev.get("time", ""), ev.get("category", ""),
				ev.get("title", ""), ch_count
			),
		})
		params = {
			"action": "soccer_play",
			"event": json.dumps({
				"title": ev.get("title", ""),
				"time": ev.get("time", ""),
				"channels": ev.get("channels", []),
			}),
		}
		add(params, o, False)
	end()


def play(params):
	from streamhub.vjlive import resolve_link
	try:
		ev = json.loads(params.get("event", "{}"))
	except Exception:
		fail_resolved("Event-Daten ungültig")
		return
	channels = ev.get("channels", [])
	if not channels:
		fail_resolved("Keine Streams für dieses Spiel")
		return

	playable_first = sorted(channels,
	                        key=lambda c: (0 if c.get("playable") else 1, c.get("label", "")))

	manual = params.get("manual") == "true"
	host_select = getSetting("movies_host_select") == "true" or manual

	if host_select and len(playable_first) > 1:
		labels = ["%s%s" % ("✓ " if c.get("playable") else "⚠ ",
		                    c.get("label", "Stream")) for c in playable_first]
		idx = selectDialog(labels, "Stream wählen — %s" % ev.get("title", ""))
		if idx < 0:
			fail_resolved()
			return
		try_order = [playable_first[idx]]
	else:
		try_order = playable_first

	import time as _time
	deadline = _time.time() + 4.0
	url, headers, chosen = None, None, None
	for c in try_order:
		if _time.time() > deadline and len(try_order) > 1:
			break
		url, headers = resolve_link(c["url"])
		if url:
			chosen = c
			break
		log("soccer host %s failed" % c.get("label", "?"))

	if not url:
		fail_resolved("Stream nicht abrufbar — bitte anderen wählen")
		return


	o = ListItem(ev.get("title", "Soccer"))
	url = configure_playback(o, url, headers, live=True)
	o.setProperty("IsPlayable", "true")
	info_tag = ListItemInfoTag(o, 'video')
	info_tag.set_info({
		"title": ev.get("title", ""),
		"plot": "[B]%s[/B]\n%s" % (ev.get("title", ""), chosen.get("label", "")),
	})
	play_directly(url, o)
