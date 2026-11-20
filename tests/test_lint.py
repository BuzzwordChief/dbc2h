from __future__ import annotations

import collections
import subprocess
import sys

import pytest

from conftest import CORPUS, REPO
from dbc2h.core import lint, settings, textio
from dbc2h.core.lint import Severity
from dbc2h.core.parser import parse

HEADER = "BU_: A B\n"


def rules(text: str) -> list[str]:
    return [f.rule for f in lint.lint(parse(HEADER + text))]


def test_clean_file_has_no_findings():
    assert rules('BO_ 1 M: 8 A\n SG_ s : 0|8@1+ (0.5,0) [0|127.5] "" B\n') == []


@pytest.mark.parametrize(("text", "expected"), [
    ('BO_ 1 M: 8 A\n SG_ a : 0|8@1+ (1,0) [0|0] "" B\n SG_ b : 4|8@1+ (1,0) [0|0] "" B\n', "E101"),
    ('BO_ 1 M: 1 A\n SG_ a : 4|8@1+ (1,0) [0|0] "" B\n', "E102"),
    ('BO_ 1 M: 10 A\n', "E102"),
    ('BO_ 1 M: 8 A\n SG_ a : 0|8@1+ (0,0) [0|0] "" B\n', "E105"),
    ('BO_ 1 M: 8 A\nBO_ 1 N: 8 A\n', "E106"),
    ('BO_ 1 M: 8 A\nBO_ 2 M: 8 A\n', "E107"),
    ('BO_ 1 M: 8 A\n SG_ a : 0|8@1+ (1,0) [0|0] "" B\n SG_ a : 8|8@1+ (1,0) [0|0] "" B\n', "E107"),
    ('BO_ 1 M: 8 A\nCM_ BO_ 2 "orphan";\n', "E108"),
    ('BO_ 1 M: 8 A\nBO_TX_BU_ 1 : Z;\n', "E108"),
    ('BO_ 1 M: 8 A\n SG_ a m1 : 0|8@1+ (1,0) [0|0] "" B\n', "E109"),
    ('BO_ 1 M: 8 A\n SG_ s M : 0|1@1+ (1,0) [0|0] "" B\n SG_ a m5 : 8|8@1+ (1,0) [0|0] "" B\n', "E109"),
    ('BO_ 1 M: 8 A\n SG_ s M : 0|4@1+ (1,0) [0|0] "" B\n SG_ a m1 : 8|8@1+ (1,0) [0|0] "" B\n'
     'SG_MUL_VAL_ 1 a s 1-1;\n', "E110"),
    ('BO_ 1 M: 8 A\n SG_ a m : 0|8@1+ (1,0) [0|0] "" B\n', "E111"),
    ('BO_ 2048 M: 8 A\n', "E112"),
    ('BO_ 1 M: 8 A\n SG_ a : 0|0@1+ (1,0) [0|0] "" B\n', "E113"),
    ('BO_ 1 M: 8 A\n SG_ a : 0|16@1+ (1,0) [0|0] "" B\nSIG_VALTYPE_ 1 a : 1;\n', "E113"),
    ('BO_ 1 M: 8 A\n SG_ a : 0|32@1+ (0.001,0) [0|0] "" B\n', "E201"),
    ('BO_ 1 M: 8 A\n SG_ a : 0|64@1+ (3,0) [0|0] "" B\n', "E202"),
    ('BO_ 1 M: 8 A\n SG_ a : 0|3@1+ (1,0) [0|12] "" B\n', "W103"),
    ('BO_ 1 M: 8 A\n SG_ a : 0|8@1+ (1,0) [5|1] "" B\n', "W104"),
    ('BU_: A\nBO_ 1 M: 8 A\n', "W107"),
    ('BO_ 1 2M: 8 A\n', "W406"),
])
def test_rule(text, expected):
    assert expected in rules(text)


def test_extended_id_above_7ff_is_fine():
    assert rules("BO_ 2147485696 M: 8 A\n") == []


def test_mux_signals_with_different_values_may_share_bits():
    assert rules('BO_ 1 M: 8 A\n SG_ s M : 0|8@1+ (1,0) [0|0] "" B\n'
                 ' SG_ a m0 : 8|8@1+ (1,0) [0|0] "" B\n SG_ b m1 : 8|8@1+ (1,0) [0|0] "" B\n') == []


def test_ignored_messages_and_signals_skip_code_rules():
    text = HEADER + ('BO_ 1 M: 8 A\n SG_ a : 0|32@1+ (0.001,0) [0|0] "" B\n SG_ b : 0|8@1+ (1,0) [0|0] "" B\n'
                     'BO_ 2048 N: 8 A\n')
    doc = parse(text)
    msg, other = doc.network.messages
    settings.set_setting(doc, msg.signals[0], "dbc2h_SigIgnore", 1)
    settings.set_setting(doc, other, "dbc2h_MsgIgnore", 1)
    assert lint.lint(doc) == []


def test_findings_carry_line_and_severity():
    (finding,) = lint.lint(parse(HEADER + 'BO_ 1 M: 8 A\n SG_ a : 0|32@1+ (0.001,0) [0|0] "" B\n'))
    assert (finding.rule, finding.severity, finding.line, finding.where) == ("E201", Severity.ERROR, 3, "M.a")
    assert finding.format().startswith("Zeile 3: E201 M.a: ")


# Reviewed when M2 was accepted: every count below is explained in PLAN.md (Stand M2).
EXPECTED_CORPUS_FINDINGS = {
    "E101": 1913, "E102": 18, "E108": 37, "E110": 25, "E112": 76, "E201": 82,
    "W103": 884, "W104": 4, "W107": 2, "W406": 10,
}


def test_corpus_findings_are_stable():
    counts = collections.Counter()
    for path in CORPUS:
        text, _ = textio.decode(path.read_bytes())
        counts.update(f.rule for f in lint.lint(parse(text)))
    assert dict(counts) == EXPECTED_CORPUS_FINDINGS


def run_check(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "dbc2h", "check", *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=REPO)


def test_check_command(tmp_path):
    clean = tmp_path / "clean.dbc"
    clean.write_text(HEADER + 'BO_ 1 M: 8 A\n SG_ s : 0|16@1+ (0.1,0) [0|0] "" B\n', encoding="cp1252")
    proc = run_check(str(clean), "--signals")
    assert proc.returncode == 0, proc.stderr
    assert "M.s" in proc.stdout and "float" in proc.stdout and "0 Fehler" in proc.stdout

    bad = tmp_path / "bad.dbc"
    bad.write_text(HEADER + 'BO_ 1 M: 8 A\n SG_ s : 0|32@1+ (0.001,0) [0|0] "" B\n', encoding="cp1252")
    proc = run_check(str(bad))
    assert proc.returncode == 1 and "E201" in proc.stdout

    broken = tmp_path / "broken.dbc"
    broken.write_text("BO_ x M: 8 A\n", encoding="cp1252")
    proc = run_check(str(broken))
    assert proc.returncode == 2 and "Zeile 1" in proc.stderr
    assert run_check(str(tmp_path / "missing.dbc")).returncode == 2
