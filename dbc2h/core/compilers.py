"""Find a C compiler and build/run the generated test file (dbc2h gen --run-tests, tests)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GNU_WARNINGS = ["-Wall", "-Wextra", "-Wpedantic", "-Wconversion", "-Wdouble-promotion", "-Werror"]
MSVC_WARNINGS = ["/W4", "/WX"]


@dataclass(frozen=True, slots=True)
class Compiler:
    name: str          # e.g. "gcc", "clang", "msvc"
    command: str       # executable for C (and C++ via -x c++ or /TP)
    cxx_command: str   # executable for C++
    version: str

    @property
    def is_msvc(self) -> bool:
        return self.name == "msvc"

    def command_line(self, source: Path, exe: Path, include: Path, *, cpp: bool = False,
                     extra: tuple[str, ...] = ()) -> list[str]:
        if self.is_msvc:
            lang = ["/TP", "/std:c++17", "/EHsc"] if cpp else ["/std:c11"]
            obj = exe.with_suffix(".obj")
            return ["cl", "/nologo", *lang, *MSVC_WARNINGS, "/O2", *extra, f"/I{include}", str(source),
                    f"/Fe:{exe}", f"/Fo:{obj}"]
        lang = ["-x", "c++", "-std=c++17"] if cpp else ["-std=c11"]
        return [self.cxx_command if cpp else self.command, *lang, *GNU_WARNINGS, "-O2", *extra, "-I", str(include),
                str(source), "-o", str(exe)]


def _version(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return out[0] if out else None


def find_compilers() -> list[Compiler]:
    """All distinct compilers on PATH. CC overrides the GNU-style search."""
    found: list[Compiler] = []
    seen: set[str] = set()
    candidates = [("gcc", "gcc", "g++"), ("clang", "clang", "clang++"), ("cc", "cc", "c++")]
    if os.environ.get("CC"):
        candidates.insert(0, ("cc", os.environ["CC"], os.environ.get("CXX", os.environ["CC"])))
    for name, c, cxx in candidates:
        if not shutil.which(c):
            continue
        version = _version([c, "--version"])
        if not version or version in seen:
            continue
        seen.add(version)
        kind = "clang" if "clang" in version.lower() else ("gcc" if name in ("gcc", "cc") else name)
        found.append(Compiler(kind, c, cxx if shutil.which(cxx) else c, version))
    if sys.platform == "win32" and shutil.which("cl"):
        version = _version(["cl"])
        if version:
            found.append(Compiler("msvc", "cl", "cl", version))
    return found


@dataclass(frozen=True, slots=True)
class RunResult:
    ok: bool
    stage: str  # "compile" or "run"
    output: str
    command: list[str]


def build_and_run(compiler: Compiler, source: Path, include: Path, workdir: Path, *, cpp: bool = False,
                  extra: tuple[str, ...] = ()) -> RunResult:
    source, include, workdir = source.resolve(), include.resolve(), workdir.resolve()
    exe = workdir / (source.stem + ("_cpp" if cpp else "") + (".exe" if sys.platform == "win32" else ""))
    cmd = compiler.command_line(source, exe, include, cpp=cpp, extra=extra)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    if proc.returncode != 0:
        return RunResult(False, "compile", proc.stdout + proc.stderr, cmd)
    run = subprocess.run([str(exe)], capture_output=True, text=True, cwd=workdir, timeout=300)
    return RunResult(run.returncode == 0, "run", run.stdout + run.stderr, cmd)
