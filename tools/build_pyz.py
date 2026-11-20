#!/usr/bin/env python3
"""Build dbc2h.pyz: the dbc2h package, the vendored wheels and their licenses in one file.

Usage: python tools/build_pyz.py [-o dist/dbc2h.pyz] [--vendor-dir vendor]

The vendored packages are unpacked at the root of the archive (see tools/pyz_main.py).
The build is reproducible: sorted entries, fixed timestamps and permissions. Besides
the file hash it prints a content digest over the uncompressed entries. The content
digest is the same on every platform; the file hash can differ between zlib versions.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vendor  # noqa: E402  (tools/vendor.py)

REPO = vendor.REPO
PACKAGE_DIR = REPO / "dbc2h"
LAUNCHER = REPO / "tools" / "pyz_main.py"
DEFAULT_OUTPUT = REPO / "dist" / "dbc2h.pyz"
SHEBANG = b"#!/usr/bin/env python3\n"
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
DATA_DIR_RE = re.compile(r"^[^/]+\.data/([^/]+)/(.*)$")
SKIPPED_PACKAGE_FILES = {"_bundle.json"}


class BuildError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class BuildResult:
    path: Path
    size: int
    sha256: str
    content_digest: str
    entries: int


def _is_cache(name: str) -> bool:
    return "__pycache__/" in name or name.endswith((".pyc", ".pyo"))


def collect_entries(vendor_dir: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    origins: dict[str, str] = {}

    def add(name: str, data: bytes, origin: str) -> None:
        if name in entries and entries[name] != data:
            raise BuildError(f"{name}: provided by both {origins[name]} and {origin}")
        entries[name] = data
        origins[name] = origin

    infos = vendor.verify(vendor_dir)
    reqs = vendor.read_requirements(vendor_dir)
    licenses_txt: list[str] = [
        "Third-party software bundled in dbc2h.pyz",
        "=========================================",
        "",
    ]
    manifest = []
    for info in infos:
        license = reqs.license_overrides.get(info.name, info.detected_license)
        manifest.append({"name": info.name, "version": info.version, "license": license})
        with zipfile.ZipFile(info.path) as zf:
            for zi in zf.infolist():
                name = zi.filename
                if zi.is_dir() or _is_cache(name):
                    continue
                if m := DATA_DIR_RE.match(name):
                    if m[1] not in ("purelib", "platlib"):
                        continue  # scripts, headers, data: not importable, not needed
                    name = m[2]
                add(name, zf.read(zi), info.path.name)
            licenses_txt += ["=" * 72, f"{info.name} {info.version}  ({license})",
                             f"https://pypi.org/project/{info.name}/{info.version}/", "=" * 72, ""]
            for lf in info.license_files:
                licenses_txt += [f"--- {lf.split('/', 1)[1]} ---", "",
                                 zf.read(lf).decode("utf-8", errors="replace").replace("\r\n", "\n").rstrip(), ""]

    for path in sorted(PACKAGE_DIR.rglob("*")):
        rel = path.relative_to(REPO).as_posix()
        if path.is_file() and not _is_cache(rel) and path.name not in SKIPPED_PACKAGE_FILES:
            add(rel, path.read_bytes(), "dbc2h")

    add("dbc2h/_bundle.json", (json.dumps({"packages": manifest}, indent=2) + "\n").encode(), "build")
    add("__main__.py", LAUNCHER.read_bytes(), "build")
    add("THIRD_PARTY_LICENSES.txt", "\n".join(licenses_txt).encode("utf-8"), "build")
    return entries


def content_digest(entries: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(entries):
        h.update(f"{name}\0{hashlib.sha256(entries[name]).hexdigest()}\n".encode())
    return h.hexdigest()


def write_archive(entries: dict[str, bytes], output: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in sorted(entries):
            zi = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3  # Unix, independent of the build platform
            zi.external_attr = 0o100644 << 16
            zf.writestr(zi, entries[name], compresslevel=9)
    data = SHEBANG + buf.getvalue()
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".tmp")
    tmp.write_bytes(data)
    if os.name == "posix":
        tmp.chmod(0o755)
    os.replace(tmp, output)
    return data


def build(output: Path = DEFAULT_OUTPUT, vendor_dir: Path = vendor.DEFAULT_VENDOR_DIR) -> BuildResult:
    entries = collect_entries(vendor_dir)
    data = write_archive(entries, output)
    return BuildResult(output, len(data), hashlib.sha256(data).hexdigest(), content_digest(entries), len(entries))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--vendor-dir", type=Path, default=vendor.DEFAULT_VENDOR_DIR)
    parser.add_argument("--digest-file", type=Path, help="write the content digest to this file")
    args = parser.parse_args(argv)
    try:
        result = build(args.output, args.vendor_dir)
    except (vendor.VendorError, BuildError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"built   {result.path}")
    print(f"size    {result.size} bytes, {result.entries} entries")
    print(f"sha256  {result.sha256}")
    print(f"content {result.content_digest}")
    if args.digest_file:
        args.digest_file.parent.mkdir(parents=True, exist_ok=True)
        args.digest_file.write_text(result.content_digest + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
