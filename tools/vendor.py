#!/usr/bin/env python3
"""Manage the vendored runtime dependencies of dbc2h.

The wheels in ``vendor/wheels/`` are bundled into ``dbc2h.pyz``. Only pure-Python
wheels under a license from ``ALLOWED_LICENSES`` are accepted.

Commands:
  lock    Resolve vendor/requirements.in with pip, download the wheels into
          vendor/wheels/, cross-check each sha256 against PyPI and write
          vendor/vendor.lock. Needs network access and pip.
  verify  Check vendor/wheels/ against vendor/vendor.lock. Offline; the build runs it.
  sync    Download wheels listed in vendor.lock that are missing from vendor/wheels/.

vendor/requirements.in accepts, besides normal requirement lines, two directives
in comments:
  # license: <name> = <SPDX-ID>   License for a wheel whose metadata is ambiguous.
  # ignore: <name>                Dependency that is deliberately not vendored.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_VENDOR_DIR = REPO / "vendor"
LOCK_NAME = "vendor.lock"
REQUIREMENTS_NAME = "requirements.in"
WHEELS_DIR_NAME = "wheels"
TARGET_PYTHON = "3.13"
SUPPORTED_PYTHONS = ((3, 13), (3, 14), (3, 15))

ALLOWED_LICENSES = frozenset(
    {"MIT", "BSD-2-Clause", "BSD-3-Clause", "PSF-2.0", "Apache-2.0", "ISC"}
)
PURE_TAGS = frozenset({"py3-none-any", "py2.py3-none-any"})
NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")
LICENSE_FILE_RE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)

# Short "License:" field values and trove classifiers that map unambiguously to SPDX.
# The "BSD License" classifier is deliberately missing: it does not say 2- or 3-clause.
LICENSE_ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "isc": "ISC",
    "psf-2.0": "PSF-2.0",
}
CLASSIFIER_LICENSES = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
}

REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<spec>[^;]*?)\s*(?:;\s*(?P<marker>.*))?$"
)
EXTRA_MARKER_RE = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")
PYVER_MARKER_RE = re.compile(
    r"""^\s*python_version\s*(?P<op><=|>=|==|!=|<|>)\s*["'](?P<ver>\d+\.\d+)["']\s*$"""
)


class VendorError(Exception):
    """Raised when the vendored dependencies are inconsistent or not allowed."""


def normalize(name: str) -> str:
    """PEP 503 name normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True, slots=True)
class Requirement:
    name: str
    extras: frozenset[str]
    marker: str | None


@dataclass(frozen=True, slots=True)
class WheelInfo:
    path: Path
    name: str
    version: str
    tag: str
    sha256: str
    detected_license: str | None
    requires: tuple[Requirement, ...]
    dist_info: str
    license_files: tuple[str, ...]
    native_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LockEntry:
    name: str
    version: str
    filename: str
    sha256: str
    license: str
    url: str


@dataclass(frozen=True, slots=True)
class RequirementsFile:
    roots: tuple[Requirement, ...]
    license_overrides: dict[str, str]
    ignored: frozenset[str]


def parse_requirement(text: str) -> Requirement:
    m = REQUIREMENT_RE.match(text)
    if not m:
        raise VendorError(f"cannot parse requirement {text!r}")
    extras = frozenset(normalize(e) for e in (m["extras"] or "").split(",") if e.strip())
    marker = m["marker"].strip() if m["marker"] else None
    return Requirement(normalize(m["name"]), extras, marker)


def read_requirements(vendor_dir: Path) -> RequirementsFile:
    path = vendor_dir / REQUIREMENTS_NAME
    roots: list[Requirement] = []
    overrides: dict[str, str] = {}
    ignored: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            directive = line[1:].strip()
            if directive.startswith("license:"):
                name, _, spdx = directive[len("license:"):].partition("=")
                overrides[normalize(name.strip())] = spdx.strip()
            elif directive.startswith("ignore:"):
                ignored.add(normalize(directive[len("ignore:"):].strip()))
            continue
        if line:
            roots.append(parse_requirement(line))
    return RequirementsFile(tuple(roots), overrides, frozenset(ignored))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_license(metadata) -> str | None:
    expr = metadata.get("License-Expression")
    if expr:
        return expr.strip()
    field = (metadata.get("License") or "").strip()
    if field and "\n" not in field and field.lower() in LICENSE_ALIASES:
        return LICENSE_ALIASES[field.lower()]
    found = {CLASSIFIER_LICENSES[c] for c in metadata.get_all("Classifier") or [] if c in CLASSIFIER_LICENSES}
    if len(found) == 1:
        return found.pop()
    return None


def license_allowed(expression: str) -> bool:
    """True if at least one OR-alternative consists only of allowed licenses."""
    cleaned = expression.replace("(", " ").replace(")", " ")
    for alternative in re.split(r"\s+OR\s+", cleaned):
        parts = [p.strip() for p in re.split(r"\s+AND\s+", alternative) if p.strip()]
        if parts and all(p in ALLOWED_LICENSES for p in parts):
            return True
    return False


def read_wheel(path: Path) -> WheelInfo:
    stem = path.name.removesuffix(".whl")
    parts = stem.split("-")
    if len(parts) not in (5, 6):
        raise VendorError(f"{path.name}: not a valid wheel file name")
    name, version, tag = normalize(parts[0]), parts[1], "-".join(parts[-3:])
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        dist_infos = {n.split("/", 1)[0] for n in names if n.split("/", 1)[0].endswith(".dist-info")}
        if len(dist_infos) != 1:
            raise VendorError(f"{path.name}: expected exactly one .dist-info directory")
        dist_info = dist_infos.pop()
        metadata = email.parser.Parser().parsestr(zf.read(f"{dist_info}/METADATA").decode("utf-8"))
    license_files = tuple(
        n for n in names
        if n.startswith(f"{dist_info}/licenses/") and not n.endswith("/")
        or (n.count("/") == 1 and n.startswith(f"{dist_info}/") and LICENSE_FILE_RE.match(n.split("/", 1)[1]))
    )
    native = tuple(n for n in names if n.lower().endswith(NATIVE_SUFFIXES))
    requires = tuple(parse_requirement(r) for r in metadata.get_all("Requires-Dist") or [])
    if normalize(metadata["Name"]) != name or metadata["Version"] != version:
        raise VendorError(f"{path.name}: metadata does not match the file name")
    return WheelInfo(
        path=path,
        name=name,
        version=version,
        tag=tag,
        sha256=sha256_file(path),
        detected_license=detect_license(metadata),
        requires=requires,
        dist_info=dist_info,
        license_files=license_files,
        native_files=native,
    )


def _python_marker_matches(marker: str) -> bool:
    """Evaluate a single ``python_version <op> "X.Y"`` marker for the supported versions.

    Anything more complex counts as matching, so that a dependency is never dropped
    silently. Use an ``# ignore:`` directive for deliberate exclusions.
    """
    m = PYVER_MARKER_RE.match(marker)
    if not m:
        return True
    want = tuple(int(x) for x in m["ver"].split("."))
    ops = {
        "<": lambda v: v < want, "<=": lambda v: v <= want, ">": lambda v: v > want,
        ">=": lambda v: v >= want, "==": lambda v: v == want, "!=": lambda v: v != want,
    }
    return any(ops[m["op"]](v) for v in SUPPORTED_PYTHONS)


def required_dependencies(req: Requirement, wheel: WheelInfo) -> list[Requirement]:
    out = []
    for dep in wheel.requires:
        if dep.marker:
            extras = {normalize(e) for e in EXTRA_MARKER_RE.findall(dep.marker)}
            if extras:
                if not extras & req.extras:
                    continue
            elif not _python_marker_matches(dep.marker):
                continue
        out.append(dep)
    return out


def read_lock(vendor_dir: Path) -> list[LockEntry]:
    data = json.loads((vendor_dir / LOCK_NAME).read_text(encoding="utf-8"))
    return [LockEntry(**p) for p in data["packages"]]


def verify(vendor_dir: Path = DEFAULT_VENDOR_DIR) -> list[WheelInfo]:
    """Check the vendored wheels. Returns them in lock order or raises VendorError."""
    reqs = read_requirements(vendor_dir)
    lock = read_lock(vendor_dir)
    wheels_dir = vendor_dir / WHEELS_DIR_NAME
    problems: list[str] = []
    infos: dict[str, WheelInfo] = {}

    for entry in lock:
        path = wheels_dir / entry.filename
        if not path.is_file():
            problems.append(f"{entry.filename}: missing (run 'python tools/vendor.py sync')")
            continue
        try:
            info = read_wheel(path)
        except (VendorError, KeyError, zipfile.BadZipFile) as exc:
            problems.append(f"{entry.filename}: unreadable ({exc})")
            continue
        if info.sha256 != entry.sha256:
            problems.append(f"{entry.filename}: sha256 mismatch (lock {entry.sha256[:12]}…, file {info.sha256[:12]}…)")
        if info.name != normalize(entry.name) or info.version != entry.version:
            problems.append(f"{entry.filename}: does not match lock entry {entry.name} {entry.version}")
        if info.tag not in PURE_TAGS:
            problems.append(f"{entry.filename}: not a pure-Python wheel (tag {info.tag})")
        if info.native_files:
            problems.append(f"{entry.filename}: contains native code ({', '.join(info.native_files[:3])})")
        license = reqs.license_overrides.get(info.name, info.detected_license)
        if license is None:
            problems.append(f"{entry.filename}: license not detectable; add '# license: {info.name} = <SPDX>' to {REQUIREMENTS_NAME}")
        elif not license_allowed(license):
            problems.append(f"{entry.filename}: license '{license}' is not allowed (allowed: {', '.join(sorted(ALLOWED_LICENSES))})")
        elif license != entry.license:
            problems.append(f"{entry.filename}: license '{license}' differs from lock ('{entry.license}')")
        if not info.license_files:
            problems.append(f"{entry.filename}: no license file in {info.dist_info}")
        infos[info.name] = info

    locked_files = {e.filename for e in lock}
    locked_names = {normalize(e.name) for e in lock}
    for extra in sorted(p.name for p in wheels_dir.glob("*.whl") if p.name not in locked_files):
        problems.append(f"{extra}: in {WHEELS_DIR_NAME}/ but not in {LOCK_NAME}")

    # Every dependency reachable from requirements.in must be vendored.
    todo = list(reqs.roots)
    seen: set[tuple[str, frozenset[str]]] = set()
    while todo:
        req = todo.pop()
        if (req.name, req.extras) in seen or req.name in reqs.ignored:
            continue
        seen.add((req.name, req.extras))
        info = infos.get(req.name)
        if info is None:
            if req.name not in locked_names:  # otherwise already reported above
                problems.append(f"{req.name}: required but not vendored")
            continue
        todo.extend(required_dependencies(req, info))

    if problems:
        raise VendorError("vendored dependencies are not valid:\n  " + "\n  ".join(problems))
    return [infos[normalize(e.name)] for e in lock]


def _pypi_file(name: str, version: str, filename: str) -> dict:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    for f in data["urls"]:
        if f["filename"] == filename:
            return f
    raise VendorError(f"{filename}: not listed on PyPI for {name} {version}")


def lock(vendor_dir: Path = DEFAULT_VENDOR_DIR) -> None:
    reqs = read_requirements(vendor_dir)
    wheels_dir = vendor_dir / WHEELS_DIR_NAME
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            sys.executable, "-m", "pip", "download", "--quiet", "--dest", tmp,
            "--only-binary=:all:", "--platform", "any", "--implementation", "py",
            "--abi", "none", "--python-version", TARGET_PYTHON,
            "-r", str(vendor_dir / REQUIREMENTS_NAME),
        ]
        subprocess.run(cmd, check=True)
        entries = []
        for path in sorted(Path(tmp).glob("*.whl")):
            info = read_wheel(path)
            if info.name in reqs.ignored:
                continue
            remote = _pypi_file(info.name, info.version, path.name)
            if remote["digests"]["sha256"] != info.sha256:
                raise VendorError(f"{path.name}: sha256 differs from PyPI")
            license = reqs.license_overrides.get(info.name, info.detected_license)
            if license is None:
                raise VendorError(f"{path.name}: license not detectable; add a '# license:' directive")
            entries.append(LockEntry(info.name, info.version, path.name, info.sha256, license, remote["url"]))
        wheels_dir.mkdir(parents=True, exist_ok=True)
        for old in wheels_dir.glob("*.whl"):
            old.unlink()
        for e in entries:
            shutil.copy2(Path(tmp) / e.filename, wheels_dir / e.filename)
    doc = {
        "comment": "Generated by 'python tools/vendor.py lock'. Do not edit by hand.",
        "target_python": TARGET_PYTHON,
        "packages": [asdict(e) for e in entries],
    }
    (vendor_dir / LOCK_NAME).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    verify(vendor_dir)


def sync(vendor_dir: Path = DEFAULT_VENDOR_DIR) -> None:
    wheels_dir = vendor_dir / WHEELS_DIR_NAME
    wheels_dir.mkdir(parents=True, exist_ok=True)
    for e in read_lock(vendor_dir):
        target = wheels_dir / e.filename
        if target.is_file() and sha256_file(target) == e.sha256:
            continue
        with urllib.request.urlopen(e.url, timeout=60) as resp:
            data = resp.read()
        if hashlib.sha256(data).hexdigest() != e.sha256:
            raise VendorError(f"{e.filename}: downloaded file does not match the lock")
        target.write_bytes(data)
        print(f"downloaded {e.filename}")
    verify(vendor_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("lock", "verify", "sync"))
    parser.add_argument("--vendor-dir", type=Path, default=DEFAULT_VENDOR_DIR)
    args = parser.parse_args(argv)
    try:
        if args.command == "lock":
            lock(args.vendor_dir)
        elif args.command == "sync":
            sync(args.vendor_dir)
        infos = verify(args.vendor_dir)
    except VendorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    reqs = read_requirements(args.vendor_dir)
    for info in infos:
        lic = reqs.license_overrides.get(info.name, info.detected_license)
        print(f"ok  {info.name} {info.version}  {lic}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
