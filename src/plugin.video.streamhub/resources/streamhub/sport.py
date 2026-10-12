
from streamhub.utils import *
from streamhub.playback import configure_playback, split_stream_url, max_height

_CAT_ALIAS = {
	"soccer": "Football", "fussball": "Football", "fußball": "Football",
	"american football": "American Football", "nfl": "American Football",
	"ice hockey": "Hockey", "nhl": "Hockey",
	"basket": "Basketball", "nba": "Basketball",
	"motor sports": "Motorsport", "motorsports": "Motorsport",
	"formula 1": "Motorsport", "f1": "Motorsport",
	"baseball": "Baseball", "mlb": "Baseball",
	"tv shows": "Other", "sports": "Other",
}


def _cat_root(cat):
	base = (cat or "").split(".")[0].strip()
	base = re.split(r"\s*[:\-–]\s*", base)[0].strip()
	if not base:
		return "Other"
	low = base.lower()
	for k, v in _CAT_ALIAS.items():
		if low == k or low.startswith(k + " "):
			return v
	return base[:1].upper() + base[1:]


def _dlhd_events():
	try:
		from streamhub.dlhd import fetch_schedule
		raw = fetch_schedule()
	except Exception:
		log(format_exc())
		return []
	out = []
	for ev in raw:
		chans = ev.get("channels") or []
		if not chans:
			continue
		title = ev.get("title", "")
		cat = ev.get("category") or ""
		if ":" in title and not cat:
			cat = title.split(":", 1)[0]
		out.append({
			"id": "dlhd_%s_%s" % (ev.get("time", ""), title[:40]),
			"title": re.sub(r"^[^:]{3,24}:\s*", "", title) or title,
			"category": _cat_root(cat or title),
			"desc": ev.get("time", ""),
			"score": "",
			"live": False,
			"source": "DLHD",
			"channels": chans,
		})
	return out


def _livetv_events():
	if getSetting("livetv") != "true":
		return []
	try:
		from streamhub.livetv import fetch_events
		evs = fetch_events()
	except Exception:
		log(format_exc())
		return []
	for e in evs:
		e["source"] = "LiveTV"
		e["category"] = _cat_root(e.get("category"))
	return evs


_PREFIX_RE = re.compile(r"^[^:]{3,24}:\s*")
_PAREN2_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_VS_RE = re.compile(r"\s+(?:vs?\.?|@)\s+", re.I)
_DASH_RE = re.compile(r"\s+[-–—]\s+")
_NOISE_RE = re.compile(r"[^a-z0-9 ]+")
_STOP = {"fc", "cf", "sc", "ac", "afc", "cd", "if", "bk", "sk", "us", "the",
         "club", "city", "united", "team", "u19", "u20", "u21", "u23", "women",
         "ii", "b"}


def _team_key(title):
	t = (title or "").lower()
	t = _PREFIX_RE.sub("", t)
	t = _PAREN2_RE.sub(" ", t)
	parts = [p for p in _VS_RE.split(t) if p.strip()]
	if len(parts) < 2:
		parts = [p for p in _DASH_RE.split(t) if p.strip()]
	if len(parts) < 2:
		return ""
	sides = []
	for p in parts[:2]:
		p = _NOISE_RE.sub(" ", p)
		toks = [w for w in p.split() if w and w not in _STOP and len(w) > 2]
		if not toks:
			toks = [w for w in p.split() if w]
		if not toks:
			return ""
		sides.append(toks[0][:6])
	if sides[0] == sides[1]:
		return ""
	return "|".join(sorted(sides))


def _all_events():
	parts = []
	with ThreadPoolExecutor(max_workers=2) as ex:
		futs = [ex.submit(_livetv_events), ex.submit(_dlhd_events)]
		for f in futs:
			try:
				parts.extend(f.result() or [])
			except Exception:
				log(format_exc())
	merged, by_key = [], {}
	for e in parts:
		key = _team_key(e.get("title"))
		if not key:
			e["sources"] = [e.get("source") or ""]
			merged.append(e)
			continue
		cur = by_key.get(key)
		if cur is None:
			e["sources"] = [e.get("source") or ""]
			by_key[key] = e
			merged.append(e)
			continue
		src = e.get("source") or ""
		if src and src not in cur["sources"]:
			cur["sources"].append(src)
		if e.get("channels"):
			cur.setdefault("channels", [])
			for c in e["channels"]:
				if c not in cur["channels"]:
					cur["channels"].append(c)
		if e.get("url") and not cur.get("url"):
			cur["url"] = e["url"]
		if e.get("live"):
			cur["live"] = True
			if e.get("score") and not cur.get("score"):
				cur["score"] = e["score"]
		if not cur.get("desc") and e.get("desc"):
			cur["desc"] = e["desc"]
	return merged


def index(params):
	try:
		events = _all_events()
	except Exception:
		log(format_exc())
		events = []
	if not events:
		dialog.notification("StreamHub Sport",
		                    "Keine Events gefunden (Quellen nicht erreichbar)",
		                    time=4000)
		end()
		return
	set_content("videos")
	set_category("Live Sport")

	live_n = sum(1 for e in events if e.get("live"))
	if live_n:
		addDir2("[B][COLOR lime]Jetzt LIVE[/COLOR][/B]  (%d)" % live_n,
		        "DefaultAddonPVRClient", "sport_list", filter="live")
	addDir2("[B]Alle Events[/B]  (%d)" % len(events),
	        "DefaultAddonPVRClient", "sport_list", filter="all")

	cats = {}
	for e in events:
		cats[e.get("category") or "Other"] = cats.get(e.get("category") or "Other", 0) + 1
	for cat in sorted(cats, key=lambda c: (-cats[c], c)):
		addDir2("%s  [COLOR grey](%d)[/COLOR]" % (cat, cats[cat]),
		        "DefaultAddonPVRClient", "sport_list", filter="cat", cat=cat)
	end()


def _sort_events(events):
	return sorted(events, key=lambda e: (
		0 if e.get("live") else 1,
		(e.get("category") or "").lower(),
		e.get("desc", ""),
		e.get("title", ""),
	))


def listing(params):
	mode = params.get("filter", "all")
	cat = params.get("cat", "")
	try:
		events = _all_events()
	except Exception:
		log(format_exc())
		events = []
	if mode == "live":
		events = [e for e in events if e.get("live")]
		title = "Jetzt LIVE"
	elif mode == "cat":
		events = [e for e in events if (e.get("category") or "Other") == cat]
		title = cat
	else:
		title = "Alle Events"
	if not events:
		dialog.notification("StreamHub Sport", "Keine Events", time=3000)
		end()
		return
	set_content("videos")
	set_category("Live Sport - %s" % title)
	for e in _sort_events(events):
		label = e.get("title", "?")
		if e.get("live"):
			label = "[COLOR lime]● LIVE[/COLOR]  %s" % label
			if e.get("score"):
				label += "  [COLOR yellow]%s[/COLOR]" % e["score"]
		elif e.get("desc"):
			t = e["desc"].split(" ")[0]
			label = "[COLOR grey]%s[/COLOR]  %s" % (t, label)
		cat_txt = e.get("category") or ""
		if cat_txt and cat_txt.lower() not in label.lower():
			label += "  [COLOR grey](%s)[/COLOR]" % cat_txt
		srcs = [s for s in (e.get("sources") or [e.get("source") or ""]) if s]
		if srcs:
			label += "  [COLOR grey]%s[/COLOR]" % "+".join(srcs)
		o = ListItem(label)
		o.setArt({"icon": "DefaultAddonPVRClient.png"})
		o.setProperty("IsPlayable", "true")
		info_tag = ListItemInfoTag(o, "video")
		info_tag.set_info({
			"title": e.get("title", ""),
			"plot": "[B]%s[/B]\n%s\n%s%s" % (
				e.get("title", ""), e.get("category", ""),
				e.get("desc", ""),
				("\n\n[COLOR lime]LIVE  %s[/COLOR]" % e.get("score", ""))
				if e.get("live") else ""),
		})
		add({"action": "sport_play",
		     "event": json.dumps({"title": e.get("title", ""),
		                          "url": e.get("url", ""),
		                          "sources": srcs,
		                          "channels": e.get("channels") or [],
		                          "live": e.get("live")})}, o, False)
	end()


def play(params):
	try:
		ev = json.loads(params.get("event", "{}"))
	except Exception:
		fail_resolved("Event-Daten ungültig")
		return

	prog = StatusProgress("StreamHub")
	prog.update(10, "Suche Streams …")

	order = []
	for c in (ev.get("channels") or []):
		if c.get("url"):
			order.append({"_dlhd": c["url"],
			              "type": "%s (DLHD)" % c.get("label", "Stream"),
			              "lang": "", "playable": c.get("playable")})
	if ev.get("url"):
		try:
			from streamhub.livetv import fetch_links
			for l in fetch_links(ev["url"]):
				l["type"] = "%s (LiveTV)" % l.get("type", "?")
				order.append(l)
		except Exception:
			log(format_exc())

	from streamhub.movies import pref_lang
	_want = pref_lang()
	order.sort(key=lambda l: (
		0 if (_want and _want in (l.get("lang") or "").upper()) else
		1 if l.get("playable") else
		2 if (l.get("lang") or "").upper() == "EN" else 3,
		l.get("type", "")))

	if not order:
		prog.close()
		fail_resolved("Keine Streams für dieses Event")
		return
	prog.update(30, "%d Stream(s) gefunden" % len(order))

	host_select = getSetting("movies_host_select") == "true"
	if host_select and len(order) > 1:
		prog.close()
		labels = []
		for i, l in enumerate(order):
			lg = l.get("lang") or ""
			labels.append("%d. %s%s" % (
				i + 1, l.get("type", "?"),
				("  [COLOR yellow][%s][/COLOR]" % lg) if lg else ""))
		idx = selectDialog(labels, "Stream wählen — %s" % ev.get("title", ""))
		if idx < 0:
			fail_resolved()
			return
		order = [order[idx]]

	url, headers, chosen = None, None, None
	for i, l in enumerate(order):
		prog.update(30 + int(60 * (i + 1) / max(len(order), 1)),
		            "Löse %s auf …" % l.get("type", "?"))
		try:
			if l.get("_dlhd"):
				from streamhub.vjlive import resolve_link as live_resolve
				url, headers = live_resolve(l["_dlhd"])
			else:
				from streamhub.livetv import resolve_link
				url, headers = resolve_link(l)
		except Exception:
			log(format_exc())
			url, headers = None, None
		if url:
			chosen = l
			break
	prog.close()

	if not url:
		fail_resolved("Stream nicht abrufbar")
		return

	o = ListItem(ev.get("title", "Sport"))
	o.setProperty("IsPlayable", "true")
	url = configure_playback(o, url, headers, live=True)
	info_tag = ListItemInfoTag(o, "video")
	info_tag.set_info({
		"title": ev.get("title", ""),
		"plot": "[B]%s[/B]\nQuelle: %s (livetv.sx)" % (
			ev.get("title", ""), (chosen or {}).get("type", "?")),
	})
	play_directly(url, o)
