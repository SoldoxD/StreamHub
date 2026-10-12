"""Version-aware Kodi file cache; InputStream add-ons use their own buffers."""
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET

from streamhub.utils import (getSetting, setSetting, log, dialog, format_exc,
                             translatePath, xbmc)

# Valid Kodi 21+ MiB options, with a conservative default for limited RAM.
PRESETS = {"0": None, "1": 64, "2": 128, "3": 256}
DEFAULT_PRESET = "1"
_MB = 1024 * 1024
_KEYS = ("memorysize", "readfactor", "buffermode")
_REVISION = "2"


def _path():
	return os.path.join(translatePath("special://profile/"), "advancedsettings.xml")


def _preset(value=None):
	value = str(value if value is not None else (getSetting("buffer_size") or DEFAULT_PRESET))
	return value if value in PRESETS else DEFAULT_PRESET


def _kodi_major():
	try:
		match = re.match(r"\s*(\d+)", xbmc.getInfoLabel("System.BuildVersion"))
		return int(match.group(1)) if match else None
	except Exception:
		return None


def _rpc(method, **params):
	response = json.loads(xbmc.executeJSONRPC(json.dumps({
		"jsonrpc": "2.0", "id": 1, "method": method, "params": params})))
	if "error" in response or "result" not in response:
		raise RuntimeError("Kodi cache API failed: %s" % method)
	return response["result"]


def _read_modern():
	return {key: _rpc("Settings.GetSettingValue", setting="filecache." + key)["value"]
	        for key in _KEYS}


def _write_modern(before, after):
	changed = []
	try:
		for key in _KEYS:
			if after[key] == before[key]:
				continue
			if _rpc("Settings.SetSettingValue", setting="filecache." + key,
			        value=after[key]) is not True:
				raise RuntimeError("Kodi rejected filecache." + key)
			changed.append(key)
		if _read_modern() != after:
			raise RuntimeError("Kodi cache verification failed")
	except Exception:
		for key in reversed(changed):
			try:
				_rpc("Settings.SetSettingValue", setting="filecache." + key, value=before[key])
			except Exception:
				log("could not roll back filecache." + key)
		raise


def _legacy_tree():
	p = _path()
	if os.path.exists(p):
		parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
		tree = ET.parse(p, parser=parser)
		if tree.getroot().tag != "advancedsettings":
			raise ValueError("unexpected advancedsettings.xml root")
		return tree
	return ET.ElementTree(ET.Element("advancedsettings"))


def _legacy_values(tree):
	cache = tree.getroot().find("cache")
	return {key: (cache.findtext(key) if cache is not None else None) for key in _KEYS}


def _write_legacy(tree, values):
	root = tree.getroot()
	cache = root.find("cache")
	if cache is None:
		cache = ET.SubElement(root, "cache")
	for key, value in values.items():
		for node in list(cache.findall(key)):
			cache.remove(node)
		if value is not None:
			ET.SubElement(cache, key).text = str(value)
	if len(cache) == 0:
		root.remove(cache)
	# Atomic replace protects existing settings from a partial write.
	p = _path()
	os.makedirs(os.path.dirname(p), exist_ok=True)
	fd, temporary = tempfile.mkstemp(prefix=".streamhub-cache-", dir=os.path.dirname(p))
	try:
		with os.fdopen(fd, "wb") as output:
			tree.write(output, encoding="utf-8", xml_declaration=True)
		os.replace(temporary, p)
	finally:
		if os.path.exists(temporary):
			os.remove(temporary)


def _backup():
	raw = getSetting("_buffer_backup") or ""
	if not raw:
		return {}
	data = json.loads(raw)
	if not isinstance(data, dict):
		raise ValueError("invalid cache backup")
	return data


def _save_backup(data):
	setSetting("_buffer_backup", json.dumps(data, sort_keys=True))


def current():
	try:
		major = _kodi_major()
		if major is None:
			return None
		if major >= 21:
			values = _read_modern()
			return {"memorysize": str(values["memorysize"] * _MB),
			        "readfactor": ("adaptiv" if values["readfactor"] == 0
			                       else "%g" % (values["readfactor"] / 100.0)),
			        "buffermode": str(values["buffermode"])}
		return {key: value for key, value in _legacy_values(_legacy_tree()).items()
		        if value is not None} or None
	except Exception:
		log("could not read cache settings\n%s" % format_exc())
		return None


def apply(preset=None, announce=True):
	preset = _preset(preset)
	major = _kodi_major()
	if major is None:
		log("could not determine Kodi version; cache left unchanged")
		return False
	modern = major >= 21
	backend = "settings" if modern else "xml"
	try:
		backups = _backup()
		previous_backups = json.loads(json.dumps(backups))
		saved = backups.get(backend)
		# Off leaves settings belonging to the user or other add-ons alone.
		if PRESETS[preset] is None and not saved:
			return True
		tree = None if modern else _legacy_tree()
		before = _read_modern() if modern else _legacy_values(tree)
		if PRESETS[preset] is None:
			after = dict(before)
			for key in _KEYS:
				# Keep manual changes made since our last applied preset.
				if before[key] == saved["applied"].get(key):
					after[key] = saved["original"].get(key)
		else:
			# Kodi 21 uses MiB and readfactor * 100; zero means adaptive.
			# Mode 2 buffers HTTP(S), without buffering local files as well.
			after = {"memorysize": PRESETS[preset], "readfactor": 0, "buffermode": 2}
			if not modern:
				after = {"memorysize": str(PRESETS[preset] * _MB),
				         "readfactor": "4", "buffermode": "2"}
			original = dict(saved["original"]) if saved else dict(before)
			if saved:
				for key in _KEYS:
					if before[key] != saved["applied"].get(key):
						original[key] = before[key]
			# Save recovery information before changing any Kodi setting.
			backups[backend] = {"original": original, "applied": after}
			_save_backup(backups)
		try:
			if modern:
				_write_modern(before, after)
			elif before != after:
				_write_legacy(tree, after)
		except Exception:
			try:
				rolled_back = _read_modern() if modern else _legacy_values(_legacy_tree())
				if rolled_back == before:
					_save_backup(previous_backups)
			except Exception:
				pass
			raise
		if PRESETS[preset] is None:
			backups.pop(backend, None)
			_save_backup(backups)
	except Exception:
		log("could not apply cache settings\n%s" % format_exc())
		return False
	log("buffer preset %s applied via %s" % (preset, backend))
	if announce:
		message = ("Kodi-Puffereinstellungen wiederhergestellt" if preset == "0"
		           else "Puffer: %d MB" % PRESETS[preset])
		message += " - nächsten Stream starten" if modern else " - Kodi neu starten"
		dialog.notification("StreamHub", message, time=6000)
	return True


def apply_if_changed():
	preset = _preset()
	major = _kodi_major()
	if major is None:
		return False
	marker = "%s:kodi%d:%s" % (_REVISION, major, preset)
	if marker == (getSetting("_buffer_applied") or ""):
		return False
	if apply(preset, announce=False):
		setSetting("_buffer_applied", marker)
		return True
	return False


def show_status():
	cur = current()
	major = _kodi_major()
	modern = major is not None and major >= 21
	size = PRESETS[_preset()]
	want = "Kodi verwaltet den Puffer" if size is None else "%d MB" % size
	if not cur:
		live = "nicht lesbar" if modern else "Kodi-Standard (kein XML-Puffer)"
	else:
		try:
			mb = int(cur.get("memorysize", "0")) // _MB
		except (TypeError, ValueError):
			mb = 0
		live = "%d MB / Leserate %s / Modus %s" % (
			mb, cur.get("readfactor", "?"), cur.get("buffermode", "?"))
	location = "Kodi-Einstellungen" if modern else "advancedsettings.xml"
	effect = ("Wirkt beim nächsten Streamstart." if modern else
	          "XML-Änderungen wirken nach einem Kodi-Neustart.")
	dialog.ok("StreamHub - Puffer",
	          "Eingestellt: %s\n%s: %s\n\n%s\n"
	          "InputStream-Add-ons verwenden eigene Puffer." % (want, location, live, effect))
