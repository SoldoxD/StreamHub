"""Kinox public catalog and mirror metadata; verification pages fail closed."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

from lib.scrapers import register
from lib.scrapers.base import BaseScraper
from lib.scrapers.html_catalog import http_url, parse_html


_MOVIE_PATH = re.compile(r"^/Stream/([^/]+)\.html$", re.I)
_TV_HINT = re.compile(r"\b(staffel|season)\b", re.I)
_REL = re.compile(r"^([^/?#&]+)&Hoster=(\d+)(?:&Mirror=\d+)?$", re.I)
_DATE = re.compile(r"\bVom\s*:\s*(\d{1,2}\.\d{1,2}\.\d{4})", re.I)
_GATE = re.compile(r"checking your request|<title[^>]*>\s*Verifizierung|"
                   r"cf-chl-|cf-turnstile|g-recaptcha", re.I)
_ASSET = re.compile(r"\.(?:js|css|jpe?g|png|gif|svg|ico|woff2?)(?:$|/)", re.I)
_IGNORED_HOSTS = ("google.com", "googleapis.com", "gstatic.com", "facebook.com",
                  "twitter.com", "jsdelivr.net", "cloudflare.com", "doubleclick.net",
                  "fontawesome.com", "googlesyndication.com")
_PAGE_LIMIT = 1024 * 1024
_MIRROR_LIMIT = 64 * 1024
_HOST_LIMIT = 12


@register
class KinoxScraper(BaseScraper):
    name = "kinox"
    base_url = "https://kinox.to"
    language = "DE"
    media_types = ("movie",)
    enabled_setting = "kinox"
    timeout = 6
    retries = 1

    def _local_url(self, value):
        return http_url(self.base_url, value, same_host=True)

    def search(self, query, media_type="movie"):
        if media_type != "movie" or not (query or "").strip():
            return []
        html = self.fetch_text(self.base_url + "/Search.html?q=" + quote(query.strip()),
                               max_bytes=_PAGE_LIMIT, allow_redirects=False)
        root = parse_html(html)
        results = next((node for node in root.walk()
                        if node.attrs.get("id") == "content_list"), None)
        if results is None:
            return []
        out, seen = [], set()
        for node in results.walk():
            self.token.check()
            if node.tag != "a":
                continue
            url = self._local_url(node.attrs.get("href"))
            match = _MOVIE_PATH.fullmatch(urlparse(url).path) if url else None
            title = (node.attrs.get("title") or node.text()).strip()
            if not match or not title or url in seen or _TV_HINT.search(title):
                continue
            seen.add(url)
            out.append({"title": title, "year": None, "poster": None,
                        "provider": self.name, "url": url, "slug": match.group(1)})
            if len(out) >= 20:
                break
        return out

    def _hosters(self, page_url):
        root = parse_html(self.fetch_text(page_url, max_bytes=_PAGE_LIMIT,
                                         allow_redirects=False))
        listing = next((node for node in root.walk()
                        if node.attrs.get("id") == "HosterList"), None)
        if listing is None:
            return []
        slug = _MOVIE_PATH.fullmatch(urlparse(page_url).path).group(1)
        out, seen = [], set()
        for node in listing.walk():
            self.token.check()
            if node.tag != "li":
                continue
            hid = re.fullmatch(r"Hoster_(\d+)", node.attrs.get("id") or "", re.I)
            rel = node.attrs.get("rel") or ""
            match = _REL.fullmatch(rel) if len(rel) <= 512 else None
            name = node.find_class("Named")
            if (not hid or not match or match.group(1) != slug or
                    match.group(2) != hid.group(1) or name is None or rel in seen):
                continue
            label = name.text()
            if not label:
                continue
            date = _DATE.search(node.text())
            seen.add(rel)
            out.append({"id": hid.group(1), "rel": rel, "name": label,
                        "date": date.group(1) if date else None})
            if len(out) >= _HOST_LIMIT:
                break
        return out

    def _target_url(self, base, value):
        url = http_url(base, value)
        if not url:
            return ""
        parsed = urlparse(url)
        host = parsed.hostname.lower()
        if host == urlparse(self.base_url).hostname:
            return url if parsed.path.startswith("/redirect/") else ""
        if (host.startswith(("ads.", "ad.")) or
                any(host == ignored or host.endswith("." + ignored)
                    for ignored in _IGNORED_HOSTS) or
                not parsed.path.strip("/") or _ASSET.search(parsed.path)):
            return ""
        return url

    def _link_target(self, html, base):
        # Only actual player elements/links count, never script, image or stylesheet URLs.
        if not isinstance(html, str) or _GATE.search(html):
            return ""
        root = parse_html(html)
        for tag, attribute in (("iframe", "src"), ("a", "href")):
            for node in root.walk():
                if node.tag != tag:
                    continue
                marker = " ".join((node.attrs.get("id") or "",
                                   node.attrs.get("class") or "")).lower()
                if any(word in marker for word in ("advert", "banner", "social")):
                    continue
                target = self._target_url(base, node.attrs.get(attribute))
                if target:
                    return target
        return ""

    def _follow_redirect(self, url, page_url):
        seen = set()
        for _ in range(3):
            self.token.check()
            if url in seen:
                return ""
            seen.add(url)
            # Never fetch the external host here: only bounded provider metadata.
            if urlparse(url).hostname != urlparse(self.base_url).hostname:
                return url
            response = self.fetch(url, headers=self.headers(referer=page_url),
                                  allow_redirects=False, max_bytes=_MIRROR_LIMIT)
            if response is None:
                return ""
            if response.status_code in (301, 302, 303, 307, 308):
                target = self._target_url(url, response.headers.get("Location"))
            elif response.status_code == 200:
                target = self._link_target(response.text, url)
            else:
                return ""
            if not target:
                return ""
            if urlparse(target).hostname != urlparse(self.base_url).hostname:
                return target
            url = target
        return ""

    def _mirror_target(self, rel, page_url):
        if not isinstance(rel, str) or not _REL.fullmatch(rel) or len(rel) > 512:
            return ""
        body = self.fetch_text(self.base_url + "/aGET/Mirror/" + quote(rel, safe="&="),
                               headers=self.headers(referer=page_url,
                                                    **{"X-Requested-With": "XMLHttpRequest"}),
                               allow_redirects=False, max_bytes=_MIRROR_LIMIT)
        try:
            data = json.loads(body)
        except (TypeError, ValueError):
            return ""
        if not isinstance(data, dict):
            return ""
        target = self._link_target(data.get("Stream"), self.base_url)
        return self._follow_redirect(target, page_url) if target else ""

    def get_sources(self, item):
        page = self._local_url(item.get("url"))
        if not page or not _MOVIE_PATH.fullmatch(urlparse(page).path):
            return []
        hosters = self._hosters(page)
        if not hosters:
            return []
        self.token.check()
        with ThreadPoolExecutor(max_workers=min(4, len(hosters))) as pool:
            targets = list(pool.map(lambda host: self._mirror_target(host["rel"], page), hosters))
        sources, seen = [], set()
        for host, target in zip(hosters, targets):
            self.token.check()
            if not target or target in seen:
                continue
            seen.add(target)
            source = self.make_source(target, label="%s (kinox)" % host["name"],
                                      referer=page, quality_text=host["name"])
            if source:
                source["added"] = host["date"]
                sources.append(source)
        return sources
