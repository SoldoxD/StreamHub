"""SerienStream's public catalog, episode pages and ordinary host redirects."""
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse

from lib.scrapers import register
from lib.scrapers.base import BaseScraper
from lib.scrapers.html_catalog import http_url, parse_html


_SERIES = re.compile(r"^/serie/[^/]+$")
_SEASON = re.compile(r"^(/serie/[^/]+)/staffel-(\d+)$")
_EPISODE = re.compile(r"^(/serie/[^/]+)/staffel-(\d+)/episode-(\d+)$")
_PAGE_LIMIT = 1024 * 1024
_LANGUAGES = {"1": "DE", "2": "EN", "3": "DE-SUB"}


def _number(value, minimum=0):
    if isinstance(value, bool) or not re.fullmatch(r"\d{1,5}", str(value)):
        return None
    number = int(value)
    return number if number >= minimum else None


@register
class SerienStreamScraper(BaseScraper):
    name = "serienstream"
    enabled_setting = "serienstream"
    base_url = "https://serienstream.to"
    language = "DE"
    media_types = ("tvshow",)
    retries = 1
    timeout = 8
    sources_ttl = 300

    def _local_url(self, value):
        return http_url(self.base_url, value, same_host=True)

    def _series_url(self, item):
        url = self._local_url(item.get("url"))
        if url and _SERIES.fullmatch(urlparse(url).path.rstrip("/")):
            return self.base_url + urlparse(url).path.rstrip("/")
        return ""

    def _page(self, url):
        return parse_html(self.fetch_text(url, max_bytes=_PAGE_LIMIT))

    def search(self, query, media_type="tvshow"):
        if media_type != "tvshow" or not (query or "").strip():
            return []
        url = self.base_url + "/suche?" + urlencode({"term": query.strip()})
        results = self._page(url).find_class("search-results")
        if results is None:
            return []
        out, seen = [], set()
        # Search also contains episode/person/FAQ matches; only series cards count.
        for card in results.walk():
            if not card.has_class("cover-card"):
                continue
            anchor = card.find_class("show-cover")
            heading = card.find_class("show-title")
            target = self._local_url(anchor.attrs.get("href")) if anchor else ""
            if not target or not _SERIES.fullmatch(urlparse(target).path) or target in seen:
                continue
            title = heading.text() if heading else ""
            if not title:
                continue
            poster = card.find_tag("img")
            image = http_url(self.base_url, (poster.attrs.get("data-src") or
                            poster.attrs.get("src")) if poster else "")
            seen.add(target)
            out.append({"title": title, "url": target, "year": None,
                        "poster": image or None, "provider": self.name})
            if len(out) >= 20:
                break
        return out

    def _episodes(self, root, series_url, season):
        path = urlparse(series_url).path
        out = {}
        for row in root.walk():
            if not row.has_class("episode-row"):
                continue
            # Read a literal navigation target only; never execute inline JS.
            match = re.fullmatch(r"\s*window\.location(?:\.href)?\s*=\s*(['\"])([^'\"]+)\1\s*;?\s*",
                                 row.attrs.get("onclick") or "")
            anchor = row.find_tag("a")
            url = self._local_url(match.group(2) if match else
                                  anchor.attrs.get("href") if anchor else "")
            entry = _EPISODE.fullmatch(urlparse(url).path) if url else None
            if not entry or entry.group(1) != path or int(entry.group(2)) != season:
                continue
            number = _number(entry.group(3), 1)
            if number is None or number in out:
                continue
            title = row.find_class("episode-title-ger")
            if title is None or not title.text():
                title = row.find_class("episode-title-eng")
            out[number] = {"episode_number": number, "season_number": season,
                           "name": title.text() if title else "Episode %s" % number,
                           "url": url}
        return [out[number] for number in sorted(out)]

    def get_details(self, item):
        url = self._series_url(item)
        if not url:
            return {}
        season = _number(item.get("season"))
        page_url = url + "/staffel-%s" % season if season is not None else url
        root = self._page(page_url)
        nav = next((node for node in root.walk() if node.attrs.get("id") == "season-nav"), None)
        seasons = {}
        for anchor in nav.walk() if nav else ():
            if anchor.tag != "a":
                continue
            target = self._local_url(anchor.attrs.get("href"))
            match = _SEASON.fullmatch(urlparse(target).path) if target else None
            if not match or match.group(1) != urlparse(url).path:
                continue
            number = _number(match.group(2))
            if number is None:
                continue
            episodes = self._episodes(root, url, number)
            seasons[number] = {"season_number": number,
                               "name": "Filme / Specials" if number == 0 else "Staffel %s" % number,
                               "id": target, "episode_count": len(episodes) if episodes else None}
        heading = root.find_tag("h1")
        overview = root.find_class("description-text")
        return {"title": heading.text() if heading else item.get("title"),
                "overview": overview.text() if overview else "",
                "seasons": [seasons[number] for number in sorted(seasons)],
                "season_ids": {str(number): value["id"] for number, value in seasons.items()}}

    def get_episodes(self, series, season_number):
        url = self._series_url(series)
        season = _number(season_number)
        if not url or season is None:
            return []
        return self._episodes(self._page(url + "/staffel-%s" % season), url, season)

    def _host_redirect(self, url, referer):
        seen = set()
        for _ in range(3):
            self.token.check()
            if url in seen:
                return ""
            seen.add(url)
            response = self.fetch(url, headers=self.headers(referer=referer),
                                  allow_redirects=False, max_bytes=64 * 1024)
            if response is None or response.status_code not in (301, 302, 303, 307, 308):
                # Login, captcha and link pages are not playable host addresses.
                return ""
            target = http_url(url, response.headers.get("Location"))
            if not target:
                return ""
            if urlparse(target).hostname != urlparse(self.base_url).hostname:
                return target
            if urlparse(target).path != "/r":
                return ""
            url = target
        return ""

    def get_sources(self, item):
        url = self._series_url(item)
        season, episode = _number(item.get("season")), _number(item.get("episode"), 1)
        if not url or season is None or episode is None:
            return []
        episode_url = url + "/staffel-%s/episode-%s" % (season, episode)
        root = self._page(episode_url)
        entries, seen = [], set()
        for button in root.walk():
            if not button.has_class("link-box"):
                continue
            target = self._local_url(button.attrs.get("data-play-url"))
            if not target or target in seen or urlparse(target).path != "/r":
                continue
            seen.add(target)
            language = _LANGUAGES.get(button.attrs.get("data-language-id"), "OV")
            label = button.attrs.get("data-provider-name") or button.text() or "Hoster"
            entries.append((target, language, label))
            if len(entries) >= 12:
                break
        if not entries:
            return []
        # Only provider redirects are requested here, never host pages or video.
        with ThreadPoolExecutor(max_workers=min(4, len(entries))) as pool:
            targets = list(pool.map(lambda entry: self._host_redirect(entry[0], episode_url), entries))
        out, seen = [], set()
        for (redirect, language, label), target in zip(entries, targets):
            key = (target, language)
            if not target or key in seen:
                continue
            seen.add(key)
            out.append(self.make_source(target, label="%s (SerienStream)" % label,
                                        language=language, referer=episode_url))
        return out
