"""Optional, bounded VOD host recovery driven by Kodi's real caching state.

Network work stays in one daemon worker. Player callbacks only queue events;
all session changes and playback commands happen on the service loop.
"""
import json
import math
import queue
import threading
import time
import uuid
from urllib.parse import urlsplit

import xbmc
import xbmcgui

from streamhub.language import matching_audio_stream, normalize_language, provider_matches_language
from streamhub.playback import configure_playback

PROPERTY = "streamhub_failover_pending"
_CONTROLLER = None
_BUFFER_SECONDS = 20
_START_GRACE = 30
_SEEK_GRACE = 15
_COOLDOWN = 60
_MAX_TRIES = 6
_MAX_HANDOFFS = 3
_OPEN_TIMEOUT = 25
_AUDIO_GRACE = 8
_RESOLVE_TIMEOUT = 25


def _setting(name, default=""):
    try:
        from streamhub.utils import getSetting
        return getSetting(name) or default
    except Exception:
        return default


def _home():
    return xbmcgui.Window(10000)


def _clean(url):
    return str(url or "").split("|", 1)[0]


def _number(value, default=0):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else default
    except (ValueError, TypeError):
        return default


def _descriptor(host):
    if not isinstance(host, dict) or not isinstance(host.get("url"), str):
        return None
    if not host["url"] or len(host["url"]) > 8192:
        return None
    # Keep provider/embed identity, never a cached signed resolver result.
    result = {}
    for key in ("url", "referer", "resolver", "provider", "host", "label", "lang", "language", "quality"):
        value = host.get(key)
        if isinstance(value, str) and len(value) <= 8192:
            result[key] = value
    if isinstance(host.get("languages"), (list, tuple)):
        result["languages"] = [v[:256] for v in host["languages"][:16] if isinstance(v, str)]
    return result


def _host_id(host):
    return _clean((host or {}).get("url"))


def announce(movie, host, all_hosts, configured_url):
    """Transfer a bounded candidate list from the plugin to its service."""
    current = _descriptor(host)
    if not current or not _clean(configured_url):
        _home().clearProperty(PROPERTY)
        return
    pool, seen = [], set()
    for candidate in list(all_hosts or [])[:128]:
        candidate = _descriptor(candidate)
        if candidate and _host_id(candidate) not in seen:
            seen.add(_host_id(candidate))
            pool.append(candidate)
        if len(pool) >= 32:
            break
    try:
        from streamhub.resume import key_for
        key = key_for(movie)
    except Exception:
        key = ""
    payload = {"session": uuid.uuid4().hex, "key": key,
               "title": str(movie.get("title") or movie.get("name") or "StreamHub")[:512],
               "current": current, "hosts": pool, "url": configured_url,
               "created": time.time(), "announced_at": time.monotonic()}
    _home().setProperty(PROPERTY, json.dumps(payload))


def transition_active():
    return bool(_CONTROLLER and _CONTROLLER._transition is not None)


def _notify(string_id, fallback):
    try:
        import xbmcaddon
        message = xbmcaddon.Addon("plugin.video.streamhub").getLocalizedString(string_id) or fallback
        xbmcgui.Dialog().notification("StreamHub", message, time=4000)
    except Exception:
        pass


def _rpc(method, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(payload))).get("result")
    except Exception:
        return None


class FailoverPlayer(xbmc.Player):
    def __init__(self):
        super(FailoverPlayer, self).__init__()
        global _CONTROLLER
        _CONTROLLER = self
        self._events = queue.SimpleQueue()
        self._job = None
        self._session = None
        self._transition = None
        self._closed = False
        self._cycle = 0
        self._paused = False
        self._own_pause = 0
        self._own_seek = False
        self._silenced = False
        self._reset_session()

    def _reset_session(self):
        self._session = None
        self._transition = None
        self._base = None
        self._expected = ""
        self._current = None
        self._attempted = set()
        self._media_seen = set()
        self._tries = 0
        self._handoffs = 0
        self._exhausted = False
        self._av = False
        self._paused = False
        self._buffer_since = None
        self._last_pos = None
        self._cooldown_until = 0
        self._grace_until = 0
        self._initial_audio_until = 0
        self._initial_audio_done = False
        self._announced_at = 0
        self._own_pause = 0
        self._own_seek = False
        self._silenced = False

    def _event(self, name, target=None):
        try:
            path = _clean(self.getPlayingFile())
        except Exception:
            path = ""
        self._events.put((name, path, time.monotonic(), target))

    def onPlayBackStarted(self): self._event("started")
    def onAVStarted(self): self._event("av")
    def onPlayBackStopped(self): self._event("stopped")
    def onPlayBackEnded(self): self._event("ended")
    def onPlayBackError(self): self._event("error")
    def onPlayBackPaused(self): self._event("paused")
    def onPlayBackResumed(self): self._event("resumed")
    def onPlayBackSeek(self, time, seekOffset): self._event("seek", _number(time) / 1000)
    def onPlayBackSeekChapter(self, chapter): self._event("seek")
    def onPlayBackSpeedChanged(self, speed):
        if speed != 1:
            self._event("speed")

    def _cancel_job(self):
        if self._job:
            self._job["cancelled"] = True
            self._job["stop"].set()

    def _discard(self):
        self._cancel_job()
        self._cycle += 1
        self._reset_session()

    def _pickup(self, now):
        try:
            raw = _home().getProperty(PROPERTY)
            if not raw:
                return
            _home().clearProperty(PROPERTY)
            data = json.loads(raw) if len(raw) <= 512 * 1024 else None
            if not isinstance(data, dict) or not data.get("session"):
                return
            if not isinstance(data.get("hosts"), list) or not _descriptor(data.get("current")):
                return
            if abs(time.time() - _number(data.get("created"))) > 120:
                return
            self._discard()
            self._session = data
            self._current = data["current"]
            self._expected = _clean(data.get("url"))
            self._media_seen.add(self._expected)
            self._announced_at = _number(data.get("announced_at"), now)
            self._attempted.add(_host_id(self._current))
            self._grace_until = now + _START_GRACE
            self._initial_audio_until = now + _START_GRACE
        except Exception:
            pass

    def _playing(self):
        try:
            return self.isPlayingVideo(), _clean(self.getPlayingFile())
        except Exception:
            return False, ""

    def _conditions(self):
        def visible(name):
            try:
                return bool(xbmc.getCondVisibility(name))
            except Exception:
                return False
        return {"caching": visible("Player.Caching"),
                "paused": visible("Player.Paused"),
                "seeking": visible("Player.Seeking"),
                "speed": visible("Player.Forwarding") or visible("Player.Rewinding"),
                "canseek": visible("Player.SeekEnabled")}

    def _drain_events(self, now):
        while not self._events.empty():
            name, path, event_at, target_position = self._events.get()
            if not self._session:
                continue
            if event_at < self._announced_at:
                continue
            transition = self._transition
            if name == "paused" and self._own_pause:
                self._own_pause -= 1
                continue
            if name == "speed" and self._silenced:
                continue
            if (name == "seek" and transition and not transition.get("waiting")
                    and target_position is not None
                    and abs(target_position - transition.get("position", -100)) <= 5):
                # Native StartOffset can produce the callback before AVReady,
                # without any Python seek request.
                self._own_seek = False
                continue
            if name in ("paused", "resumed", "seek", "speed"):
                self._paused = name == "paused"
                self._cancel_job()
                self._buffer_since = None
                self._grace_until = now + _SEEK_GRACE
                if transition:
                    # User interaction takes control immediately; never restart
                    # playback after an explicit stop/seek/pause.
                    self._discard()
                continue
            if name in ("stopped", "ended", "error"):
                if not transition and path and path != self._expected:
                    continue
                if transition:
                    # VideoPlayer usually replaces in place (no stop). A
                    # different engine can close the previous player first.
                    # Ignore only that bounded pre-start close, once.
                    if (name == "stopped" and not transition.get("started") and not transition.get("old_stop")
                            and event_at <= transition.get("old_stop_until", 0)
                            and path != transition.get("url")):
                        transition["old_stop"] = True
                        continue
                    if name in ("ended", "error"):
                        self._reject(now)
                    else:
                        self._discard()
                else:
                    self._discard()
                continue
            if name in ("started", "av"):
                target = transition.get("url") if transition else self._expected
                if path and path != target:
                    # A delayed old event during replacement is harmless;
                    # an unrelated file must cancel this recovery session.
                    if not transition or path != self._expected:
                        self._discard()
                    continue
                if transition:
                    transition["started"] = True
                    if name == "av":
                        transition["av"] = True
                        transition["audio_until"] = now + _AUDIO_GRACE
                        transition["seek_after"] = now + 1
                elif name == "av":
                    self._av = True
                    self._initial_audio_until = now + _AUDIO_GRACE

    def _audio(self, wanted):
        active = _rpc("Player.GetActivePlayers")
        player_id = next((p.get("playerid") for p in (active or [])
                          if isinstance(p, dict) and p.get("type") == "video"), None)
        if player_id is None:
            return None, None
        result = _rpc("Player.GetProperties", {"playerid": player_id,
                      "properties": ["audiostreams", "currentaudiostream"]}) or {}
        streams = result.get("audiostreams")
        index = matching_audio_stream(streams, wanted)
        current = result.get("currentaudiostream") or {}
        # Presence alone is insufficient: make sure Kodi selected the requested
        # track before accepting a replacement.
        current_index = matching_audio_stream([current], wanted)
        if current_index is not None:
            return current_index, True
        if index is not None and current_index != index:
            self.setAudioStream(index)
            return index, False
        return index, index is not None and current_index == index

    def _wanted(self):
        return _setting("pref_lang", "de").strip().lower()

    def _candidate(self):
        if self._tries >= _MAX_TRIES:
            return None
        wanted = self._wanted()
        for host in self._session.get("hosts", [])[:32]:
            host = _descriptor(host)
            if (host and _host_id(host) not in self._attempted
                    and provider_matches_language(host, wanted)):
                self._attempted.add(_host_id(host))
                self._tries += 1
                return host
        return None

    def _launch(self, host, now, restoring=False):
        if self._job is not None:
            return False
        job = {"host": host, "restoring": restoring, "session": self._session["session"],
               "cycle": self._cycle, "stop": threading.Event(), "done": threading.Event(),
               "result": None, "cancelled": False, "deadline": now + _RESOLVE_TIMEOUT}
        def worker():
            try:
                from streamhub.movies import resolve_failover_candidate
                result = resolve_failover_candidate(dict(host), job["stop"])
                if not job["stop"].is_set():
                    job["result"] = result
            except Exception:
                pass
            finally:
                job["done"].set()
        self._job = job
        thread = threading.Thread(target=worker, name="StreamHubFailover", daemon=True)
        job["thread"] = thread
        thread.start()
        return True

    def _next(self, now):
        if self._job is not None or not self._session:
            return
        host = self._candidate()
        if host:
            self._launch(host, now)
        elif self._transition and self._base and not self._base.get("restored"):
            self._base["restored"] = True
            _notify(30096, "Returning to the previous host.")
            self._launch(self._base["host"], now, restoring=True)
        elif self._transition:
            self._restore_failed()
        else:
            self._exhausted = True
            self._buffer_since = None
            _notify(30095, "No compatible replacement found.")

    def _restore_failed(self):
        # A rejected language/duration must not be left playing silently.
        try:
            self.stop()
        except Exception:
            pass
        self._discard()
        _notify(30097, "Playback could not be restored. Please choose another host.")

    def _begin(self, job, now):
        try:
            if not job["restoring"] and not provider_matches_language(job["host"], self._wanted()):
                self._next(now)
                return
            url, headers = job["result"]
            if urlsplit(_clean(url)).scheme not in ("http", "https"):
                raise ValueError("invalid media URL")
            if not job["restoring"]:
                if _clean(url) in self._media_seen:
                    self._next(now)
                    return
                self._media_seen.add(_clean(url))
            if self._base is None:
                self._base = {"host": self._current, "position": _number(self.getTime()),
                              "total": _number(self.getTotalTime()), "restored": False}
            base = self._base
            item = xbmcgui.ListItem(label=self._session["title"])
            item.setProperty("IsPlayable", "true")
            configured = configure_playback(item, url, headers)
            item.setProperty("StartOffset", str(base["position"]))
            try:
                item.getVideoInfoTag().setTitle(self._session["title"])
            except Exception:
                pass
            self._transition = {"url": _clean(configured), "host": job["host"],
                                "restoring": job["restoring"], "deadline": now + _OPEN_TIMEOUT,
                                "started": False, "av": False, "seek_requested": False,
                                "old_stop_until": now + 3, "old_stop": False,
                                "position": base["position"], "total": base["total"]}
            self._paused = False
            self._own_pause = 0
            self._own_seek = False
            self._silenced = False
            self.play(configured, item)
        except Exception:
            if job["restoring"]:
                self._restore_failed()
            else:
                self._next(now)

    def _reject(self, now):
        transition = self._transition
        if not transition:
            return
        if transition.get("restoring"):
            self._restore_failed()
            return
        # Silence the incompatible candidate while finding a replacement.
        try:
            if self.isPlaying() and not self._conditions()["paused"]:
                self._own_pause += 1
                self._silenced = True
                self.pause()
        except Exception:
            pass
        self._transition = {"url": transition.get("url"), "waiting": True,
                            "started": True, "deadline": now + _RESOLVE_TIMEOUT,
                            "restoring": False}
        self._next(now)

    def _accept(self, now):
        transition = self._transition
        self._expected = transition["url"]
        self._current = transition["host"]
        self._attempted.add(_host_id(self._current))
        self._cooldown_until = now + _COOLDOWN
        self._grace_until = now + _START_GRACE
        self._buffer_since = None
        self._last_pos = _number(self.getTime())
        self._av = True
        self._initial_audio_done = True
        if transition["restoring"]:
            self._exhausted = True
        else:
            self._handoffs += 1
            _notify(30094, "Playback continued on another host.")
        # ResumeTracker must not infer a new episode or write the rejected
        # candidate's zero/short position. Publish only after validation.
        if _setting("resume_enabled") != "false" and self._session.get("key"):
            _home().setProperty("streamhub_playing", json.dumps({
                "key": self._session["key"], "title": self._session["title"],
                "resume": self._last_pos, "managed_resume": True,
                "expected_url": self._expected}))
        self._transition = None
        self._base = None
        self._own_seek = False
        self._own_pause = 0

    def _validate(self, now, playing, path):
        transition = self._transition
        if transition.get("waiting"):
            if now >= transition["deadline"]:
                self._cancel_job()
                self._restore_failed()
                return
            self._next(now)
            return
        if playing and path not in (self._expected, transition["url"]):
            self._discard()
            return
        if transition.get("old_stop") and not playing and now > transition["old_stop_until"]:
            self._discard()
            return
        if now >= transition["deadline"]:
            self._reject(now)
            return
        if not transition.get("av") or not playing or path != transition["url"]:
            return
        total = _number(self.getTotalTime())
        old_total = transition["total"]
        if old_total and total and abs(total - old_total) > max(60, old_total * 0.05):
            self._reject(now)
            return
        wanted = self._wanted()
        requires_audio = bool(normalize_language(wanted)) and not transition["restoring"]
        if requires_audio:
            _, confirmed = self._audio(wanted)
            if not confirmed:
                if now >= transition["audio_until"]:
                    self._reject(now)
                return
        position = transition["position"]
        current = _number(self.getTime())
        # The configured start offset is usually already applied. Only fall
        # back once, after AV startup, and require positive acknowledgement.
        if transition.get("resume_confirmed"):
            if not self._conditions()["caching"] and current > transition["progress_from"] + 0.25:
                self._accept(now)
            return
        if abs(current - position) <= 5 or (position == 0 and current < 10):
            transition["resume_confirmed"] = True
            transition["progress_from"] = current
            return
        if (now >= transition["seek_after"] and not transition["seek_requested"]
                and self._conditions()["canseek"] and total > position):
            transition["seek_requested"] = True
            self._own_seek = True
            self.seekTime(position)

    def sample(self):
        if self._closed:
            return
        now = time.monotonic()
        self._pickup(now)
        self._drain_events(now)
        enabled = _setting("auto_host_failover") == "true"
        if not enabled:
            self._cancel_job()
            self._buffer_since = None
            if self._transition:
                self._discard()
        job = self._job
        if job and not job["done"].is_set() and now >= job["deadline"]:
            self._cancel_job()
        result = None
        if job and job["done"].is_set():
            self._job = None
            if (not job["cancelled"] and self._session
                    and job["session"] == self._session["session"] and job["cycle"] == self._cycle):
                result = job
        if not self._session:
            return
        playing, path = self._playing()
        conditions = self._conditions()
        if self._transition:
            # A user can open a different add-on before its Python callback
            # arrives. Check ownership before a ready worker may call play().
            if playing and path not in (self._expected, self._transition.get("url")):
                self._discard()
                return
            if result:
                if result["result"]:
                    self._begin(result, now)
                elif result["restoring"]:
                    self._restore_failed()
                else:
                    self._next(now)
            if self._transition:
                self._validate(now, *self._playing())
            return
        if not playing or path != self._expected:
            self._cancel_job()
            self._buffer_since = None
            if playing and now >= self._grace_until:
                self._discard()
            return
        if self._av and not self._initial_audio_done:
            wanted = self._wanted()
            if not normalize_language(wanted):
                self._initial_audio_done = True
            elif now <= self._initial_audio_until:
                try:
                    _, confirmed = self._audio(self._wanted())
                    self._initial_audio_done = bool(confirmed)
                except Exception:
                    pass
            else:
                self._initial_audio_done = True
                _notify(30098, "Preferred audio language could not be confirmed.")
        position = _number(self.getTime())
        progressed = self._last_pos is not None and position > self._last_pos + 0.75
        self._last_pos = position
        if (not enabled or not self._av or self._paused or conditions["paused"]
                or conditions["seeking"] or conditions["speed"] or progressed
                or not conditions["caching"] or now < max(self._grace_until, self._cooldown_until)):
            self._cancel_job()
            self._buffer_since = None
            return
        if self._exhausted or self._handoffs >= _MAX_HANDOFFS:
            return
        if position > 0 and not conditions["canseek"]:
            return
        if self._buffer_since is None:
            self._buffer_since = now
        if now - self._buffer_since < _BUFFER_SECONDS:
            return
        if result:
            if result["result"]:
                self._begin(result, now)
            else:
                self._next(now)
        elif self._job is None:
            if self._tries == 0:
                _notify(30093, "Buffering: checking another host…")
            self._next(now)

    def shutdown(self):
        self._closed = True
        self._discard()
        global _CONTROLLER
        if _CONTROLLER is self:
            _CONTROLLER = None
