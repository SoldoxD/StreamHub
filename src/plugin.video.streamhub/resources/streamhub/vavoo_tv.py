
from streamhub.utils import *

def vavoo_groups():
	log("Getting VAVOO groups and md5")
	groups=[]
	a = request("GET", "https://www2.vavoo.to/live2/index?output=json", timeout=10, retries=1).text
	hash = md5(a.encode()).hexdigest()
	chans = json.loads(a)
	for c in chans:
		if c["group"] not in groups: groups.append(c["group"])
	return sorted(groups), hash

def choose():
	groups, hash = vavoo_groups()
	cacheOk, b = get_cache("groups")
	preselect = []
	if cacheOk:
		oldgroups = []
		for a in b:
			if a in groups: oldgroups.append(a)
		preselect = [groups.index(oldgroup) for oldgroup in oldgroups]
	indicies = selectDialog(groups, "Choose VAVOO Groups", True, preselect)
	if not indicies:
		return []
	group = [groups[i] for i in indicies]
	set_cache("groups", group)
	return group

def new_vav_channels(group, sig=None):
	sig = sig or getAuthSignature()
	if not sig:
		log("VAVOO: no auth signature for group %s" % group)
		return group, [], False
	_headers={"user-agent": "MediaHubMX/2", "accept": "application/json", "content-type": "application/json; charset=utf-8", "accept-encoding": "gzip", "mediahubmx-signature": sig}
	items = []
	cursor = 0
	while cursor != None:
		try:
			_data={"language":"de","region":"AT","catalogId":"iptv","id":"iptv","adult":False,"search":"","sort":"name","filter":{"group":group},"cursor":cursor,"clientVersion":"3.1.0"}
			req = request_json("POST", "https://vavoo.to/mediahubmx-catalog.json", json=_data, headers=_headers, timeout=15, retries=2)
			if not isinstance(req, dict) or "items" not in req:
				log("VAVOO: bad catalog payload for group %s" % group)
				return group, items, False
			for r in req["items"]:
				items.append({"url": r["url"], "name": r["name"], "group": r["group"]})
			cursor = req.get("nextCursor")
		except Exception:
			log("VAVOO: group %s failed at cursor %s\n%s" % (group, cursor, format_exc()))
			return group, items, False
	return group, items, True

_COUNTRY_CODES = {
	"germany": "DE", "deutschland": "DE",
	"austria": "AT", "österreich": "AT",
	"switzerland": "CH", "schweiz": "CH",
	"italy": "IT", "italia": "IT",
	"france": "FR",
	"spain": "ES", "españa": "ES",
	"united kingdom": "UK", "uk": "UK", "great britain": "UK",
	"united states": "US", "usa": "US",
	"netherlands": "NL", "niederlande": "NL",
	"poland": "PL", "polska": "PL",
	"portugal": "PT",
	"greece": "GR",
	"turkey": "TR", "türkei": "TR",
	"romania": "RO",
	"russia": "RU",
	"czech republic": "CZ", "czechia": "CZ",
	"albania": "AL",
	"balkans": "BA",
	"arabic": "AR",
	"belgium": "BE",
	"bulgaria": "BG",
	"croatia": "HR",
	"denmark": "DK",
	"finland": "FI",
	"hungary": "HU",
	"ireland": "IE",
	"norway": "NO",
	"sweden": "SE",
	"ukraine": "UA",
}


def _group_code(group):
	if not group:
		return ""
	code = _COUNTRY_CODES.get(group.strip().lower())
	if code:
		return code
	return group[:2].upper()


def default_groups(all_groups):
	try:
		from streamhub.movies import lang_countries
		codes = lang_countries()
	except Exception:
		codes = ()
	if not codes:
		return list(all_groups)
	picked = [g for g in all_groups if _group_code(g) in codes]
	return picked or list(all_groups)


def get_vav_channels(groups = False):
	if groups == False: cacheOk, groups = get_cache("groups")
	g, newhash = vavoo_groups()
	if not groups:
		groups = default_groups(g)
		if groups:
			set_cache("groups", groups)
	if not groups:
		return {}
	groups = [x for x in groups if x in g] or default_groups(g)
	key = "%s|%s" % (newhash, ",".join(sorted(groups)))
	cacheOk, chan = get_cache("vav_channels")
	if cacheOk and isinstance(chan, dict) and chan.get("key") == key and chan.get("channels"):
		channels = chan["channels"]
	else:
		log("Getting new VAVOO Channels for groups: %s" % ", ".join(groups))
		sig = getAuthSignature()
		channels = []
		failed = []
		with ThreadPoolExecutor(max_workers=min(6, max(len(groups), 1))) as _ex:
			futs = [_ex.submit(new_vav_channels, grp, sig) for grp in groups]
			for fut in futs:
				try:
					grp, items, ok = fut.result()
				except Exception:
					log(format_exc())
					continue
				channels += items
				if not ok:
					failed.append(grp)
		if failed:
			log("VAVOO: incomplete fetch, groups failed: %s" % ", ".join(failed))
			if cacheOk and isinstance(chan, dict) and chan.get("channels"):
				log("VAVOO: keeping previous cached channel list")
				channels = chan["channels"]
			dialog.notification("StreamHub",
			                    "VAVOO unvollständig (%s)" % ", ".join(failed[:3]),
			                    time=4000)
		elif channels:
			set_cache("vav_channels", {"channels": channels, "key": key})
	vavchannels = {}
	channel_groups = {}
	for item in channels:
		if item["group"] not in groups: continue
		name = filterout(item["name"])
		if name not in vavchannels:
			vavchannels[name] = []
			channel_groups[name] = []
		if item["url"] not in vavchannels[name]:
			vavchannels[name].append(item["url"])
		code = _group_code(item["group"])
		if code and code not in channel_groups[name]:
			channel_groups[name].append(code)
	try:
		home.setProperty("vav_channel_groups", json.dumps(channel_groups))
	except Exception:
		pass
	return vavchannels
