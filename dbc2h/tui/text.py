"""Safe display of text from DBC files.

DBC content is untrusted: control characters could manipulate the terminal and square
brackets would be read as Rich markup. Everything shown goes through ``text()``.
"""

from __future__ import annotations

import re

from rich.text import Text

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(value: object, *, single_line: bool = True) -> str:
    s = "" if value is None else str(value)
    if single_line:
        s = s.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return _CONTROL.sub("�", s)


def text(value: object, *, single_line: bool = True, style: str = "") -> Text:
    """Plain Text object (never parsed as markup) with control characters replaced."""
    return Text(clean(value, single_line=single_line), style=style, end="")
