"""Movie2k public search results and explicit player mirror links."""
import re
from urllib.parse import urlencode, urlparse

from lib.scrapers import register
from lib.scrapers.base import BaseScraper
from lib.scrapers.html_catalog import http_url, parse_html


@register
class Movie2kScraper(BaseScraper):
    name = "movie2k"
    enabled_setting = "movie2k"
    base_url = "https://movie2k.cx"
    language = "DE"
    media_types = ("movie",)
    retries = 1
    timeout = 8
    sources_ttl = 300

    def _movie_url(self, value):
        url = http_url(self.base_url, value, same_host=True)
        return url if re.fullmatch(r"/stream/[^/]+", urlparse(url).path) else ""

    def search(self, query, media_type="movie"):
        if media_type != "movie" or not (query or "").strip():
            return []
        url = self.base_url + "/search?" + urlencode({"q": query.strip()})
        root = parse_html(self.fetch_text(url, max_bytes=1024 * 1024))
        content = next((node for node in root.walk()
                        if node.attrs.get("id") == "maincontentnew"), None)
        results, seen = [], set()
        if content is None:
            return results
        for card in content.walk():
            if card.tag != "table":
                continue
            heading = card.find_tag("h2")
            anchor = heading.find_tag("a") if heading else None
            if anchor is None:
                continue
            target = self._movie_url(anchor.attrs.get("href"))
            title = anchor.text()
            if not target or target in seen or not title:
                continue
            flags = [node.attrs.get("src", "") for node in heading.walk()
                     if node.tag == "img" and "_flag." in node.attrs.get("src", "")]
            if flags and not any("ger_flag." in flag for flag in flags):
                continue
            image = card.find_tag("img")
            poster = http_url(self.base_url, image.attrs.get("src") if image else "")
            seen.add(target)
            # Search cards do not expose a release year; do not infer one from a title.
            results.append({"title": title, "year": None, "url": target,
                            "poster": poster or None, "provider": self.name})
            if len(results) >= 20:
                break
        return results

    def get_sources(self, item):
        page = self._movie_url(item.get("url"))
        if not page:
            return []
        root = parse_html(self.fetch_text(page, max_bytes=1024 * 1024))
        sources, seen = [], set()
        for group in root.walk():
            if group.attrs.get("id") != "tablemoviesindex2":
                continue
            for anchor in group.walk():
                if anchor.tag != "a" or not re.match(
                        r"\s*return\s+loadMirror\s*\(", anchor.attrs.get("onclick", "")):
                    continue
                url = http_url(page, anchor.attrs.get("href"))
                host = (urlparse(url).hostname or "").lower()
                if not url or url in seen or host in (
                        "movie2k.cx", "youtube.com", "www.youtube.com", "youtu.be"):
                    continue
                quality = " ".join(node.attrs.get("alt", "") for node in anchor.walk()
                                   if node.tag == "img")
                seen.add(url)
                source = self.make_source(url, label="%s (Movie2k)" % host,
                                          quality_text=quality, referer=page)
                if source:
                    sources.append(source)
                if len(sources) >= 16:
                    return sources
        return sources
