"""HDfilme public search cards and explicitly listed player mirrors."""
import re
from urllib.parse import urlencode, urlparse

from lib.scrapers import register
from lib.scrapers.base import BaseScraper
from lib.scrapers.html_catalog import http_url, parse_html


@register
class HDFilmeScraper(BaseScraper):
    name = "hdfilme"
    enabled_setting = "hdfilme"
    base_url = "https://hd-filme.lat"
    language = "DE"
    media_types = ("movie",)
    retries = 1
    timeout = 8

    def search(self, query, media_type="movie"):
        if media_type != "movie" or not (query or "").strip():
            return []
        url = self.base_url + "/?" + urlencode({
            "do": "search", "subaction": "search", "story": query.strip()})
        root = parse_html(self.fetch_text(url, max_bytes=1024 * 1024))
        results, seen = [], set()
        for card in root.walk():
            if not card.has_class("movie-short-card"):
                continue
            heading = card.find_class("movie-title")
            anchor = heading.find_tag("a") if heading else None
            if anchor is None:
                continue
            title = anchor.text()
            target = http_url(self.base_url, anchor.attrs.get("href"), same_host=True)
            if not target or target in seen or not urlparse(target).path.endswith(".html"):
                continue
            # Series are not advertised until their episode mapping is supported.
            if not title or re.search(r"\b(?:Staffel|Staffeln|Episode)\s*\d", title, re.I):
                continue
            meta = card.find_class("movie-meta")
            year = re.search(r"\b(?:19|20)\d{2}\b", meta.text() if meta else "")
            poster = card.find_tag("img")
            image = http_url(self.base_url, (poster.attrs.get("data-src") or
                             poster.attrs.get("src")) if poster else "")
            seen.add(target)
            results.append({"title": title, "year": int(year.group()) if year else None,
                            "url": target, "poster": image or None, "provider": self.name})
            if len(results) >= 20:
                break
        return results

    def get_sources(self, item):
        page = http_url(self.base_url, item.get("url"), same_host=True)
        if not page:
            return []
        root = parse_html(self.fetch_text(page, max_bytes=1024 * 1024))
        sources, seen = [], set()
        for group in root.walk():
            if not (group.has_class("mirrors") or group.attrs.get("id") == "fsMirrors"):
                continue
            for node in group.walk():
                url = http_url(page, node.attrs.get("data-link"))
                host = (urlparse(url).hostname or "").lower()
                if not url or url in seen or "trailer" in node.text().lower():
                    continue
                if host in ("youtube.com", "www.youtube.com", "youtu.be"):
                    continue
                seen.add(url)
                source = self.make_source(url, label="%s (HDfilme)" % host,
                                          language="DE", referer=page)
                if source:
                    sources.append(source)
        return sources[:16]
