"""Turn dist\\EN2KR4GAMING into a ready-to-run zip.

Run after `pyinstaller EN2KR4GAMING.spec --noconfirm`, from the project venv:
    python tools\\package_release.py
Adds the user guide, third-party notices and the license files of every
bundled package, then writes dist\\EN2KR4GAMING_v<version>.zip.
"""
import importlib.metadata as md
import os
import re
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from en2kr import __version__  # noqa: E402

DIST = os.path.join(ROOT, "dist", "EN2KR4GAMING")
# Installed in the venv but not shipped (dev tools, and PyAV which the spec excludes).
NOT_BUNDLED = {"pyinstaller", "pyinstaller-hooks-contrib", "pytest", "pyflakes", "pip", "setuptools", "altgraph",
               "pefile", "iniconfig", "pluggy", "colorama", "exceptiongroup", "tomli", "packaging", "av"}
LICENSE_RE = re.compile(r"(licen[cs]e|copying|notice)", re.I)


def collect_licenses(target):
    count = 0
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in NOT_BUNDLED:
            continue
        files = [f for f in (dist.files or []) if LICENSE_RE.search(os.path.basename(str(f)))
                 and ".dist-info" in str(f)]
        if not files:
            # Some wheels (e.g. ctranslate2, onnxruntime) only declare the license in their metadata.
            meta = dist.metadata
            dst_dir = os.path.join(target, name)
            os.makedirs(dst_dir, exist_ok=True)
            with open(os.path.join(dst_dir, "LICENSE-INFO.txt"), "w", encoding="utf-8") as fh:
                fh.write(f"{name} {dist.version}\n")
                fh.write(f"License: {meta.get('License-Expression') or meta.get('License') or 'see project'}\n")
                for url in meta.get_all("Project-URL") or []:
                    fh.write(f"{url}\n")
                if meta.get("Home-page"):
                    fh.write(f"Home-page: {meta['Home-page']}\n")
            count += 1
        for f in files:
            src = f.locate()
            if os.path.isfile(src):
                dst_dir = os.path.join(target, name)
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copy2(src, os.path.join(dst_dir, os.path.basename(str(f))))
                count += 1
    return count


def main():
    if not os.path.exists(os.path.join(DIST, "EN2KR4GAMING.exe")):
        raise SystemExit("dist\\EN2KR4GAMING\\EN2KR4GAMING.exe not found - run PyInstaller first")
    for doc in ("READ ME.txt", "THIRD_PARTY_NOTICES.txt"):
        shutil.copy2(os.path.join(ROOT, doc), os.path.join(DIST, doc))
    lic_dir = os.path.join(DIST, "licenses")
    shutil.rmtree(lic_dir, ignore_errors=True)
    print(f"license files: {collect_licenses(lic_dir)}")
    silero = os.path.join(lic_dir, "silero-vad")
    os.makedirs(silero, exist_ok=True)
    with open(os.path.join(silero, "LICENSE"), "w", encoding="utf-8") as f:
        f.write("Silero VAD - MIT License - https://github.com/snakers4/silero-vad/blob/master/LICENSE\n")

    archive = os.path.join(ROOT, "dist", f"EN2KR4GAMING_v{__version__}.zip")
    if os.path.exists(archive):
        os.remove(archive)
    parent = os.path.dirname(DIST)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for folder, _, names in os.walk(DIST):
            for n in names:
                full = os.path.join(folder, n)
                z.write(full, os.path.relpath(full, parent))
    print(f"wrote {archive} ({os.path.getsize(archive) / 2**30:.2f} GB)")


if __name__ == "__main__":
    main()
