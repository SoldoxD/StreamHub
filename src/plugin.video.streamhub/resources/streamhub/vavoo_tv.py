
from streamhub.utils import *

def vavoo_groups():
	# The index is shared by every channel start; do not download it on each zap.
	ok, cached = get_cache("vavoo_group_index")
	if (ok and isinstance(cached, dict) and isinstance(cached.get("groups"), list)
	        and cached.get("hash")):
		return cached["groups"], cached["hash"]
	response = request("GET", "https://www2.vavoo.to/live2/index?output=json",
	                   timeout=8, retries=0, max_bytes=8 * 1024 * 1024)
	response.raise_for_status()
	body = response.text
	channels = json.loads(body)
	if not isinstance(channels, list):
		raise ValueError("Invalid VAVOO channel index")
	groups = sorted({row["group"] for row in channels if isinstance(row, dict)
	                 and isinstance(row.get("group"), str) and row["group"]})
	if not groups:
		raise ValueError("Empty VAVOO channel index")
	digest = md5(body.encode()).hexdigest()
	set_cache("vavoo_group_index", {"groups": groups, "hash": digest}, timeout=10.0 / 60)
	return groups, digest


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
	headers = {"user-agent": "MediaHubMX/2", "accept": "application/json",
	           "content-type": "application/json; charset=utf-8", "mediahubmx-signature": sig}
	items, seen_cursors, seen_urls = [], set(), set()
	cursor = 0
	deadline = time.monotonic() + 20
	for _ in range(100):
		if monitor.abortRequested() or time.monotonic() >= deadline:
			return group, items, False
		cursor_key = json.dumps(cursor, sort_keys=True)
		if cursor_key in seen_cursors:
			log("VAVOO: repeated catalog cursor for %s" % group)
			return group, items, False
		seen_cursors.add(cursor_key)
		try:
			payload = {"language": "de", "region": "AT", "catalogId": "iptv", "id": "iptv",
			           "adult": False, "search": "", "sort": "name", "filter": {"group": group},
			           "cursor": cursor, "clientVersion": "3.1.0"}
			result = request_json("POST", "https://vavoo.to/mediahubmx-catalog.json",
			                      json=payload, headers=headers, timeout=min(6, max(0.1, deadline - time.monotonic())),
			                      retries=0, max_bytes=2 * 1024 * 1024)
			if not isinstance(result, dict) or not isinstance(result.get("items"), list):
				return group, items, False
			for row in result["items"]:
				if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k]
				                                      for k in ("url", "name", "group")):
					continue
				if row["url"] not in seen_urls:
					seen_urls.add(row["url"])
					items.append({k: row[k] for k in ("url", "name", "group")})
			cursor = result.get("nextCursor")
			if cursor is None:
				return group, items, True
		except Exception:
			log("VAVOO: catalog failed for %s\n%s" % (group, format_exc()))
			return group, items, False
	return group, items, False

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
	cacheOk, chan = get_cache("vav_channels")
	cached_channels = chan.get("channels") if cacheOk and isinstance(chan, dict) else []
	index_failed = False
	try:
		g, newhash = vavoo_groups()
	except Exception:
		log("VAVOO: index unavailable; using cached channels\n%s" % format_exc())
		if not cached_channels:
			return {}
		g = sorted({row["group"] for row in cached_channels})
		newhash, index_failed = "", True
	if not groups:
		groups = default_groups(g)
		if groups:
			set_cache("groups", groups)
	if not groups:
		return {}
	groups = [x for x in groups if x in g] or default_groups(g)
	key = "%s|%s" % (newhash, ",".join(sorted(groups)))
	if cacheOk and isinstance(chan, dict) and (index_failed or chan.get("key") == key) and chan.get("channels"):
		channels = chan["channels"]
	else:
		log("Getting new VAVOO Channels for groups: %s" % ", ".join(groups))
		sig = getAuthSignature()
		channels = []
		failed = []
		if not sig:
			# Do not repeat a failed login independently in every group worker.
			channels = cached_channels or []
			failed = list(groups)
		with ThreadPoolExecutor(max_workers=min(6, max(len(groups), 1))) as _ex:
			futs = [_ex.submit(new_vav_channels, grp, sig) for grp in groups] if sig else []
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
