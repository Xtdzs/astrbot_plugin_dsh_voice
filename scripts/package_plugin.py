"""Build a plugin ZIP containing only reviewed source and documentation."""

from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
files = [
    "__init__.py",
    "main.py",
    "core.py",
    "providers.py",
    "errors.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
]
metadata = (root / "metadata.yaml").read_text("utf-8")
version = re.search(
    r"^version:\s*v?([0-9]+\.[0-9]+\.[0-9]+)\s*$", metadata, re.MULTILINE
).group(1)
target = root / "dist" / f"astrbot_plugin_dsh_voice-v{version}.zip"
target.parent.mkdir(exist_ok=True)
with ZipFile(target, "w", ZIP_DEFLATED) as archive:
    for name in files:
        archive.write(root / name, "astrbot_plugin_dsh_voice/" + name)
with ZipFile(target) as archive:
    assert archive.testzip() is None
    assert len(archive.namelist()) == len(files)
print(f"Created {target} ({target.stat().st_size:,} bytes)")
