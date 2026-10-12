"""StreamKiste public movie pages and the normal DeVideoSRC source API."""
import json
import re
from urllib.parse import urlencode, urlparse

from lib.scrapers import register
from lib.scrapers.base import BaseScraper
from lib.scrapers.html_catalog import http_url, parse_html


@register
class StreamKisteScraper(BaseScraper):
    name = "streamkiste"
    enabled_setting = "streamkiste"
    base_url = "https://streamkiste.bid"
    language = "DE"
    media_types = ("movie",)
    retries = 1
    timeout = 8
    sources_ttl = 300

    def search(self, query, media_type="movie"):
        if media_type != "movie" or not (query or "").strip():
            return []
        url = self.base_url + "/?" + urlencode({
            "do": "search", "subaction": "search", "story": query.strip()})
        root = parse_html(self.fetch_text(url, max_bytes=1024 * 1024))
        results, seen = [], set()
        for card in root.walk():
            if not card.has_class("movie-preview"):
                continue
            heading = card.find_class("movie-title")
            anchor = heading.find_tag("a") if heading else None
            if anchor is None:
                continue
            target = http_url(self.base_url, anchor.attrs.get("href"), same_host=True)
            if not target or target in seen or not re.match(r"^/movie/\d+-", urlparse(target).path):
                continue
            title = anchor.text()
            if not title:
                continue
            meta = card.find_class("movie-release")
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

    def _embed_sources(self, embed):
        parsed = urlparse(embed)
        # Only follow the observed movie player endpoint, never download frames.
        match = re.fullmatch(r"/movie/(tt\d+)", parsed.path)
        if parsed.hostname != "devideosrc.co" or not match:
            return []
        html = self.fetch_text(embed, headers=self.headers(referer=self.base_url + "/"),
                               max_bytes=256 * 1024)
        # This is the public per-page request payload; no JS evaluation or
        # token generation. If the public contract changes, return no sources.
        payload = re.search(r"body\s*:\s*JSON\.stringify\(\s*\{([^{}]+)\}", html)
        if not payload:
            return []
        body = payload.group(1)
        kind = re.search(r"\btype\s*:\s*['\"]movie['\"]", body)
        identifier = re.search(r"\bid\s*:\s*['\"](tt\d+)['\"]", body)
        token = re.search(r"\btoken\s*:\s*['\"]([A-Za-z0-9._-]+)['\"]", body)
        if not kind or not identifier or not token or identifier.group(1) != match.group(1):
            return []
        data = self.fetch_json(
            "https://devideosrc.co/api/embed-links", method="POST",
            headers=self.headers(referer=embed, **{"Content-Type": "application/json"}),
            data=json.dumps({"type": "movie", "id": identifier.group(1), "token": token.group(1)}),
            max_bytes=256 * 1024)
        if not isinstance(data, dict) or data.get("ok") is not True:
            return []
        entries = data.get("sources")
        if not isinstance(entries, list):
            return []
        out = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            url = http_url(embed, entry.get("url"))
            if not url or not (entry.get("embed") is True or entry.get("kind") in ("embed", "direct")):
                continue
            label = entry.get("name") or urlparse(url).hostname
            source = self.make_source(url, label="%s (StreamKiste)" % label,
                                      language="DE", referer=embed)
            if source:
                out.append(source)
        return out

    def get_sources(self, item):
        page = http_url(self.base_url, item.get("url"), same_host=True)
        if not page or not re.match(r"^/movie/\d+-", urlparse(page).path):
            return []
        root = parse_html(self.fetch_text(page, max_bytes=1024 * 1024))
        sources, seen = [], set()
        for node in root.walk():
            if node.tag != "iframe":
                continue
            embed = http_url(page, node.attrs.get("src"))
            parsed = urlparse(embed)
            if parsed.hostname != "devideosrc.co" or not re.fullmatch(r"/movie/tt\d+", parsed.path):
                continue
            if embed in seen:
                continue
            seen.add(embed)
            sources.extend(self._embed_sources(embed))
            if len(seen) >= 2:
                break
        unique = {}
        for source in sources:
            unique.setdefault(source["url"], source)
        return list(unique.values())[:16]
