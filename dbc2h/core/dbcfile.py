"""A DBC file on disk: document plus encoding and the state of the file when loaded."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dbc2h.core import textio
from dbc2h.core.document import Document
from dbc2h.core.parser import parse

NEW_FILE_NEWLINE = "\r\n"  # what Vector CANdb++ writes

# Skeleton of a new file, in the layout CANdb++ uses.
NEW_FILE_SYMBOLS = (
    "NS_DESC_ CM_ BA_DEF_ BA_ VAL_ CAT_DEF_ CAT_ FILTER BA_DEF_DEF_ EV_DATA_ ENVVAR_DATA_ "
    "SGTYPE_ SGTYPE_VAL_ BA_DEF_SGTYPE_ BA_SGTYPE_ SIG_TYPE_REF_ VAL_TABLE_ SIG_GROUP_ "
    "SIG_VALTYPE_ SIGTYPE_VALTYPE_ BO_TX_BU_ BA_DEF_REL_ BA_REL_ BA_DEF_DEF_REL_ BU_SG_REL_ "
    "BU_EV_REL_ BU_BO_REL_ SG_MUL_VAL_"
).split()


def new_file_text(newline: str = NEW_FILE_NEWLINE) -> str:
    nl = newline
    symbols = "".join(f"{nl}\t{s}" for s in NEW_FILE_SYMBOLS)
    return f'VERSION ""{nl}{nl}{nl}NS_ : {symbols}{nl}{nl}BS_:{nl}{nl}BU_:{nl}{nl}{nl}'


@dataclass(eq=False)
class DbcFile:
    document: Document
    path: Path | None = None
    format: textio.FileFormat = textio.FileFormat()
    state: textio.FileState | None = None  # None: never saved
    backed_up: bool = False  # a .bak was written in this session

    @classmethod
    def new(cls) -> DbcFile:
        return cls(parse(new_file_text()))

    @classmethod
    def load(cls, path: Path) -> DbcFile:
        text, fmt, state = textio.read(path)
        return cls(parse(text), path, fmt, state)

    def encoded(self) -> bytes:
        return textio.encode(self.document.text(), self.format)

    def can_store(self, text: str) -> bool:
        """True if ``text`` can be written in the encoding of this file."""
        return textio.can_encode(text, self.format)

    def save(self, path: Path | None = None, *, force: bool = False) -> None:
        """Save atomically. Raises textio.ExternalChangeError if the file changed on disk
        since it was loaded (unless ``force``), textio.SaveError on write errors."""
        target = path or self.path
        if target is None:
            raise ValueError("no path given for a new file")
        same_file = self.path is not None and Path(target).resolve() == Path(self.path).resolve()
        expected = self.state if same_file and not force else None
        self.state = textio.save(target, self.encoded(), expected, backup=same_file and not self.backed_up)
        if same_file:
            self.backed_up = True
        self.path = Path(target)
        self.document.commit_rendering()
