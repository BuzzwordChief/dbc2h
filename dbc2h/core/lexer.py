"""Tokenizer for DBC files."""

from __future__ import annotations

import bisect
import enum
import re
from dataclasses import dataclass


class DbcSyntaxError(Exception):
    """Syntax error with a 1-based position in the file."""

    def __init__(self, message: str, line: int, col: int) -> None:
        super().__init__(f"Zeile {line}, Spalte {col}: {message}")
        self.message = message
        self.line = line
        self.col = col


class T(enum.Enum):
    IDENT = "Bezeichner"
    NUMBER = "Zahl"
    STRING = "String"
    PUNCT = "Zeichen"
    EOF = "Dateiende"


@dataclass(frozen=True, slots=True)
class Token:
    type: T
    text: str
    start: int
    end: int
    line: int
    col: int
    line_start: bool  # only whitespace precedes the token on its line

    @property
    def string_value(self) -> str:
        """Content of a STRING token with \\" and \\\\ unescaped."""
        return unescape(self.text[1:-1])


_TOKEN_RE = re.compile(
    r"""
      (?P<ws>[ \t\r\n\f\v\x00]+)
    | (?P<comment>//[^\n]*)
    | (?P<string>"(?:\\.|[^"\\])*")
    | (?P<number>[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<punct>[:;,|@()\[\]+\-])
    """,
    re.VERBOSE | re.DOTALL,
)
_DIGIT_IDENT_RE = re.compile(r"\d+[A-Za-z_][A-Za-z0-9_]*")
_IDENT_CHAR_RE = re.compile(r"[A-Za-z_]")
_ESCAPE_RE = re.compile(r"\\([\\\"])")


def unescape(s: str) -> str:
    return _ESCAPE_RE.sub(r"\1", s)


def escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def tokenize(text: str) -> list[Token]:
    line_starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    tokens: list[Token] = []
    pos = 0
    n = len(text)

    def position(offset: int) -> tuple[int, int]:
        line = bisect.bisect_right(line_starts, offset)
        return line, offset - line_starts[line - 1] + 1

    while pos < n:
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            line, col = position(pos)
            if text[pos] == '"':
                raise DbcSyntaxError("String ohne schließendes Anführungszeichen", line, col)
            raise DbcSyntaxError(f"unerwartetes Zeichen {text[pos]!r}", line, col)
        kind = m.lastgroup
        end = m.end()
        if kind == "number" and end < n and _IDENT_CHAR_RE.match(text[end]) and m.group().isdigit():
            # Not allowed by the DBC format, but found in real files: names such as
            # "2017_5" or "0_COUNTER". Only plain digits followed by a letter or "_"
            # qualify, so numbers like "1e5" stay numbers.
            m = _DIGIT_IDENT_RE.match(text, pos)
            kind, end = "ident", m.end()
        if kind not in ("ws", "comment"):
            line, col = position(pos)
            line_start = text[line_starts[line - 1] : pos].strip() == ""
            ttype = {"string": T.STRING, "number": T.NUMBER, "ident": T.IDENT, "punct": T.PUNCT}[kind]
            tokens.append(Token(ttype, text[pos:end], pos, end, line, col, line_start))
        pos = end
    line, col = position(n)
    tokens.append(Token(T.EOF, "", n, n, line, col, True))
    return tokens
