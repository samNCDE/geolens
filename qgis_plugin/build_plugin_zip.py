"""Rebuild geolens_qgis_plugin.zip from the geolens_qgis folder.

Run it after any change to the plugin:

    python qgis_plugin/build_plugin_zip.py

tests/test_qgis_runner.py fails while the zip and the folder disagree, so a
release cannot ship a plugin zip older than its source.
"""
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "geolens_qgis"
ZIP = HERE / "geolens_qgis_plugin.zip"


def plugin_files():
    return sorted(p for p in SRC.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts
                  and p.suffix != ".pyc")


def build() -> Path:
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in plugin_files():
            z.write(p, p.relative_to(HERE).as_posix())
    return ZIP


if __name__ == "__main__":
    print(build())
