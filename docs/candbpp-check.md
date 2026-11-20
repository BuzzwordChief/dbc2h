# Manueller Check mit Vector CANdb++

Zweck: klären, ob CANdb++ die Dateien von dbc2h ohne Verluste liest und schreibt
(PLAN.md 2.3 und 2.4, Abnahme von M1). Die Dateien erzeugt `tools/make_candbpp_check.py`.

| Datei | Encoding | Inhalt |
|---|---|---|
| `tests/data/candbpp/dbc2h_check_cp1252.dbc` | cp1252, CRLF | Standard für neue Dateien |
| `tests/data/candbpp/dbc2h_check_utf8.dbc` | UTF-8 ohne BOM, CRLF | gleicher Inhalt plus ein Kommentar mit `→ ≤ µ` |

Beide enthalten zwei Knoten, eine Wertetabelle, eine Standard- und eine Extended-Nachricht
(einfaches Multiplexing), Kommentare mit Umlauten, `°C` als Einheit und alle `dbc2h_*`-Attribute
mit einem Wert, der vom Default abweicht.

## Ablauf

Für jede der beiden Dateien:

1. In CANdb++ öffnen.
2. Die Fragen unten beantworten.
3. Ohne Änderung unter neuem Namen speichern (z. B. `…_candb.dbc`) und die Datei zurückgeben.
   Mit `diff` sieht man dann, was CANdb++ beim Speichern umschreibt oder verwirft.

## Fragen

| # | Frage | Erwartet |
|---|---|---|
| 1 | Öffnet CANdb++ die Datei ohne Fehler- oder Warnmeldung? | ja |
| 2 | Erscheinen alle 11 `dbc2h_*`-Attribute unter *View → Attribute Definitions* mit dem richtigen Objekttyp (7 × Network, 1 × Message, 3 × Signal)? | ja |
| 3 | Haben die Attribute die Werte aus der Tabelle unten? | ja |
| 4 | Wie zeigt CANdb++ den Kommentar der Nachricht `VehicleStatus` an? | `Pfad C:\temp\neu und "Anführungszeichen"` (einfache Backslashes). Steht dort `C:\\temp\\neu`, behandelt CANdb++ `\\` nicht als Escape. Dann muss der Writer Backslashes unverändert schreiben. |
| 5 | Werden `°C` (Einheit von `coolant_temp`) und `ü` (Kommentar von `speed`) richtig angezeigt? | ja, in beiden Dateien |
| 6 | Nur UTF-8-Datei: Wird der Kommentar von `coolant_temp` (`→ ≤ µ`) richtig angezeigt? | Offen. Davon hängt ab, ob UTF-8 als Standard für neue Dateien taugt. |
| 7 | Sind nach dem Speichern in CANdb++ noch alle `dbc2h_*`-Attribute samt Werten in der Datei? | ja |
| 8 | Welches Encoding hat die von CANdb++ gespeicherte UTF-8-Datei? | Offen. Bleibt sie UTF-8 oder wird sie zu cp1252? |

## Erwartete Attributwerte

| Objekt | Attribut | Wert in der Datei | Bedeutung |
|---|---|---|---|
| Network | `dbc2h_Prefix` | `pt` | |
| Network | `dbc2h_FloatType` | `1` | `double` |
| Network | `dbc2h_StrictQuantization` | `1` | an |
| Network | `dbc2h_FloatEpsLsb` | `0.03125` | 1/32 LSB |
| Network | `dbc2h_OutputMode` | `1` | `split` |
| Network | `dbc2h_OutputDir` | `gen/can` | |
| Network | `dbc2h_Lint` | `E103=warning,W405=off` | |
| Message `ExtendedMsg` | `dbc2h_MsgIgnore` | `1` | |
| Signal `speed` | `dbc2h_SigFloatType` | `1` | `float` |
| Signal `coolant_temp` | `dbc2h_SigIgnore` | `1` | |
| Signal `gear` | `dbc2h_SigEnumComplete` | `1` | |

## Ergebnis

Noch nicht durchgeführt.
