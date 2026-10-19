import json
import os
import random
import re
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from streamhub.utils import request, log, cachepath, format_exc

USER_AGENTS = (
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
)

DEFAULT_TIMEOUT = 8
DEFAULT_RETRIES = 3
CACHE_DIR = os.path.join(cachepath, "scrapers")

_QUALITY_PATTERNS = (
	(2160, re.compile(r"\b(2160p?|4k|uhd)\b", re.I)),
	(1440, re.compile(r"\b1440p?\b", re.I)),
	(1080, re.compile(r"\b(1080p?|fhd|fullhd)\b", re.I)),
	(720, re.compile(r"\b(720p?|hd)\b", re.I)),
	(480, re.compile(r"\b(480p?|sd)\b", re.I)),
	(360, re.compile(r"\b360p?\b", re.I)),
	(240, re.compile(r"\b(240p?|cam|ts|telesync)\b", re.I)),
)

_HDR_RE = re.compile(r"\b(hdr10\+?|hdr|dolby\s*vision|dovi)\b", re.I)
_CODEC_RE = re.compile(r"\b(av1|hevc|h\.?265|x265)\b", re.I)
_DIRECT_RE = re.compile(r"\.(mp4|mkv|m3u8|avi)(\?|$)", re.I)


class Cancelled(Exception):
	pass


class CancelToken:
	def __init__(self, *sources):
		self._sources = [s for s in sources if s]
		self._flag = False

	def cancel(self):
		self._flag = True

	def cancelled(self):
		if self._flag:
			return True
		for s in self._sources:
			try:
				if s():
					self._flag = True
					return True
			except Exception:
				continue
		return False

	def check(self):
		if self.cancelled():
			raise Cancelled()


NULL_TOKEN = CancelToken()


def kodi_token(prog=None):
	sources = []
	try:
		import xbmc
		mon = xbmc.Monitor()
		sources.append(mon.abortRequested)
	except Exception:
		pass
	if prog is not None:
		sources.append(prog.iscanceled)
	return CancelToken(*sources)


def _cache_file(key):
	from hashlib import md5
	return os.path.join(CACHE_DIR, md5(key.encode("utf-8")).hexdigest() + ".json")


def cache_get(key):
	try:
		p = _cache_file(key)
		with open(p, "r", encoding="utf-8") as f:
			blob = json.load(f)
		if blob.get("exp", 0) < time.time():
			return None
		return blob.get("val")
	except Exception:
		return None


def cache_set(key, value, ttl=900):
	try:
		if not os.path.isdir(CACHE_DIR):
			os.makedirs(CACHE_DIR)
		with open(_cache_file(key), "w", encoding="utf-8") as f:
			json.dump({"exp": time.time() + ttl, "val": value}, f)
	except Exception:
		pass


def cache_clear():
	n = 0
	try:
		for f in os.listdir(CACHE_DIR):
			if f.endswith(".json"):
				try:
					os.remove(os.path.join(CACHE_DIR, f))
					n += 1
				except Exception:
					continue
	except Exception:
		pass
	return n


def _drop_stale_cache_on_upgrade():
	"""Scraper results cached by an older build can outlive an update and keep
	serving results a fixed scraper would no longer return. Wipe them once
	whenever the addon version changes."""
	try:
		from streamhub.utils import addonInfo
		ver = addonInfo("version") or "?"
	except Exception:
		return
	stamp = os.path.join(CACHE_DIR, ".version")
	try:
		with open(stamp, "r", encoding="utf-8") as f:
			if f.read().strip() == ver:
				return
	except Exception:
		pass
	n = cache_clear()
	try:
		if not os.path.isdir(CACHE_DIR):
			os.makedirs(CACHE_DIR)
		with open(stamp, "w", encoding="utf-8") as f:
			f.write(ver)
		if n:
			log("scraper cache cleared for new version %s (%d entries)" % (ver, n))
	except Exception:
		pass


_drop_stale_cache_on_upgrade()


def detect_quality(*texts):
	blob = " ".join(t for t in texts if t)
	for score, rx in _QUALITY_PATTERNS:
		if rx.search(blob):
			label = "4K" if score >= 2160 else "%dp" % score
			return score, label
	return 0, ""


def score_source(s):
	score = s.get("quality_score") or 0
	if s.get("hdr"):
		score += 150
	if s.get("codec"):
		score += 50
	if s.get("direct"):
		score += 400
	if s.get("alive") is True:
		score += 1000
	elif s.get("alive") is False:
		score -= 2000
	return score


def sort_sources(sources, pref_language=None):
	def key(s):
		lang = (s.get("language") or "").upper()
		if pref_language:
			if pref_language in lang:
				lrank = 0
			elif not lang or lang == "OV":
				lrank = 2
			else:
				lrank = 1
		else:
			lrank = 0
		return (lrank, -score_source(s), s.get("host") or "")

	return sorted(sources, key=key)


class BaseScraper:
	name = "base"
	base_url = ""
	language = ""
	media_types = ("movie",)
	virtual = False
	enabled_setting = None

	search_ttl = 900
	sources_ttl = 600
	empty_ttl = 120
	timeout = DEFAULT_TIMEOUT
	retries = DEFAULT_RETRIES
	delay_range = (0, 0)
	response_limit = 2 * 1024 * 1024

	def __init__(self, token=None):
		self.token = token or NULL_TOKEN
		self._ua = random.choice(USER_AGENTS)

	def headers(self, referer=None, **extra):
		h = {
			"User-Agent": self._ua,
			"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
			"Accept-Language": "de,en-US;q=0.8,en;q=0.6",
		}
		if referer or self.base_url:
			h["Referer"] = referer or (self.base_url.rstrip("/") + "/")
		h.update(extra)
		return h

	def _wait(self, seconds):
		deadline = time.monotonic() + max(0, seconds)
		while True:
			self.token.check()
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				return
			time.sleep(min(0.1, remaining))

	def fetch(self, url, method="GET", timeout=None, **kwargs):
		self.token.check()
		lo, hi = self.delay_range
		if hi:
			self._wait(random.uniform(lo, hi))
		headers = kwargs.pop("headers", None) or self.headers()
		kwargs.setdefault("max_bytes", self.response_limit)
		last = None
		for attempt in range(self.retries):
			self.token.check()
			try:
				r = request(method, url, headers=headers,
				            timeout=timeout or self.timeout,
				            retries=0, **kwargs)
				try:
					self.token.check()
				except Cancelled:
					r.close()
					raise
				if r.status_code in (429, 503) and attempt < self.retries - 1:
					last = Exception("HTTP %s" % r.status_code)
					r.close()
				else:
					return r
			except Cancelled:
				raise
			except Exception as e:
				last = e
			if attempt < self.retries - 1:
				backoff = (0.4 * (2 ** attempt)) + random.uniform(0, 0.4)
				self._wait(backoff)
		self.token.check()
		log("%s: fetch failed %s (%s)" % (self.name, url, last))
		return None

	def fetch_text(self, url, **kwargs):
		r = self.fetch(url, **kwargs)
		if r is None:
			return ""
		try:
			if r.status_code >= 400:
				return ""
			return r.text
		except Exception:
			return ""

	def fetch_json(self, url, **kwargs):
		r = self.fetch(url, **kwargs)
		if r is None:
			return None
		try:
			if r.status_code >= 400:
				return None
			return r.json()
		except Exception:
			return None

	def search(self, query, media_type="movie"):
		raise NotImplementedError

	def get_details(self, item):
		return {}

	def get_sources(self, item):
		raise NotImplementedError

	def cached_search(self, query, media_type="movie"):
		self.token.check()
		key = "s|%s|%s|%s" % (self.name, media_type, (query or "").lower().strip())
		hit = cache_get(key)
		if hit is not None:
			return hit
		try:
			res = self.search(query, media_type) or []
		except Cancelled:
			raise
		except Exception:
			log("%s: search failed\n%s" % (self.name, format_exc()))
			res = []
		self.token.check()
		cache_set(key, res, self.search_ttl if res else self.empty_ttl)
		return res

	def cached_sources(self, item):
		self.token.check()
		key = "v|%s|%s" % (self.name, self.source_key(item))
		hit = cache_get(key)
		if hit is not None:
			return hit
		try:
			res = self.get_sources(item) or []
		except Cancelled:
			raise
		except Exception:
			log("%s: get_sources failed\n%s" % (self.name, format_exc()))
			res = []
		self.token.check()
		cache_set(key, res, self.sources_ttl if res else self.empty_ttl)
		return res

	def source_key(self, item):
		return "%s|%s|%s" % (item.get("url") or item.get("id") or item.get("title", ""),
		                     item.get("season") if item.get("season") is not None else "", item.get("episode") or "")

	def make_source(self, url, host=None, label=None, resolver="embed",
	                language=None, quality_text="", referer=None, **extra):
		if not url:
			return None
		host = host or (urlparse(url).netloc or "?").lower().replace("www.", "")
		qscore, qlabel = detect_quality(quality_text, label or "", url)
		direct = bool(_DIRECT_RE.search(urlparse(url).path))
		lang = (language or self.language or "").upper()
		hdr = bool(_HDR_RE.search(quality_text or ""))
		src = {
			"provider": self.name,
			"host": host,
			"label": label or host,
			"url": url,
			"resolver": "direct" if direct else resolver,
			"referer": referer or (self.base_url.rstrip("/") + "/" if self.base_url else None),
			"language": lang,
			"lang": lang,
			"quality": qlabel,
			"quality_score": qscore,
			"hdr": hdr,
			"_hdr": hdr,
			"codec": bool(_CODEC_RE.search(quality_text or "")),
			"direct": direct,
			"_direct": direct,
			"alive": None,
		}
		src.update(extra)
		return src


def head_alive(url, token=None, timeout=4):
	token = token or NULL_TOKEN
	if token.cancelled():
		return None
	try:
		r = request("HEAD", url, timeout=timeout, retries=0,
		            headers={"User-Agent": random.choice(USER_AGENTS)})
		if r.status_code in (405, 501):
			return None
		return r.status_code < 400
	except Exception:
		return None


def prefilter_dead(sources, token=None, deadline_s=4.0, max_workers=8):
	token = token or NULL_TOKEN
	if not sources:
		return sources
	with ThreadPoolExecutor(max_workers=min(max_workers, len(sources))) as ex:
		futs = {ex.submit(head_alive, s["url"], token): s for s in sources}
		try:
			for fut in as_completed(futs, timeout=deadline_s):
				if token.cancelled():
					break
				s = futs[fut]
				try:
					s["alive"] = fut.result()
				except Exception:
					s["alive"] = None
		except Exception:
			pass
	return [s for s in sources if s.get("alive") is not False]
