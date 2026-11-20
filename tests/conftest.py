from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import build_pyz  # noqa: E402
import vendor  # noqa: E402


# --- fake vendor directories -------------------------------------------------------


def make_wheel(
    wheels_dir: Path,
    name: str,
    version: str = "1.0",
    *,
    tag: str = "py3-none-any",
    license_expression: str | None = "MIT",
    classifiers: tuple[str, ...] = (),
    requires: tuple[str, ...] = (),
    extra_files: dict[str, bytes] | None = None,
    license_file: bool = True,
) -> Path:
    dist = name.replace("-", "_")
    path = wheels_dir / f"{dist}-{version}-{tag}.whl"
    meta = [f"Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    if license_expression:
        meta.append(f"License-Expression: {license_expression}")
    meta += [f"Classifier: {c}" for c in classifiers]
    meta += [f"Requires-Dist: {r}" for r in requires]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{dist}/__init__.py", f"VALUE = {name!r}\n")
        zf.writestr(f"{dist}-{version}.dist-info/METADATA", "\n".join(meta) + "\n")
        zf.writestr(f"{dist}-{version}.dist-info/WHEEL", f"Wheel-Version: 1.0\nTag: {tag}\n")
        if license_file:
            zf.writestr(f"{dist}-{version}.dist-info/licenses/LICENSE", f"License text of {name}\n")
        for arcname, data in (extra_files or {}).items():
            zf.writestr(arcname, data)
    return path


def write_vendor(vendor_dir: Path, requirements: str, licenses: dict[str, str] | None = None) -> None:
    """Write requirements.in and a lock that matches the wheels in vendor_dir/wheels."""
    (vendor_dir / "requirements.in").write_text(requirements, encoding="utf-8")
    packages = []
    for whl in sorted((vendor_dir / "wheels").glob("*.whl")):
        info = vendor.read_wheel(whl)
        packages.append({
            "name": info.name,
            "version": info.version,
            "filename": whl.name,
            "sha256": hashlib.sha256(whl.read_bytes()).hexdigest(),
            "license": (licenses or {}).get(info.name, info.detected_license or "MIT"),
            "url": f"https://example.invalid/{whl.name}",
        })
    (vendor_dir / "vendor.lock").write_text(json.dumps({"packages": packages}), encoding="utf-8")


@pytest.fixture
def fake_vendor(tmp_path: Path) -> Path:
    d = tmp_path / "vendor"
    (d / "wheels").mkdir(parents=True)
    return d


# --- built archive and clean interpreter -----------------------------------------


@pytest.fixture(scope="session")
def built_pyz(tmp_path_factory) -> build_pyz.BuildResult:
    out = tmp_path_factory.mktemp("dist") / "dbc2h.pyz"
    return build_pyz.build(out)


@pytest.fixture(scope="session")
def clean_python(tmp_path_factory) -> Path:
    """Interpreter of a fresh venv without any packages (not even pip)."""
    env = tmp_path_factory.mktemp("cleanvenv")
    venv.EnvBuilder(with_pip=False).create(env)
    exe = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    assert exe.exists()
    return exe


def run_pyz(python: Path, pyz: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(python), str(pyz), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
    )


# --- DBC corpus ----------------------------------------------------------------------

CORPUS_DIR = REPO / "tests" / "corpus"
CORPUS = sorted(p for p in CORPUS_DIR.rglob("*") if p.suffix.lower() == ".dbc")


def corpus_id(path: Path) -> str:
    return path.relative_to(CORPUS_DIR).as_posix()
