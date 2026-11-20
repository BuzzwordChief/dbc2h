"""Tests of the build and of the built dbc2h.pyz in a clean environment."""

from __future__ import annotations

import ast
import subprocess
import sys
import zipfile

import pytest

from conftest import REPO, make_wheel, run_pyz, write_vendor
import build_pyz
import vendor

pytestmark = pytest.mark.pyz


def test_build_fails_for_disallowed_license(fake_vendor, tmp_path):
    make_wheel(fake_vendor / "wheels", "alpha", license_expression="GPL-3.0-only")
    write_vendor(fake_vendor, "alpha==1.0\n", licenses={"alpha": "GPL-3.0-only"})
    out = tmp_path / "x.pyz"
    proc = subprocess.run(
        [sys.executable, str(REPO / "tools" / "build_pyz.py"), "--vendor-dir", str(fake_vendor), "-o", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "GPL-3.0-only' is not allowed" in proc.stderr
    assert not out.exists()


def test_build_is_reproducible(built_pyz, tmp_path):
    again = build_pyz.build(tmp_path / "again.pyz")
    assert again.sha256 == built_pyz.sha256
    assert again.content_digest == built_pyz.content_digest


def test_archive_contents(built_pyz):
    with zipfile.ZipFile(built_pyz.path) as zf:
        names = set(zf.namelist())
        licenses = zf.read("THIRD_PARTY_LICENSES.txt").decode("utf-8")
        assert zf.read("__main__.py") == (REPO / "tools" / "pyz_main.py").read_bytes()
    assert "dbc2h/tui/dbc2h.tcss" in names
    assert "dbc2h/_bundle.json" in names
    assert "textual/__init__.py" in names  # vendored packages at the archive root
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    for entry in vendor.read_lock(vendor.DEFAULT_VENDOR_DIR):
        assert f"{entry.name} {entry.version}  ({entry.license})" in licenses
        assert any(n.endswith(".dist-info/METADATA") and n.lower().startswith(entry.name.replace("-", "_"))
                   for n in names), entry.name
    assert "Permission is hereby granted" in licenses  # MIT text, not just the name


def test_version_in_clean_environment(built_pyz, clean_python):
    proc = run_pyz(clean_python, built_pyz.path, "--version")
    assert proc.returncode == 0, proc.stderr
    for entry in vendor.read_lock(vendor.DEFAULT_VENDOR_DIR):
        assert f"{entry.name} {entry.version} ({entry.license})" in proc.stdout


def test_selftest_in_clean_environment(built_pyz, clean_python):
    proc = run_pyz(clean_python, built_pyz.path, "--selftest")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok   stylesheet #dialog" in proc.stdout
    assert "ok   quit with q" in proc.stdout


def test_importlib_metadata_works_inside_archive(built_pyz, clean_python):
    code = f"import sys; sys.path.insert(0, {str(built_pyz.path)!r}); import textual; print(textual.__version__)"
    proc = subprocess.run([str(clean_python), "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    expected = next(e.version for e in vendor.read_lock(vendor.DEFAULT_VENDOR_DIR) if e.name == "textual")
    assert proc.stdout.strip() == expected


@pytest.mark.parametrize("path", ["tools/pyz_main.py", "dbc2h/__init__.py", "dbc2h/__main__.py"])
def test_entry_files_parse_on_old_python(path):
    """These files run before the version check and must not use new syntax."""
    ast.parse((REPO / path).read_text(encoding="utf-8"), feature_version=(3, 7))


def test_version_check_message(monkeypatch, capsys):
    import dbc2h.__main__ as entry

    monkeypatch.setattr(entry, "MIN_PYTHON", (99, 0))
    assert entry.main([]) == 1
    assert "benötigt Python 99.0 oder neuer" in capsys.readouterr().err


def test_package_does_not_access_files_via_dunder_file():
    """Resources must be read with importlib.resources, or they break inside the .pyz."""
    offenders = [p.relative_to(REPO).as_posix() for p in (REPO / "dbc2h").rglob("*.py")
                 if "__file__" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_redirected_output_is_utf8(built_pyz, clean_python):
    """Python 3.13/3.14 on Windows would use the ANSI code page for pipes."""
    proc = subprocess.run([str(clean_python), str(built_pyz.path), "--version"], capture_output=True)
    assert proc.returncode == 0
    assert "Gebündelt".encode("utf-8") in proc.stdout


def test_check_runs_from_the_archive_without_textual(built_pyz, clean_python, tmp_path):
    dbc = tmp_path / "net.dbc"
    dbc.write_text('BU_: A\nBO_ 1 M: 8 A\n SG_ s : 0|16@1+ (0.1,0) [0|0] "" A\n', encoding="cp1252")
    proc = run_pyz(clean_python, built_pyz.path, "check", str(dbc))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    code = (f"import sys; sys.path.insert(0, {str(built_pyz.path)!r}); from dbc2h.cli import main; "
            f"rc = main(['check', {str(dbc)!r}]); print('textual loaded:', 'textual' in sys.modules)")
    proc = subprocess.run([str(clean_python), "-c", code], capture_output=True, text=True)
    assert "textual loaded: False" in proc.stdout, proc.stdout + proc.stderr
