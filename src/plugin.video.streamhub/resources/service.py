
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


def check_for_update(force=False):
	installed = _installed_version()

	# cheap change-detector: skip parsing the manifest if the md5 is unchanged
	if not force:
		md5 = _remote_md5()
		st = _load_state()
		if md5 and md5 == st.get("md5") and st.get("known") \
				and not _is_newer(st.get("known"), installed):
			_log("manifest unchanged, up to date")
			return False

	remote = _fetch_remote_version()
	_log("installed=%s  remote=%s" % (installed, remote))
	if not remote:
		return False
	try:
		st = _load_state()
		st["md5"] = _remote_md5() or st.get("md5", "")
		st["known"] = remote
		_save_state(st)
	except Exception:
		pass
	if not _is_newer(remote, installed):
		_log("already up to date")
		return False

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

	xbmcgui.Dialog().notification("StreamHub", "Update %s wird geladen..." % remote, time=4000)
	zip_bytes = _download_zip(remote)
	if not zip_bytes:
		xbmcgui.Dialog().notification("StreamHub", "Download fehlgeschlagen", time=6000)
		return False

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

	def _pickup(self):
		try:
			from streamhub.utils import home
			import json
			raw = home.getProperty(self.PROP)
			if not raw:
				return
			d = json.loads(raw)
			self._key = d.get("key") or None
			self._title = d.get("title", "")
			self._pending_resume = int(d.get("resume") or 0)
			self._seeked = False
			home.clearProperty(self.PROP)
		except Exception:
			pass

	def onAVStarted(self):
		self._pickup()
		self._maybe_seek()

	def onPlayBackStarted(self):
		if not self._key:
			self._pickup()

	def _maybe_seek(self):
		if self._seeked or not self._key:
			return
		try:
			pos = getattr(self, "_pending_resume", 0)
			if pos and pos >= 30:
				# wait until the stream reports a duration, then seek
				for _ in range(20):
					if self.isPlaying() and (self.getTotalTime() or 0) > pos:
						self.seekTime(pos)
						break
					xbmc.sleep(250)
			self._seeked = True
		except Exception:
			pass

	def sample(self):
		if not self._key:
			return
		try:
			if not self.isPlaying():
				return
			t = self.getTime()
			if t and t >= 0:
				self._pos = int(t)
			tot = self.getTotalTime()
			if tot and tot > 0:
				self._total = int(tot)
			# periodic flush so a crash / hard stop never loses the position
			now = time.time()
			if self._pos >= 30 and (now - getattr(self, "_last_flush", 0)) >= 15:
				self._last_flush = now
				self._flush()
		except Exception:
			pass

	def _flush(self):
		if not self._key or self._pos < 30:
			return
		try:
			from streamhub import resume
			resume.record(self._key, self._pos, self._total or 0, self._title)
		except Exception:
			pass

	def _commit(self, ended=False):
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
			else:
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
		self._reset()


def main():
	_log("service started for %s v%s" % (ADDON_ID, _installed_version()))
	monitor = SettingsMonitor()

	_apply_buffer()

	if monitor.waitForAbort(STARTUP_DELAY):
		return

	_add_resources_path()
	tracker = None
	try:
		tracker = ResumeTracker()
		_log("resume tracker active")
	except Exception as e:
		_log("resume tracker unavailable: %s" % e, xbmc.LOGWARNING)

	now = int(time.time())
	last = _last_check_ts()
	due = (now - last) >= CHECK_INTERVAL_HOURS * 3600
	if due or _setting("update_on_start") == "true":
		try:
			check_for_update()
		except Exception as e:
			_log("update check error: %s" % e, xbmc.LOGWARNING)
		_set_last_check_ts(time.time())

	while not monitor.abortRequested():
		if monitor.waitForAbort(1):
			break
		if monitor.buffer_changed:
			monitor.buffer_changed = False
			_apply_buffer()
		if tracker is not None:
			tracker.sample()
		if (time.time() - _last_check_ts()) >= CHECK_INTERVAL_HOURS * 3600:
			try:
				check_for_update()
			except Exception as e:
				_log("update check error: %s" % e, xbmc.LOGWARNING)
			_set_last_check_ts(time.time())

	_log("service stopped")


if __name__ == "__main__":
	main()
