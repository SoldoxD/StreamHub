import sys

from streamhub.utils import log


def _resolver_contract():
	from streamhub import movies
	from lib.scrapers import get_scrapers
	from lib.scrapers.base import BaseScraper

	seen = set()
	probe = BaseScraper()
	probe.name = "selftest"
	probe.base_url = "https://example.invalid"
	src = probe.make_source("https://example.invalid/e/abc")
	seen.add(src.get("resolver"))
	for cls in get_scrapers("movie") + get_scrapers("tvshow"):
		seen.add(getattr(cls, "default_resolver", None) or "embed")

	calls = []
	orig_embed = movies._resolve_embed
	orig_vavoo = movies._resolve_host
	movies._resolve_embed = lambda h: calls.append(h.get("resolver")) or ("ok", None)
	try:
		bad = []
		for rn in sorted(x for x in seen if x):
			if rn == "vavoo_chain":
				continue
			calls[:] = []
			res = movies._resolve_host({"url": "https://x/y", "resolver": rn})
			if not res:
				bad.append(rn)
	finally:
		movies._resolve_embed = orig_embed
		movies._resolve_host = orig_vavoo
	return bad, sorted(x for x in seen if x)


def run():
	problems = []
	bad, names = _resolver_contract()
	if bad:
		problems.append("resolver names not dispatched by _resolve_host: %s" % bad)
	log("selftest: resolver names in use = %s" % names)
	if problems:
		for p in problems:
			log("SELFTEST FAILURE: %s" % p)
		return False, problems
	return True, ["resolver dispatch ok (%s)" % ", ".join(names)]


if __name__ == "__main__":
	ok, msgs = run()
	for m in msgs:
		print(m)
	sys.exit(0 if ok else 1)
