
import io
import os
import re
import sys
import time
import zipfile

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


ADDON_ID = "plugin.video.streamhub"
MANIFEST_URL = "https://soldoxd.github.io/StreamHub/addons.xml"
ZIP_URL_FMT = "https://soldoxd.github.io/StreamHub/plugin.video.streamhub/plugin.video.streamhub-{}.zip"
CHECK_INTERVAL_HOURS = 24
STARTUP_DELAY = 30


def _log(msg, level=xbmc.LOGINFO):
	xbmc.log("[StreamHub-Updater] " + str(msg), level)


def _addon():
	return xbmcaddon.Addon(ADDON_ID)


def _setting(key, default=""):
	try:
		return _addon().getSetting(key) or default
	except Exception:
		return default


def _installed_version():
	try:
		return _addon().getAddonInfo("version")
	except Exception:
		return "0.0.0"


def _addon_path():
	return xbmcvfs.translatePath(_addon().getAddonInfo("path"))


def _ensure_path_for_import():
	resources = os.path.join(_addon_path(), "resources")
	if resources not in sys.path:
		sys.path.insert(0, resources)


def _http_session():
	_ensure_path_for_import()
	try:
		import requests as _r
		return _r.Session()
	except Exception:
		pass
	from streamhub import _http as _r
	return _r.Session()


MD5_URL = "https://soldoxd.github.io/StreamHub/addons.xml.md5"


def _get(url, timeout, tries=3):
	sess = _http_session()
	last = None
	for attempt in range(tries):
		try:
			r = sess.request("GET", url, timeout=timeout)
			if getattr(r, "status_code", 0) == 200:
				return r
			last = "HTTP %s" % getattr(r, "status_code", "?")
		except Exception as e:
			last = str(e)
		try:
			if xbmc.Monitor().waitForAbort(min(2 ** attempt, 8)):
				break
		except Exception:
			pass
	_log("GET failed (%s): %s" % (url, last), xbmc.LOGWARNING)
	return None


def _state_file():
	try:
		from streamhub.utils import translatePath
		import os as _os
		d = translatePath("special://profile/addon_data/%s/" % ADDON_ID)
		return _os.path.join(d, "update_state.json")
	except Exception:
		return None


def _load_state():
	try:
		import json
		with open(_state_file(), "r", encoding="utf-8") as f:
			return json.load(f)
	except Exception:
		return {}


def _save_state(st):
	try:
		import json
		p = _state_file()
		d = os.path.dirname(p)
		if not os.path.isdir(d):
			os.makedirs(d)
		with open(p, "w", encoding="utf-8") as f:
			json.dump(st, f)
	except Exception:
		pass


def _remote_md5():
	r = _get(MD5_URL, timeout=10, tries=2)
	return (r.text.strip() if r else "") or ""


def _fetch_remote_version():
	r = _get(MANIFEST_URL, timeout=10)
	if r is None:
		return None
	m = re.search(
		r'<addon\s+id="' + re.escape(ADDON_ID) + r'"\s+name="[^"]*"\s+version="([^"]+)"',
		r.text,
	)
	return m.group(1) if m else None


def _version_tuple(v):
	out = []
	for x in str(v or "").split("."):
		try:
			out.append(int(x))
		except Exception:
			out.append(0)
	return tuple(out)


def _is_newer(remote, installed):
	return _version_tuple(remote) > _version_tuple(installed)


def _download_zip(version):
	r = _get(ZIP_URL_FMT.format(version), timeout=60, tries=3)
	return r.content if r is not None else None


_REQUIRED = ("%s/addon.xml" % ADDON_ID,
             "%s/resources/main.py" % ADDON_ID,
             "%s/resources/service.py" % ADDON_ID)


def _addon_version_from(text):
	m = re.search(r'<addon\b[^>]*\bid="' + re.escape(ADDON_ID) + r'"[^>]*>', text, re.S)
	if not m:
		m = re.search(r'<addon\b[^>]*>', text or "", re.S)
	if not m:
		return ""
	vm = re.search(r'\bversion="([^"]+)"', m.group(0))
	return vm.group(1) if vm else ""


def _validate_zip(zip_bytes, version):
	"""Fully check the download before it is allowed to touch the install."""
	try:
		z = zipfile.ZipFile(io.BytesIO(zip_bytes))
	except Exception as e:
		return False, "kein gültiges Zip (%s)" % e
	names = z.namelist()
	if not names:
		return False, "leeres Zip"
	for n in names:
		if n.startswith("/") or ".." in n.replace("\\", "/").split("/"):
			return False, "unsicherer Pfad im Zip: %s" % n
	top = {n.split("/", 1)[0] for n in names}
	if top != {ADDON_ID}:
		return False, "falsche Struktur: %s" % sorted(top)
	for req in _REQUIRED:
		if req not in names:
			return False, "fehlende Datei: %s" % req
	try:
		axml = z.read("%s/addon.xml" % ADDON_ID).decode("utf-8", "ignore")
		found = _addon_version_from(axml)
		if found != version:
			return False, "Versionskonflikt im Zip (%s != %s)" % (found or "?", version)
	except Exception as e:
		return False, "addon.xml unlesbar (%s)" % e
	bad = z.testzip()
	if bad:
		return False, "beschädigte Datei im Zip: %s" % bad
	return True, z


def _install_zip(zip_bytes, version):
	import shutil
	ok, res = _validate_zip(zip_bytes, version)
	if not ok:
		_log("update rejected: %s" % res, xbmc.LOGERROR)
		return False, res
	z = res
	addons_dir = xbmcvfs.translatePath("special://home/addons/")
	live = os.path.join(addons_dir, ADDON_ID)
	backup = live + ".bak"

	try:
		if os.path.isdir(backup):
			shutil.rmtree(backup, ignore_errors=True)
		if os.path.isdir(live):
			shutil.copytree(live, backup)
	except Exception as e:
		_log("backup failed, aborting update: %s" % e, xbmc.LOGERROR)
		return False, "Backup fehlgeschlagen"

	try:
		z.extractall(addons_dir)
	except Exception as e:
		_log("extract failed, rolling back: %s" % e, xbmc.LOGERROR)
		_restore(live, backup)
		return False, "Entpacken fehlgeschlagen"

	# verify the install on disk matches what we intended
	try:
		with open(os.path.join(live, "addon.xml"), "r", encoding="utf-8") as f:
			on_disk = _addon_version_from(f.read())
		req_ok = all(os.path.exists(os.path.join(addons_dir, r.replace("/", os.sep)))
		             for r in _REQUIRED)
		if on_disk != version or not req_ok:
			_log("post-install check failed (disk=%s), rolling back" % on_disk,
			     xbmc.LOGERROR)
			_restore(live, backup)
			return False, "Prüfung nach Installation fehlgeschlagen"
	except Exception as e:
		_log("post-install verify error, rolling back: %s" % e, xbmc.LOGERROR)
		_restore(live, backup)
		return False, "Prüfung fehlgeschlagen"

	shutil.rmtree(backup, ignore_errors=True)
	return True, None


def _restore(live, backup):
	import shutil
	try:
		if not os.path.isdir(backup):
			return
		shutil.rmtree(live, ignore_errors=True)
		os.rename(backup, live)
		_log("rolled back to previous version")
	except Exception as e:
		_log("rollback failed: %s" % e, xbmc.LOGERROR)


def _last_check_ts():
	try:
		s = _setting("_last_update_check", "0")
		return int(s) if s.isdigit() else 0
	except Exception:
		return 0


def _set_last_check_ts(ts):
	try:
		_addon().setSetting("_last_update_check", str(int(ts)))
	except Exception:
		pass


def _playback_active():
	try:
		return _failover_transition_active() or xbmc.Player().isPlaying()
	except Exception:
		# Automatic maintenance can wait if player state is unavailable.
		return True


def check_for_update(force=False):
	# A manual update keeps its existing explicit behavior. Automatic work waits
	# for idle, including when playback begins during the manifest request.
	if not force and _playback_active():
		return None
	installed = _installed_version()

	# cheap change-detector: skip parsing the manifest if the md5 is unchanged
	md5 = ""
	if not force:
		md5 = _remote_md5()
		if _playback_active():
			return None
		st = _load_state()
		if md5 and md5 == st.get("md5") and st.get("known") \
				and not _is_newer(st.get("known"), installed):
			_log("manifest unchanged, up to date")
			return False

	remote = _fetch_remote_version()
	if not force and _playback_active():
		return None
	_log("installed=%s  remote=%s" % (installed, remote))
	if not remote:
		return False
	try:
		st = _load_state()
		# Reuse the checksum already fetched by an automatic check.
		st["md5"] = (_remote_md5() if force else md5) or st.get("md5", "")
		st["known"] = remote
		_save_state(st)
	except Exception:
		pass
	if not _is_newer(remote, installed):
		_log("already up to date")
		return False

	if not force and _playback_active():
		return None
	auto = _setting("auto_update") == "true"
	if not auto and not force:
		ok = xbmcgui.Dialog().yesno(
			"StreamHub Update",
			"Eine neue Version ist verfuegbar:\n\n"
			"  installiert: " + installed + "\n"
			"  neu:         " + remote + "\n\n"
			"Jetzt installieren?",
		)
		if not ok:
			return False

	if not force and _playback_active():
		return None
	xbmcgui.Dialog().notification("StreamHub", "Update %s wird geladen..." % remote, time=4000)
	zip_bytes = _download_zip(remote)
	if not zip_bytes:
		xbmcgui.Dialog().notification("StreamHub", "Download fehlgeschlagen", time=6000)
		return False

	if not force and _playback_active():
		return None
	ok, reason = _install_zip(zip_bytes, remote)
	if not ok:
		xbmcgui.Dialog().notification(
			"StreamHub", "Update fehlgeschlagen: %s" % (reason or ""), time=7000)
		return False

	try:
		xbmc.executebuiltin("UpdateLocalAddons")
	except Exception:
		pass

	# non-blocking: never freeze the service loop on a modal dialog
	xbmcgui.Dialog().notification(
		"StreamHub", "Update %s installiert – Kodi neu starten" % remote,
		time=8000)
	_maybe_offer_restart(remote)
	return True


def _maybe_offer_restart(version):
	"""Offer a one-tap restart where Kodi supports it, so the new service loads."""
	try:
		if xbmc.getCondVisibility("System.Platform.Android"):
			return
		if not xbmc.getCondVisibility("System.Platform.Windows") \
				and not xbmc.getCondVisibility("System.Platform.Linux") \
				and not xbmc.getCondVisibility("System.Platform.OSX"):
			return
		if xbmc.Player().isPlaying():
			return
		if xbmcgui.Dialog().yesno(
				"StreamHub",
				"Update %s installiert.\n\nKodi jetzt neu starten, damit es aktiv wird?"
				% version,
				yeslabel="Neustart", nolabel="Später"):
			xbmc.executebuiltin("RestartApp")
	except Exception:
		pass


def _add_resources_path():
	try:
		p = os.path.join(xbmcvfs.translatePath(_addon().getAddonInfo("path")), "resources")
		if p not in sys.path:
			sys.path.insert(0, p)
	except Exception:
		pass


def _apply_buffer():
	try:
		_add_resources_path()
		from streamhub import buffer
		if buffer.apply_if_changed():
			_log("buffer settings applied")
	except Exception as e:
		_log("buffer apply failed: %s" % e, xbmc.LOGWARNING)


class SettingsMonitor(xbmc.Monitor):
	def __init__(self):
		super(SettingsMonitor, self).__init__()
		self.buffer_changed = False

	def onSettingsChanged(self):
		# The callback can run on a different thread. Apply on the service loop.
		self.buffer_changed = True


def _failover_transition_active():
	try:
		from streamhub.failover import transition_active
		return bool(transition_active())
	except ImportError:
		return False
	except Exception:
		# Preserve the last valid position if handoff state is unavailable.
		return True


class ResumeTracker(xbmc.Player):
	PROP = "streamhub_playing"

	def __init__(self):
		super(ResumeTracker, self).__init__()
		self._reset()

	def _reset(self):
		self._key = None
		self._title = ""
		self._pos = 0
		self._total = 0
		self._seeked = False
		self._last_flush = 0
		self._last_saved = None
		self._pending_resume = 0
		self._av_started = False
		self._sampled = False
		self._seek_after = 0
		self._seek_deadline = 0
		self._seek_requested = False
		self._transition_seen = False
		self._active_file = ""
		self._deferred_av_file = ""

	def _handoff_active(self):
		if _failover_transition_active():
			self._transition_seen = True
			return True
		return False

	def _playing_file(self):
		try:
			return self.getPlayingFile() or ""
		except Exception:
			return ""

	def _pickup(self, managed_only=False, deferred_file=""):
		if self._handoff_active():
			return False
		try:
			from streamhub.utils import home
			import json
			raw = home.getProperty(self.PROP)
			if not raw:
				return False
			d = json.loads(raw)
			if not isinstance(d, dict) or not d.get("key"):
				home.clearProperty(self.PROP)
				return False
			managed = d.get("managed_resume") is True
			if managed_only and not managed:
				return False
			expected = d.get("expected_url") or ""
			playing_file = self._playing_file()
			if (expected and (not isinstance(expected, str) or
					expected.split("|", 1)[0] != playing_file.split("|", 1)[0])):
				return False
			if deferred_file and (not expected or deferred_file != playing_file):
				return False
			pending = max(0, int(d.get("resume") or 0))
			if deferred_file:
				# The old stop callback was suppressed during the transition.
				# Save its last valid sample before adopting the new title.
				self._commit(ended=False)
			self._reset()
			self._key = d.get("key") or None
			self._title = d.get("title", "")
			self._pending_resume = pending
			# The failover player has already verified language, duration and
			# the seek. Its late handoff must not trigger another resume seek.
			self._seeked = managed
			self._av_started = managed
			if managed:
				self._active_file = self._playing_file()
			home.clearProperty(self.PROP)
			return True
		except Exception:
			return False

	def onAVStarted(self):
		if self._handoff_active():
			# A user can open another title while the previous handoff is
			# still active. Keep AV readiness tied to its actual player file.
			self._deferred_av_file = self._playing_file()
			return
		picked = self._pickup()
		playing_file = self._playing_file()
		if (not picked and self._av_started and self._active_file
				and playing_file and self._active_file != playing_file):
			# An unrelated player start has no StreamHub resume announcement.
			self._reset()
			return
		self._active_file = playing_file
		self._av_started = True
		# Kodi may already apply the ListItem resume point. Give it one service
		# tick, then check the actual position before issuing a fallback seek.
		now = time.monotonic()
		self._seek_after = now + 1
		self._seek_deadline = now + 10

	def onPlayBackStarted(self):
		if self._handoff_active():
			return
		if not self._key:
			self._pickup()

	def onPlayBackSeek(self, time, seekOffset):
		if self._handoff_active():
			return
		if not self._key:
			return
		# A user/native seek during startup takes precedence over our fallback.
		# Keep waiting only for acknowledgement of our own requested resume.
		try:
			own_seek = self._seek_requested and abs(float(time) / 1000 - self._pending_resume) <= 5
		except (TypeError, ValueError):
			own_seek = False
		if not own_seek:
			self._seeked = True

	def _maybe_seek(self):
		if self._seeked or not self._key or not self._av_started:
			return
		pos = self._pending_resume
		if pos < 30:
			self._seeked = True
			return
		now = time.monotonic()
		if now < self._seek_after:
			return
		try:
			if not self.isPlaying():
				return
			# Avoid a second seek (and buffer discard) after Kodi resumed itself.
			if self.getTime() >= pos - 5:
				self._seeked = True
			elif now >= self._seek_deadline:
				self._seeked = True
			elif not self._seek_requested and (self.getTotalTime() or 0) > pos:
				self._seek_requested = True
				self.seekTime(pos)
		except Exception:
			if now >= self._seek_deadline:
				self._seeked = True

	def sample(self):
		if self._handoff_active():
			return
		had_transition = self._transition_seen
		managed = self._pickup(managed_only=True)
		if had_transition and not managed:
			deferred = self._deferred_av_file
			try:
				ready = (deferred and deferred == self._playing_file()
						and self.isPlaying())
			except Exception:
				ready = False
			if ready and self._pickup(deferred_file=deferred):
				self._active_file = deferred
				self._av_started = True
				self._seek_after = time.monotonic() + 1
				self._seek_deadline = time.monotonic() + 10
				return
			# Canceled/exhausted handoffs have no accepted replacement packet.
			# Save only the old valid sample, then release its playback identity.
			self._commit(ended=False)
			return
		if not self._key:
			return
		try:
			if not self.isPlaying() or not self._av_started:
				return
			self._maybe_seek()
			if not self._seeked:
				return
			t = self.getTime()
			if t is not None and t >= 0:
				self._pos = int(t)
				self._sampled = True
			tot = self.getTotalTime()
			if tot and tot > 0:
				self._total = int(tot)
			# periodic flush so a crash / hard stop never loses the position
			now = time.monotonic()
			if (self._pos, self._total) != self._last_saved and now - self._last_flush >= 15:
				self._flush()
		except Exception:
			pass

	def _flush(self):
		if self._handoff_active():
			return
		if not self._key or not self._sampled:
			return
		# Persist a backwards seek below 30s only after saving real progress.
		if self._pos < 30 and self._last_saved is None:
			return
		try:
			from streamhub import resume
			resume.record(self._key, self._pos, self._total or 0, self._title)
			self._last_saved = (self._pos, self._total)
			self._last_flush = time.monotonic()
		except Exception:
			pass

	def _commit(self, ended=False):
		if self._handoff_active():
			return
		if not self._key:
			return
		key = self._key
		try:
			from streamhub import resume
			if ended:
				# reached the end -> watched, even if the duration was unknown
				if self._total > 0:
					resume.record(key, self._total, self._total, self._title)
				else:
					resume.mark_watched(key, self._title)
			elif self._sampled:
				resume.record(key, self._pos, self._total or 0, self._title)
		except Exception:
			_log("resume commit failed", xbmc.LOGWARNING)
		self._reset()
		if ended:
			self._autoplay_next(key)

	def _autoplay_next(self, key):
		try:
			if _setting("autoplay_next") == "false":
				return
			if ":s" not in (key or ""):
				return
			from streamhub import resume
			from urllib.parse import urlencode
			meta = resume.get_meta(key)
			params = meta.get("params") or {}
			if params.get("action") != "series_play":
				return
			cur = int(params.get("episode") or 0)
			total = int(meta.get("episode_count") or 0)
			from streamhub.episode_navigation import next_episode_number
			nxt = next_episode_number(cur, total, params.get("episode_numbers"))
			if nxt is None:
				return
			np = dict(params)
			np["episode"] = str(nxt)
			url = "plugin://%s/?%s" % (ADDON_ID, urlencode(np))
			_log("autoplay: next episode E%d" % nxt)
			xbmc.executebuiltin('Notification(StreamHub,Nächste Episode E%d,3000)' % nxt)
			xbmc.Player().play(url)
		except Exception as e:
			_log("autoplay next failed: %s" % e, xbmc.LOGWARNING)

	def onPlayBackStopped(self):
		self._commit(ended=False)

	def onPlayBackEnded(self):
		self._commit(ended=True)

	def onPlayBackError(self):
		self._commit(ended=False)


def main():
	_log("service started for %s v%s" % (ADDON_ID, _installed_version()))
	monitor = SettingsMonitor()
	_add_resources_path()
	failover = None
	try:
		from streamhub.failover import FailoverPlayer
		failover = FailoverPlayer()
		_log("host failover monitor active")
	except Exception as e:
		_log("host failover monitor unavailable: %s" % e, xbmc.LOGWARNING)
	# Register callbacks immediately: streams may start during the updater's
	# startup grace period, and their resume metadata must still be consumed.
	tracker = None
	try:
		tracker = ResumeTracker()
		_log("resume tracker active")
	except Exception as e:
		_log("resume tracker unavailable: %s" % e, xbmc.LOGWARNING)

	monitor.buffer_changed = True
	startup_until = time.monotonic() + STARTUP_DELAY
	next_check = _last_check_ts() + CHECK_INTERVAL_HOURS * 3600
	if _setting("update_on_start") == "true":
		next_check = 0

	try:
		while not monitor.abortRequested():
			if failover is not None:
				try:
					failover.sample()
				except Exception as e:
					_log("host failover sample failed: %s" % e, xbmc.LOGWARNING)
			if tracker is not None:
				tracker.sample()
			idle = not _playback_active()
			if monitor.buffer_changed and idle:
				monitor.buffer_changed = False
				_apply_buffer()
			if idle and time.monotonic() >= startup_until and time.time() >= next_check:
				result = False
				try:
					result = check_for_update()
				except Exception as e:
					_log("update check error: %s" % e, xbmc.LOGWARNING)
				# None means playback started during the check: retry once idle.
				if result is not None:
					now = time.time()
					_set_last_check_ts(now)
					next_check = now + CHECK_INTERVAL_HOURS * 3600
			if monitor.waitForAbort(1):
				break

	finally:
		# Flush before shutdown clears the transition guard, so an unaccepted
		# replacement can never replace the last valid position on service exit.
		if tracker is not None:
			tracker._flush()
		if failover is not None:
			try:
				failover.shutdown()
			except Exception as e:
				_log("host failover shutdown failed: %s" % e, xbmc.LOGWARNING)

	_log("service stopped")


if __name__ == "__main__":
	main()
