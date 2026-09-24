"""打包成 AstrBot WebUI 可上传的 zip。"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_NAME = "astrbot_plugin_dst"
OUT = ROOT / f"{PLUGIN_NAME}.zip"

INCLUDE_FILES = [
    "metadata.yaml",
    "main.py",
    "_conf_schema.json",
    "requirements.txt",
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
    "data/items.json",
    "data/commands.json",
    "dst/__init__.py",
    "dst/i18n.py",
    "dst/lobby.py",
    "dst/monitor.py",
    "dst/search.py",
    "dst/store.py",
]


def main() -> None:
    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{PLUGIN_NAME}/", "")
        for rel in INCLUDE_FILES:
            src = ROOT / rel
            if not src.is_file():
                raise FileNotFoundError(f"缺少打包文件：{rel}")
            zf.write(src, f"{PLUGIN_NAME}/{rel}".replace("\\", "/"))
    names = zipfile.ZipFile(OUT).namelist()
    print(f"已生成 {OUT} ({OUT.stat().st_size} bytes)")
    print("zip 条目：")
    for name in names:
        print(f"  {name}")


if __name__ == "__main__":
    main()
