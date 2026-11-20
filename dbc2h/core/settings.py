"""dbc2h settings stored as DBC attributes (PLAN.md 2.4).

Reading never changes the file. A definition (BA_DEF_, BA_DEF_DEF_) is only written
when a value is first set to something other than its default, and a value equal to
the default is stored by removing the BA_ statement. A DBC that is only opened and
generated from therefore stays byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from dbc2h.core.document import Document
from dbc2h.core.model import AttributeDefinition, AttributeType, Message, Network, Number, ObjectType, Signal

SettingValue = str | int | Fraction

MAX_NAME_LENGTH = 32


@dataclass(frozen=True, slots=True)
class Setting:
    name: str
    object_type: ObjectType
    type: AttributeType
    default: SettingValue
    choices: tuple[str, ...] = ()
    minimum: SettingValue | None = None
    maximum: SettingValue | None = None


def _bool(name: str, object_type: ObjectType) -> Setting:
    return Setting(name, object_type, AttributeType.INT, 0, minimum=0, maximum=1)


SETTINGS: dict[str, Setting] = {
    s.name: s
    for s in (
        Setting("dbc2h_Prefix", ObjectType.NETWORK, AttributeType.STRING, ""),
        Setting("dbc2h_FloatType", ObjectType.NETWORK, AttributeType.ENUM, "float", ("float", "double")),
        _bool("dbc2h_StrictQuantization", ObjectType.NETWORK),
        Setting("dbc2h_FloatEpsLsb", ObjectType.NETWORK, AttributeType.FLOAT, Fraction(1, 16),
                minimum=Fraction(0), maximum=Fraction(1, 2)),
        Setting("dbc2h_OutputMode", ObjectType.NETWORK, AttributeType.ENUM, "header_only", ("header_only", "split")),
        Setting("dbc2h_OutputDir", ObjectType.NETWORK, AttributeType.STRING, "."),
        Setting("dbc2h_Lint", ObjectType.NETWORK, AttributeType.STRING, ""),
        _bool("dbc2h_MsgIgnore", ObjectType.MESSAGE),
        Setting("dbc2h_SigFloatType", ObjectType.SIGNAL, AttributeType.ENUM, "default", ("default", "float", "double")),
        _bool("dbc2h_SigIgnore", ObjectType.SIGNAL),
        _bool("dbc2h_SigEnumComplete", ObjectType.SIGNAL),
    )
}


class SettingError(ValueError):
    pass


def _object_type(obj) -> ObjectType:
    match obj:
        case Network():
            return ObjectType.NETWORK
        case Message():
            return ObjectType.MESSAGE
        case Signal():
            return ObjectType.SIGNAL
    raise SettingError(f"dbc2h-Einstellungen gibt es nicht für {type(obj).__name__}")


def _spec(name: str, obj) -> Setting:
    spec = SETTINGS.get(name)
    if spec is None:
        raise SettingError(f"unbekannte Einstellung {name}")
    if spec.object_type is not _object_type(obj):
        raise SettingError(f"{name} gilt für {spec.object_type.name}, nicht für {type(obj).__name__}")
    return spec


def _matching_definition(network: Network, spec: Setting) -> AttributeDefinition | None:
    """The file's definition, if it is compatible with ours."""
    d = network.attribute_definition(spec.name)
    if d is None or d.object_type is not spec.object_type or d.type is not spec.type:
        return None
    if spec.type is AttributeType.ENUM and not set(spec.choices) <= set(d.choices):
        return None
    return d


def conflicts(network: Network) -> list[str]:
    """Definitions in the file that use a dbc2h name but are incompatible with it."""
    out = []
    for spec in SETTINGS.values():
        d = network.attribute_definition(spec.name)
        if d is not None and _matching_definition(network, spec) is None:
            out.append(f"Attribut {spec.name} ist in der Datei anders definiert als von dbc2h erwartet")
    return out


def _interpret(spec: Setting, raw, choices: list[str]) -> SettingValue | None:
    """Typed value of a raw attribute value, or None if it does not fit the setting."""
    try:
        match spec.type:
            case AttributeType.INT | AttributeType.HEX:
                return int(raw) if isinstance(raw, Number) else int(raw)
            case AttributeType.FLOAT:
                return raw.value if isinstance(raw, Number) else Fraction(raw)
            case AttributeType.STRING:
                return raw if isinstance(raw, str) else raw.text
            case AttributeType.ENUM:
                if isinstance(raw, Number):  # CANdb++ stores the index
                    return choices[int(raw)]
                return raw if raw in choices else None
    except (ValueError, IndexError, ZeroDivisionError):
        return None
    return None


def default(network: Network, name: str) -> SettingValue:
    spec = SETTINGS[name]
    d = _matching_definition(network, spec)
    if d is not None and d.default is not None:
        value = _interpret(spec, d.default, d.choices)
        if value is not None:
            return value
    return spec.default


def get_setting(network: Network, obj, name: str) -> SettingValue:
    spec = _spec(name, obj)
    d = _matching_definition(network, spec)
    if d is not None and name in obj.attributes:
        value = _interpret(spec, obj.attributes[name], d.choices)
        if value is not None:
            return value
    return default(network, name)


def _validate(spec: Setting, value: SettingValue) -> SettingValue:
    match spec.type:
        case AttributeType.ENUM:
            if value not in spec.choices:
                raise SettingError(f"{spec.name}: erlaubt sind {', '.join(spec.choices)}")
        case AttributeType.STRING:
            if not isinstance(value, str):
                raise SettingError(f"{spec.name}: Text erwartet")
        case AttributeType.INT:
            if isinstance(value, bool):
                value = int(value)
            if not isinstance(value, int):
                raise SettingError(f"{spec.name}: ganze Zahl erwartet")
        case AttributeType.FLOAT:
            value = Fraction(value)
    if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
        raise SettingError(f"{spec.name}: Wert außerhalb {spec.minimum} … {spec.maximum}")
    return value


def _raw(spec: Setting, value: SettingValue, definition: AttributeDefinition) -> Number | str:
    match spec.type:
        case AttributeType.ENUM:
            return Number.of(definition.choices.index(value))
        case AttributeType.STRING:
            return value
    return Number.of(value)


def ensure_definition(doc: Document, name: str) -> AttributeDefinition:
    network = doc.network
    spec = SETTINGS[name]
    d = network.attribute_definition(name)
    if d is not None:
        if _matching_definition(network, spec) is None:
            raise SettingError(f"Attribut {name} ist in der Datei anders definiert als von dbc2h erwartet")
        return d
    d = AttributeDefinition(
        name=name,
        object_type=spec.object_type,
        type=spec.type,
        minimum=Number.of(spec.minimum) if spec.minimum is not None else None,
        maximum=Number.of(spec.maximum) if spec.maximum is not None else None,
        choices=list(spec.choices),
        default=spec.default if isinstance(spec.default, str) else Number.of(spec.default),
    )
    network.attribute_definitions.append(d)
    doc.new_statement("BA_DEF_", d, d, "BA_DEF_")
    doc.new_statement("BA_DEF_DEF_", d, d, "BA_DEF_DEF_")
    return d


def set_setting(doc: Document, obj, name: str, value: SettingValue) -> None:
    spec = _spec(name, obj)
    value = _validate(spec, value)
    if value == default(doc.network, name):
        if name in obj.attributes:
            doc.remove_attribute(obj, name)
        return
    d = ensure_definition(doc, name)
    doc.set_attribute(obj, name, _raw(spec, value, d))
