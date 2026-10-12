
from streamhub.utils import (
	request, log, urlparse, ThreadPoolExecutor, as_completed, format_exc,
	stats_line,
)

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def hoster_name(url):
	if not url:
		return "?"
	if url.startswith("dlhd://"):
		return "DLHD"
	try:
		host = urlparse(url).netloc.lower()
	except Exception:
		return "?"
	if "vavoo" in host:
		return "VAVOO"
	if not host:

		return "STALKER"

	host = host.replace("www.", "")
	parts = host.split(".")
	if len(parts) >= 2:
		return ".".join(parts[-2:]).upper()
	return host.upper()


def _check_one(url, timeout=5):
	r = None
	try:
		if url.startswith("dlhd://"):
			cid = url[len("dlhd://"):]
			probe = "https://dlhd.pk/stream/stream-%s.php" % cid
			r = request("GET", probe,
			            headers={"User-Agent": DEFAULT_UA, "Referer": "https://dlhd.pk/"},
			            timeout=timeout, retries=0, stream=True, allow_redirects=True)
			return r.status_code < 400
		if "vavoo.to" in url:



			return True

		if not urlparse(url).scheme:
			return True
		r = request("HEAD", url, headers={"User-Agent": DEFAULT_UA},
		            timeout=timeout, retries=0, allow_redirects=True)
		if r.status_code in (405, 501):
			r.close()

			r = request("GET", url, headers={"User-Agent": DEFAULT_UA, "Range": "bytes=0-0"},
			            timeout=timeout, retries=0, stream=True, allow_redirects=True)
		return r.status_code < 400
	except Exception:
		log("hoster check failed for %s: %s" % (url, format_exc().splitlines()[-1]))
		return False
	finally:
		if r is not None:
			r.close()


def filter_alive(candidates, timeout=5, max_workers=8, prog=None, deadline_s=8.0):
	if not candidates:
		return []
	total = len(candidates)
	done = 0
	ok = 0
	dead = 0
	alive = {}
	with ThreadPoolExecutor(max_workers=min(max_workers, total)) as ex:
		futures = {ex.submit(_check_one, c["url"], timeout): i
		           for i, c in enumerate(candidates)}
		try:
			for fut in as_completed(futures, timeout=deadline_s):
				i = futures[fut]
				try:
					alive[i] = fut.result()
				except Exception:
					alive[i] = False
				candidates[i]["_alive"] = bool(alive[i])
				done += 1
				if alive[i]:
					ok += 1
				else:
					dead += 1
				if prog:
					prog.step(20 + int(60 * done / total),
					          "Prüfe Quellen %d/%d" % (done, total),
					          candidates[i].get("label", ""),
					          stats_line(ok=ok, dead=dead, pending=total - done))
					if prog.iscanceled():
						break
		except Exception:
			log("filter_alive: deadline reached after %d/%d" % (done, total))
	return [c for i, c in enumerate(candidates) if alive.get(i, False)]


_SOURCE_RANK = {"VAVOO": 0, "DLHD": 1, "STALKER": 2}


def label_live_streams(urls):
	counts = {}
	out = []
	for u in urls:
		h = hoster_name(u)
		counts[h] = counts.get(h, 0) + 1
	seen = {}
	for u in urls:
		h = hoster_name(u)
		if counts[h] > 1:
			seen[h] = seen.get(h, 0) + 1
			label = "%s #%d" % (h, seen[h])
		else:
			label = h
		out.append({"url": u, "hoster": h, "label": label,
		            "source": "dlhd" if str(u).startswith("dlhd://") else "vavoo"})
	return _rank_live(out)


_LIVE_SRC_COLOUR = {"dlhd": "orange", "vavoo": "deepskyblue"}


def live_display(c, index=None):
	badges = []
	if c.get("_alive") is True:
		badges.append("[COLOR lime]OK[/COLOR]")
	elif c.get("_alive") is False:
		badges.append("[COLOR red]tot[/COLOR]")
	src = c.get("source") or ""
	if src:
		badges.append("[COLOR %s]%s[/COLOR]" % (_LIVE_SRC_COLOUR.get(src, "grey"), src))
	name = "[B]%s[/B]" % c.get("label", "?")
	tag = ("  [COLOR grey][[/COLOR]%s[COLOR grey]][/COLOR]" % " ".join(badges)) if badges else ""
	if index is not None:
		return "[COLOR grey]%2d.[/COLOR] %s%s" % (index, name, tag)
	return "%s%s" % (name, tag)


def _rank_live(items):
	try:
		from streamhub.movies import _detect_quality
	except Exception:
		return items

	def key(c):
		qscore, qlabel = _detect_quality({
			"label": c.get("label", ""),
			"url": c.get("url", ""),
			"host": c.get("hoster", ""),
		})
		if qlabel:
			c["label"] = "%s  [COLOR cyan]%s[/COLOR]" % (c["label"], qlabel)
		return (_SOURCE_RANK.get(c.get("hoster"), 8), -qscore, c.get("label", ""))

	try:
		return sorted(items, key=key)
	except Exception:
		return items
