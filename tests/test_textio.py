from __future__ import annotations

import codecs
import os
import sys
from pathlib import Path

import pytest

from dbc2h.core import textio
from dbc2h.core.dbcfile import DbcFile
from dbc2h.core.model import Number

SIMPLE = 'VERSION ""\r\n\r\nBU_: A\r\n\r\nBO_ 1 M: 8 A\r\n SG_ s : 0|8@1+ (1,0) [0|0] "\xb0C" A\r\n'


@pytest.mark.parametrize(("data", "codec", "bom"), [
    (b"plain ascii", "utf-8", False),
    ("°C –".encode("utf-8"), "utf-8", False),
    (codecs.BOM_UTF8 + "x".encode("utf-8"), "utf-8", True),
    ("°C €".encode("cp1252"), "cp1252", False),
    (b"\x81\x8d undefined in cp1252", "latin-1", False),
    ("中文".encode("gbk"), "cp1252", False),  # GBK bytes that happen to be valid cp1252
    ("丁丂".encode("gbk"), "latin-1", False),  # GBK with a byte undefined in cp1252 (0x81)
])
def test_decode_stages_and_lossless_encode(data, codec, bom):
    text, fmt = textio.decode(data)
    assert (fmt.codec, fmt.bom) == (codec, bom)
    assert textio.encode(text, fmt) == data


def test_can_encode():
    assert textio.can_encode("°C €", textio.FileFormat("cp1252"))
    assert not textio.can_encode("→", textio.FileFormat("cp1252"))
    assert textio.can_encode("→", textio.FileFormat("utf-8"))


def test_load_edit_save_round_trip(tmp_path):
    path = tmp_path / "net.dbc"
    path.write_bytes(SIMPLE.encode("cp1252"))
    f = DbcFile.load(path)
    assert f.format.codec == "cp1252" and f.document.newline == "\r\n"
    f.save()
    assert path.read_bytes() == SIMPLE.encode("cp1252")
    assert (tmp_path / "net.dbc.bak").read_bytes() == SIMPLE.encode("cp1252")

    msg = f.document.network.message(1)
    msg.comment = "Straße"
    f.document.new_statement("CM_", msg, msg, "CM_")
    f.save()
    data = path.read_bytes()
    assert data.endswith(b'\r\n\r\nCM_ BO_ 1 "Stra\xdfe";\r\n')  # the file trailer stays at the end
    assert DbcFile.load(path).document.network.message(1).comment == "Straße"


def test_backup_is_written_once_per_session(tmp_path):
    path = tmp_path / "net.dbc"
    path.write_bytes(SIMPLE.encode("cp1252"))
    f = DbcFile.load(path)
    f.save()
    f.document.set_attribute(f.document.network, "X", Number.of(1))
    f.save()
    assert (tmp_path / "net.dbc.bak").read_bytes() == SIMPLE.encode("cp1252")


def test_external_change_is_detected(tmp_path):
    path = tmp_path / "net.dbc"
    path.write_bytes(SIMPLE.encode("cp1252"))
    f = DbcFile.load(path)
    path.write_bytes(SIMPLE.encode("cp1252") + b"\r\n// changed elsewhere\r\n")
    with pytest.raises(textio.ExternalChangeError):
        f.save()
    assert b"changed elsewhere" in path.read_bytes()
    f.save(force=True)
    assert b"changed elsewhere" not in path.read_bytes()


def test_deleted_file_counts_as_external_change(tmp_path):
    path = tmp_path / "net.dbc"
    path.write_bytes(SIMPLE.encode("cp1252"))
    f = DbcFile.load(path)
    path.unlink()
    with pytest.raises(textio.ExternalChangeError):
        f.save()


def test_touch_without_content_change_is_not_an_external_change(tmp_path):
    path = tmp_path / "net.dbc"
    path.write_bytes(SIMPLE.encode("cp1252"))
    f = DbcFile.load(path)
    os.utime(path, ns=(1, 1))
    f.save()


def test_new_file_save_as(tmp_path):
    f = DbcFile.new()
    with pytest.raises(ValueError):
        f.save()
    target = tmp_path / "new.dbc"
    f.save(target)
    assert f.path == target and target.read_bytes().startswith(b'VERSION ""\r\n')
    assert not (tmp_path / "new.dbc.bak").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks and modes need POSIX")
def test_symlink_and_mode_are_kept(tmp_path):
    real = tmp_path / "real.dbc"
    real.write_bytes(SIMPLE.encode("cp1252"))
    real.chmod(0o640)
    link = tmp_path / "link.dbc"
    link.symlink_to(real)
    f = DbcFile.load(link)
    f.document.set_attribute(f.document.network, "X", Number.of(1))
    f.save()
    assert link.is_symlink()
    assert b'BA_ "X" 1;' in real.read_bytes()
    assert real.stat().st_mode & 0o777 == 0o640


def test_locked_file_keeps_the_new_content(tmp_path, monkeypatch):
    path = tmp_path / "net.dbc"
    path.write_bytes(SIMPLE.encode("cp1252"))
    f = DbcFile.load(path)
    f.document.set_attribute(f.document.network, "X", Number.of(1))

    def locked(src, dst):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(textio.os, "replace", locked)
    with pytest.raises(textio.SaveError) as info:
        f.save()
    assert info.value.temp_path is not None
    assert b'BA_ "X" 1;' in Path(info.value.temp_path).read_bytes()
    assert path.read_bytes() == SIMPLE.encode("cp1252")
