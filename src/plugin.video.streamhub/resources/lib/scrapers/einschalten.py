"""Einschalten's public catalog and player-link JSON endpoints."""
import json
import re
from urllib.parse import urlparse

from lib.scrapers import register
from lib.scrapers.base import BaseScraper
from lib.scrapers.html_catalog import http_url


@register
class EinschaltenScraper(BaseScraper):
    name = "einschalten"
    enabled_setting = "einschalten"
    base_url = "https://einschalten.in"
    language = "DE"
    media_types = ("movie",)
    retries = 1
    timeout = 8
    sources_ttl = 300

    def search(self, query, media_type="movie"):
        if media_type != "movie" or not (query or "").strip():
            return []
        data = self.fetch_json(
            self.base_url + "/api/search", method="POST",
            data=json.dumps({"query": query.strip(), "pageNumber": 1}),
            headers=self.headers(**{"Content-Type": "application/json", "Accept": "application/json"}),
            max_bytes=256 * 1024)
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        results, seen = [], set()
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            movie_id = str(row.get("id", ""))
            title = row.get("title")
            if not re.fullmatch(r"[1-9]\d{0,9}", movie_id) or movie_id in seen:
                continue
            if not isinstance(title, str) or not title.strip():
                continue
            released = row.get("releaseDate")
            year = re.match(r"(?:19|20)\d{2}(?=-)", released) if isinstance(released, str) else None
            poster = row.get("posterPath")
            image = ("https://image.tmdb.org/t/p/w500" + poster
                     if isinstance(poster, str) and re.fullmatch(r"/[\w.-]+", poster) else None)
            seen.add(movie_id)
            results.append({"title": title.strip(), "year": int(year.group()) if year else None,
                            "id": movie_id, "url": self.base_url + "/movies/" + movie_id,
                            "poster": image, "provider": self.name})
            if len(results) >= 20:
                break
        return results

    def get_sources(self, item):
        page = http_url(self.base_url, item.get("url"), same_host=True)
        match = re.fullmatch(r"/movies/([1-9]\d{0,9})", urlparse(page).path)
        if not match:
            return []
        data = self.fetch_json(
            self.base_url + "/api/movies/" + match.group(1) + "/watch",
            headers=self.headers(referer=page, Accept="application/json"),
            max_bytes=64 * 1024)
        if not isinstance(data, dict):
            return []
        url = http_url(page, data.get("streamUrl"))
        if not url:
            return []
        release = data.get("releaseName")
        source = self.make_source(
            url, label="%s (Einschalten)" % urlparse(url).hostname,
            quality_text=release if isinstance(release, str) else "", referer=page)
        return [source] if source else []
