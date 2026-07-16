import hashlib
import os
import re
import shutil
import time
import zipfile
import xml.etree.ElementTree as ET

SRC = r"C:\Users\soldo\Desktop\plugin.video.streamhub"
REPO = os.path.dirname(os.path.abspath(__file__))
ADDON_ID = "plugin.video.streamhub"
OUT = os.path.join(REPO, ADDON_ID)

EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
EXCLUDE_EXT = {".pyc", ".pyo", ".zip"}


def version():
    return ET.parse(os.path.join(SRC, "addon.xml")).getroot().get("version")


def build_zip(ver):
    for old in os.listdir(OUT):
        if old.endswith(".zip"):
            os.remove(os.path.join(OUT, old))
    name = "%s-%s.zip" % (ADDON_ID, ver)
    path = os.path.join(OUT, name)
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(SRC):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in files:
                if os.path.splitext(f)[1].lower() in EXCLUDE_EXT:
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, SRC).replace("\\", "/")
                z.write(full, "%s/%s" % (ADDON_ID, rel))
                n += 1
    return name, n, os.path.getsize(path)


def build_addons_xml(ver):
    tree = ET.parse(os.path.join(SRC, "addon.xml"))
    raw = open(os.path.join(SRC, "addon.xml"), encoding="utf-8").read()
    body = raw.split("?>", 1)[1].strip()
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n%s\n</addons>\n' % body
    p = os.path.join(REPO, "addons.xml")
    open(p, "w", encoding="utf-8", newline="\n").write(xml)
    md5 = hashlib.md5(open(p, "rb").read()).hexdigest()
    open(os.path.join(REPO, "addons.xml.md5"), "w", encoding="utf-8", newline="\n").write(md5)
    return md5


def build_folder_index(zipname):
    rows = ['<tr><td><a href="../">..</a></td></tr>']
    for f in ["addon.xml", "icon.png", zipname]:
        rows.append('<tr><td><a href="%s">%s</a></td></tr>' % (f, f))
    html = (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        '<title>Index of /%s/</title></head>\n<body><h1>Index of /%s/</h1>\n<table>\n%s\n</table></body></html>\n'
        % (ADDON_ID, ADDON_ID, "\n".join(rows))
    )
    open(os.path.join(OUT, "index.html"), "w", encoding="utf-8", newline="\n").write(html)


def copy_meta():
    shutil.copy2(os.path.join(SRC, "addon.xml"), os.path.join(OUT, "addon.xml"))
    icon = os.path.join(SRC, "icon.png")
    if os.path.exists(icon):
        shutil.copy2(icon, os.path.join(OUT, "icon.png"))


def bump_root_index(ver):
    p = os.path.join(REPO, "index.html")
    html = open(p, encoding="utf-8").read()
    html = re.sub(r"(<p class=\"tag\">Kodi[^<]*)", r"\1", html)
    open(p, "w", encoding="utf-8", newline="\n").write(html)


def main():
    ver = version()
    copy_meta()
    zipname, n, size = build_zip(ver)
    md5 = build_addons_xml(ver)
    build_folder_index(zipname)
    bump_root_index(ver)
    open(os.path.join(REPO, ".build"), "w", newline="\n").write(
        "last-build: %s\n" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    print("version    : %s" % ver)
    print("zip        : %s (%d files, %.1f KB)" % (zipname, n, size / 1024.0))
    print("addons.xml : md5 %s" % md5)


if __name__ == "__main__":
    main()
