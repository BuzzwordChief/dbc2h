"""Parser: DBC text -> Document (statements and model).

Two passes per statement: first the statement is delimited in the token stream, then
its tokens are interpreted. Unknown statements are kept as opaque text. Statements
that refer to objects which do not exist are kept as referencing text (orphans).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from dbc2h.core.document import Document, detect_newline
from dbc2h.core.lexer import DbcSyntaxError, T, Token, tokenize
from dbc2h.core.model import (
    AttributeDefinition,
    AttributeType,
    AttributeValue,
    ByteOrder,
    Message,
    Multiplexing,
    Network,
    Node,
    Number,
    ObjectType,
    Signal,
    ValueTable,
    ValueType,
)
from dbc2h.core.statements import AttributeTarget, Ref, RefKind, Statement, StatementClass

MODELLED = StatementClass.MODELLED
REFERENCING = StatementClass.REFERENCING
OPAQUE = StatementClass.OPAQUE

KEYWORD_RE = re.compile(r"^[A-Z][A-Z0-9_]*_$")
RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
OBJECT_KEYWORDS = {"BU_", "BO_", "SG_", "EV_"}

# How a statement ends: at ";" (default), at the end of its line, at the next statement,
# or (NS_) at the first line that is not indented.
LINE_STATEMENTS = {"VERSION", "BS_", "BO_", "SG_"}
UNTIL_NEXT_STATEMENT = {"BU_"}


def parse(text: str) -> Document:
    return _Parser(text).run()


class _Cursor:
    """Sequential access to the tokens of one statement."""

    def __init__(self, tokens: list[Token], kind: str) -> None:
        self.toks = tokens
        self.i = 0
        self.kind = kind

    def peek(self, k: int = 0) -> Token | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def done(self) -> bool:
        return self.i >= len(self.toks)

    def error(self, message: str, tok: Token | None = None) -> DbcSyntaxError:
        tok = tok or self.peek()
        if tok is None:
            last = self.toks[-1]
            return DbcSyntaxError(f"{self.kind}: {message}", last.line, last.col + len(last.text))
        return DbcSyntaxError(f"{self.kind}: {message}", tok.line, tok.col)

    def accept(self, ttype: T, text: str | None = None) -> Token | None:
        tok = self.peek()
        if tok is not None and tok.type is ttype and (text is None or tok.text == text):
            self.i += 1
            return tok
        return None

    def expect(self, ttype: T, text: str | None = None, what: str | None = None) -> Token:
        tok = self.accept(ttype, text)
        if tok is None:
            found = self.peek()
            desc = what or (f"'{text}'" if text else ttype.value)
            got = f", gefunden: '{found.text}'" if found else ", Statement endet vorher"
            raise self.error(f"{desc} erwartet{got}")
        return tok

    def number(self, what: str) -> Number:
        return Number.parse(self.expect(T.NUMBER, what=what).text)

    def integer(self, what: str) -> tuple[int, Token]:
        tok = self.expect(T.NUMBER, what=what)
        n = Number.parse(tok.text)
        if not n.is_integer():
            raise self.error(f"{what}: ganze Zahl erwartet, gefunden: '{tok.text}'", tok)
        return int(n), tok

    def string(self, what: str) -> str:
        return self.expect(T.STRING, what=what).string_value

    def value(self, what: str) -> tuple[AttributeValue, Token]:
        """Attribute value: number, quoted string or bare word."""
        tok = self.peek()
        if tok is None or tok.type not in (T.NUMBER, T.STRING, T.IDENT):
            raise self.error(f"{what} erwartet")
        self.i += 1
        if tok.type is T.NUMBER:
            return Number.parse(tok.text), tok
        if tok.type is T.STRING:
            return tok.string_value, tok
        return tok.text, tok

    def finish(self) -> None:
        self.accept(T.PUNCT, ";")
        if not self.done():
            raise self.error(f"unerwartet: '{self.peek().text}'")


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.toks = tokenize(text)
        self.network = Network()
        self.statements: list[Statement] = []
        self.prev_end = 0
        self.current_message: Message | None = None
        self.warnings: list[str] = []
        self.handlers: dict[str, Callable[[_Cursor, Statement], None]] = {
            "VERSION": self._version,
            "NS_": self._ns,
            "BS_": self._bs,
            "BU_": self._bu,
            "VAL_TABLE_": self._val_table,
            "BO_": self._bo,
            "SG_": self._sg,
            "BO_TX_BU_": self._bo_tx_bu,
            "EV_": self._ev,
            "ENVVAR_DATA_": self._envvar_data,
            "CM_": self._cm,
            "BA_DEF_": self._ba_def,
            "BA_DEF_DEF_": self._ba_def_def,
            "BA_": self._ba,
            "VAL_": self._val,
            "SIG_GROUP_": self._sig_group,
            "SIG_VALTYPE_": self._sig_valtype,
            "SG_MUL_VAL_": self._sg_mul_val,
            "BA_DEF_REL_": self._ba_def_rel,
            "BA_DEF_DEF_REL_": self._ba_def_def_rel,
            "BA_REL_": self._ba_rel,
        }

    # --- delimiting ------------------------------------------------------------------

    def _is_statement_start(self, tok: Token) -> bool:
        return tok.type is T.IDENT and tok.line_start and (tok.text == "VERSION" or bool(KEYWORD_RE.match(tok.text)))

    def _statement_end(self, i: int, kind: str) -> int:
        """Index one past the last token of the statement starting at tokens[i]."""
        toks = self.toks
        j = i + 1
        if kind in LINE_STATEMENTS:
            while toks[j].type is not T.EOF and not toks[j].line_start:
                j += 1
        elif kind == "NS_":
            while toks[j].type is not T.EOF and not (toks[j].line_start and toks[j].col == 1):
                j += 1
        elif kind in UNTIL_NEXT_STATEMENT:
            while toks[j].type is not T.EOF and not self._is_statement_start(toks[j]):
                j += 1
        else:
            while toks[j].type is not T.EOF and not self._is_statement_start(toks[j]):
                j += 1
                if toks[j - 1].type is T.PUNCT and toks[j - 1].text == ";":
                    break
        return j

    def run(self) -> Document:
        i = 0
        while self.toks[i].type is not T.EOF:
            first = self.toks[i]
            kind = first.text if first.type is T.IDENT else "?"
            end = self._statement_end(i, kind)
            stmt_toks = self.toks[i:end]
            stmt = Statement(
                kind=kind,
                cls=OPAQUE,
                text=self.text[first.start : stmt_toks[-1].end],
                prefix=self.text[self.prev_end : first.start],
                line=first.line,
            )
            handler = self.handlers.get(kind)
            if handler is not None:
                cur = _Cursor(stmt_toks, kind)
                cur.i = 1
                handler(cur, stmt)
            self.statements.append(stmt)
            self.prev_end = stmt_toks[-1].end
            i = end
        return Document(
            network=self.network,
            statements=self.statements,
            trailer=self.text[self.prev_end :],
            newline=detect_newline(self.text),
            warnings=self.warnings,
        )

    # --- helpers ----------------------------------------------------------------------

    def _warn(self, tok: Token, message: str) -> None:
        self.warnings.append(f"Zeile {tok.line}, Spalte {tok.col}: {message}")

    def _check_name(self, tok: Token) -> None:
        if tok.text[0].isdigit():
            self._warn(tok, f"Name '{tok.text}' beginnt mit einer Ziffer (in DBC nicht erlaubt)")

    def _ref(self, stmt: Statement, kind: RefKind, tok: Token, message_id: int | None = None, name: str | None = None) -> Ref:
        base = self.toks_start(stmt)
        start, end = tok.start - base, tok.end - base
        if tok.type is T.STRING:  # the reference is the content, without the quotes
            start, end = start + 1, end - 1
        return Ref(kind, name if name is not None else tok.text, start, end, message_id)

    def toks_start(self, stmt: Statement) -> int:
        return self.prev_end + len(stmt.prefix)

    def _referencing(self, stmt: Statement, refs: list[Ref], **info) -> None:
        stmt.cls = REFERENCING
        stmt.refs = tuple(refs)
        stmt.info.update(info)

    def _modelled(self, stmt: Statement, target, src_owner, key: str) -> None:
        stmt.cls = MODELLED
        stmt.target = target
        src_owner.src[key] = stmt

    # --- statements -------------------------------------------------------------------

    def _version(self, c: _Cursor, stmt: Statement) -> None:
        self.network.version = c.string("Versions-String")
        c.finish()
        self._modelled(stmt, self.network, self.network, "VERSION")

    def _ns(self, c: _Cursor, stmt: Statement) -> None:
        c.expect(T.PUNCT, ":")
        while not c.done():
            self.network.new_symbols.append(c.expect(T.IDENT, what="Symbol").text)
        self._modelled(stmt, self.network, self.network, "NS_")

    def _bs(self, c: _Cursor, stmt: Statement) -> None:
        colon = c.expect(T.PUNCT, ":")
        if not c.done():
            self.network.bit_timing = self.text[colon.end : c.toks[-1].end].strip()
        self._modelled(stmt, self.network, self.network, "BS_")

    def _bu(self, c: _Cursor, stmt: Statement) -> None:
        c.expect(T.PUNCT, ":")
        nodes = []
        while not c.done():
            if c.accept(T.PUNCT, ","):
                continue
            tok = c.expect(T.IDENT, what="Knotenname")
            self._check_name(tok)
            nodes.append(Node(tok.text))
        self.network.nodes.extend(nodes)
        stmt.info["nodes"] = nodes
        self._modelled(stmt, self.network, self.network, "BU_")

    def _val_table(self, c: _Cursor, stmt: Statement) -> None:
        table = ValueTable(c.expect(T.IDENT, what="Name der Wertetabelle").text)
        table.entries = self._value_pairs(c)
        c.finish()
        self.network.value_tables.append(table)
        self._modelled(stmt, table, table, "VAL_TABLE_")

    def _value_pairs(self, c: _Cursor) -> list[tuple[Number, str]]:
        pairs = []
        while (tok := c.peek()) is not None and tok.type is T.NUMBER:
            value = c.number("Wert")
            pairs.append((value, c.string("Wertbeschreibung")))
        return pairs

    def _bo(self, c: _Cursor, stmt: Statement) -> None:
        raw_id, _ = c.integer("Nachrichten-ID")
        name_tok = c.expect(T.IDENT, what="Nachrichtenname")
        name = name_tok.text
        self._check_name(name_tok)
        c.expect(T.PUNCT, ":")
        size, _ = c.integer("DLC")
        tx = c.accept(T.IDENT)
        c.finish()
        msg = Message(raw_id, name, size, tx.text if tx else None)
        self.network.messages.append(msg)
        self.current_message = msg
        self._modelled(stmt, msg, msg, "BO_")

    def _sg(self, c: _Cursor, stmt: Statement) -> None:
        if self.current_message is None:
            raise c.error("SG_ ohne vorangehendes BO_", c.toks[0])
        name_tok = c.expect(T.IDENT, what="Signalname")
        name = name_tok.text
        self._check_name(name_tok)
        mux = Multiplexing()
        if (tok := c.accept(T.IDENT)) is not None:
            mux = Multiplexing(tok.text)
            if not mux.is_valid:
                self._warn(tok, f"ungültige Multiplex-Angabe '{tok.text}' bei Signal {name}, wird ignoriert")
        c.expect(T.PUNCT, ":")
        start, _ = c.integer("Startbit")
        c.expect(T.PUNCT, "|")
        length, _ = c.integer("Länge")
        c.expect(T.PUNCT, "@")
        order_tok = c.expect(T.NUMBER, what="Byte-Order (0 oder 1)")
        if order_tok.text not in ("0", "1"):
            raise c.error(f"Byte-Order 0 oder 1 erwartet, gefunden: '{order_tok.text}'", order_tok)
        sign = c.accept(T.PUNCT, "+") or c.expect(T.PUNCT, "-", what="Vorzeichen (+ oder -)")
        c.expect(T.PUNCT, "(")
        factor = c.number("Faktor")
        c.expect(T.PUNCT, ",")
        offset = c.number("Offset")
        c.expect(T.PUNCT, ")")
        c.expect(T.PUNCT, "[")
        minimum = c.number("Minimum")
        c.expect(T.PUNCT, "|")
        maximum = c.number("Maximum")
        c.expect(T.PUNCT, "]")
        unit = c.string("Einheit")
        receivers = []
        while not c.done():
            if c.accept(T.PUNCT, ","):
                continue
            receivers.append(c.expect(T.IDENT, what="Empfänger").text)
        sig = Signal(
            name=name, start_bit=start, length=length, byte_order=ByteOrder(int(order_tok.text)),
            is_signed=sign.text == "-", factor=factor, offset=offset, minimum=minimum,
            maximum=maximum, unit=unit, receivers=receivers, mux=mux,
            message=self.current_message,
        )
        self.current_message.signals.append(sig)
        self._modelled(stmt, sig, sig, "SG_")

    def _bo_tx_bu(self, c: _Cursor, stmt: Statement) -> None:
        raw_id, id_tok = c.integer("Nachrichten-ID")
        c.expect(T.PUNCT, ":")
        refs = [self._ref(stmt, RefKind.MESSAGE, id_tok)]
        while not c.done() and not c.accept(T.PUNCT, ";"):
            if c.accept(T.PUNCT, ","):
                continue
            refs.append(self._ref(stmt, RefKind.NODE, c.expect(T.IDENT, what="Knotenname")))
        self._referencing(stmt, refs, message_id=raw_id)

    def _ev(self, c: _Cursor, stmt: Statement) -> None:
        refs = [self._ref(stmt, RefKind.ENVVAR, c.expect(T.IDENT, what="Name der Umgebungsvariable"))]
        in_nodes = False
        for tok in c.toks[c.i :]:
            if tok.type is T.IDENT and tok.text.startswith("DUMMY_NODE_VECTOR"):
                in_nodes = True
            elif in_nodes and tok.type is T.IDENT and tok.text != "Vector__XXX":
                refs.append(self._ref(stmt, RefKind.NODE, tok))
        self._referencing(stmt, refs)

    def _envvar_data(self, c: _Cursor, stmt: Statement) -> None:
        tok = c.expect(T.IDENT, what="Name der Umgebungsvariable")
        self._referencing(stmt, [self._ref(stmt, RefKind.ENVVAR, tok)])

    def _object_ref(self, c: _Cursor, stmt: Statement) -> tuple[object | None, ObjectType, list[Ref]]:
        """Parse an optional "BU_ n | BO_ id | SG_ id s | EV_ e" object reference.

        Returns the model object (None if it does not exist or is an env var), the
        object type, and the references in the statement.
        """
        tok = c.peek()
        if tok is None or tok.type is not T.IDENT or tok.text not in OBJECT_KEYWORDS:
            return self.network, ObjectType.NETWORK, []
        c.i += 1
        if tok.text == "BU_":
            name = c.expect(T.IDENT, what="Knotenname")
            return self.network.node(name.text), ObjectType.NODE, [self._ref(stmt, RefKind.NODE, name)]
        if tok.text == "EV_":
            name = c.expect(T.IDENT, what="Name der Umgebungsvariable")
            return None, ObjectType.ENVVAR, [self._ref(stmt, RefKind.ENVVAR, name)]
        raw_id, id_tok = c.integer("Nachrichten-ID")
        msg = self.network.message(raw_id)
        refs = [self._ref(stmt, RefKind.MESSAGE, id_tok)]
        if tok.text == "BO_":
            return msg, ObjectType.MESSAGE, refs
        sig_tok = c.expect(T.IDENT, what="Signalname")
        refs.append(self._ref(stmt, RefKind.SIGNAL, sig_tok, message_id=raw_id))
        return (msg.signal(sig_tok.text) if msg else None), ObjectType.SIGNAL, refs

    def _cm(self, c: _Cursor, stmt: Statement) -> None:
        obj, otype, refs = self._object_ref(c, stmt)
        comment = c.string("Kommentar")
        c.finish()
        if obj is None:
            self._referencing(stmt, refs, orphan=otype is not ObjectType.ENVVAR)
            return
        obj.comment = comment
        self._modelled(stmt, obj, obj, "CM_")

    def _ba_def(self, c: _Cursor, stmt: Statement) -> None:
        otype = ObjectType.NETWORK
        if (tok := c.peek()) is not None and tok.type is T.IDENT and tok.text in OBJECT_KEYWORDS:
            c.i += 1
            otype = ObjectType(tok.text)
        name = c.string("Attributname")
        type_tok = c.expect(T.IDENT, what="Attributtyp")
        try:
            atype = AttributeType(type_tok.text)
        except ValueError:
            raise c.error(f"unbekannter Attributtyp '{type_tok.text}'", type_tok) from None
        defn = AttributeDefinition(name, otype, atype)
        if atype in (AttributeType.INT, AttributeType.HEX, AttributeType.FLOAT):
            if (tok := c.peek()) is not None and tok.type is T.NUMBER:
                defn.minimum = c.number("Minimum")
                defn.maximum = c.number("Maximum")
        elif atype is AttributeType.ENUM:
            while (tok := c.peek()) is not None and tok.type in (T.STRING, T.PUNCT) and tok.text != ";":
                if not c.accept(T.PUNCT, ","):
                    defn.choices.append(c.string("Enum-Wert"))
        c.finish()
        self.network.attribute_definitions.append(defn)
        self._modelled(stmt, defn, defn, "BA_DEF_")

    def _ba_def_def(self, c: _Cursor, stmt: Statement) -> None:
        name_tok = c.expect(T.STRING, what="Attributname")
        value, _ = c.value("Default-Wert")
        c.finish()
        defn = self.network.attribute_definition(name_tok.string_value)
        if defn is None:
            ref = self._ref(stmt, RefKind.ATTRIBUTE, name_tok, name=name_tok.string_value)
            self._referencing(stmt, [ref], orphan=True)
            return
        defn.default = value
        self._modelled(stmt, defn, defn, "BA_DEF_DEF_")

    def _ba(self, c: _Cursor, stmt: Statement) -> None:
        name_tok = c.expect(T.STRING, what="Attributname")
        name = name_tok.string_value
        obj, otype, refs = self._object_ref(c, stmt)
        value, _ = c.value("Attributwert")
        c.finish()
        if obj is None:
            refs.insert(0, self._ref(stmt, RefKind.ATTRIBUTE, name_tok, name=name))
            self._referencing(stmt, refs, orphan=otype is not ObjectType.ENVVAR)
            return
        obj.attributes[name] = value
        self._modelled(stmt, AttributeTarget(obj, name), obj, f"BA_:{name}")

    def _val(self, c: _Cursor, stmt: Statement) -> None:
        tok = c.peek()
        if tok is not None and tok.type is T.IDENT:  # VAL_ of an environment variable
            c.i += 1
            self._value_pairs(c)
            c.finish()
            self._referencing(stmt, [self._ref(stmt, RefKind.ENVVAR, tok)])
            return
        raw_id, id_tok = c.integer("Nachrichten-ID")
        sig_tok = c.expect(T.IDENT, what="Signalname")
        entries = self._value_pairs(c)
        c.finish()
        msg = self.network.message(raw_id)
        sig = msg.signal(sig_tok.text) if msg else None
        if sig is None:
            refs = [self._ref(stmt, RefKind.MESSAGE, id_tok),
                    self._ref(stmt, RefKind.SIGNAL, sig_tok, message_id=raw_id)]
            self._referencing(stmt, refs, orphan=True)
            return
        sig.values = entries
        self._modelled(stmt, sig, sig, "VAL_")

    def _sig_group(self, c: _Cursor, stmt: Statement) -> None:
        raw_id, id_tok = c.integer("Nachrichten-ID")
        c.expect(T.IDENT, what="Name der Signalgruppe")
        c.integer("Wiederholungen")
        c.expect(T.PUNCT, ":")
        refs = [self._ref(stmt, RefKind.MESSAGE, id_tok)]
        while not c.done() and not c.accept(T.PUNCT, ";"):
            if c.accept(T.PUNCT, ","):
                continue
            refs.append(self._ref(stmt, RefKind.SIGNAL, c.expect(T.IDENT, what="Signalname"), message_id=raw_id))
        self._referencing(stmt, refs, message_id=raw_id)

    def _sig_valtype(self, c: _Cursor, stmt: Statement) -> None:
        raw_id, id_tok = c.integer("Nachrichten-ID")
        sig_tok = c.expect(T.IDENT, what="Signalname")
        c.accept(T.PUNCT, ":")
        vt, vt_tok = c.integer("Wertetyp")
        c.finish()
        if vt not in (0, 1, 2):
            raise c.error(f"Wertetyp 0, 1 oder 2 erwartet, gefunden: {vt}", vt_tok)
        msg = self.network.message(raw_id)
        sig = msg.signal(sig_tok.text) if msg else None
        if sig is None:
            refs = [self._ref(stmt, RefKind.MESSAGE, id_tok),
                    self._ref(stmt, RefKind.SIGNAL, sig_tok, message_id=raw_id)]
            self._referencing(stmt, refs, orphan=True)
            return
        sig.value_type = ValueType(vt)
        self._modelled(stmt, sig, sig, "SIG_VALTYPE_")

    def _sg_mul_val(self, c: _Cursor, stmt: Statement) -> None:
        raw_id, id_tok = c.integer("Nachrichten-ID")
        sig_tok = c.expect(T.IDENT, what="Signalname")
        mux_tok = c.expect(T.IDENT, what="Name des Multiplexers")
        ranges_text = self.text[mux_tok.end : c.toks[-1].end]
        ranges = [(int(a), int(b)) for a, b in RANGE_RE.findall(ranges_text)]
        refs = [
            self._ref(stmt, RefKind.MESSAGE, id_tok),
            self._ref(stmt, RefKind.SIGNAL, sig_tok, message_id=raw_id),
            self._ref(stmt, RefKind.SIGNAL, mux_tok, message_id=raw_id),
        ]
        self._referencing(stmt, refs, message_id=raw_id, signal=sig_tok.text, multiplexer=mux_tok.text, ranges=ranges)

    def _ba_def_rel(self, c: _Cursor, stmt: Statement) -> None:
        c.expect(T.IDENT, what="Relationstyp")
        name_tok = c.expect(T.STRING, what="Attributname")
        self._referencing(stmt, [self._ref(stmt, RefKind.ATTRIBUTE, name_tok, name=name_tok.string_value)])

    def _ba_def_def_rel(self, c: _Cursor, stmt: Statement) -> None:
        name_tok = c.expect(T.STRING, what="Attributname")
        self._referencing(stmt, [self._ref(stmt, RefKind.ATTRIBUTE, name_tok, name=name_tok.string_value)])

    def _ba_rel(self, c: _Cursor, stmt: Statement) -> None:
        name_tok = c.expect(T.STRING, what="Attributname")
        refs = [self._ref(stmt, RefKind.ATTRIBUTE, name_tok, name=name_tok.string_value)]
        rel = c.expect(T.IDENT, what="Relationstyp")
        refs.append(self._ref(stmt, RefKind.NODE, c.expect(T.IDENT, what="Knotenname")))
        if rel.text == "BU_SG_REL_":
            c.expect(T.IDENT, "SG_")
            raw_id, id_tok = c.integer("Nachrichten-ID")
            refs.append(self._ref(stmt, RefKind.MESSAGE, id_tok))
            refs.append(self._ref(stmt, RefKind.SIGNAL, c.expect(T.IDENT, what="Signalname"), message_id=raw_id))
        elif rel.text == "BU_BO_REL_":
            _, id_tok = c.integer("Nachrichten-ID")
            refs.append(self._ref(stmt, RefKind.MESSAGE, id_tok))
        elif rel.text == "BU_EV_REL_":
            refs.append(self._ref(stmt, RefKind.ENVVAR, c.expect(T.IDENT, what="Name der Umgebungsvariable")))
        self._referencing(stmt, refs)
