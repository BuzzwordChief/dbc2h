# dbc2h – Umsetzungsplan

Werkzeug zum **Erstellen, Verwalten und Exportieren** von DBC-Dateien als benutzbare **C11-Header** (auch aus C++ einbindbar).

- **Python 3.13 oder neuer**, läuft unter **Windows, macOS und Linux**
- Auslieferung als **eine Datei `dbc2h.pyz`** (zipapp). Der Benutzer braucht nur Python, kein `pip install`.
- Fremdbibliotheken nur, wenn sie **reines Python** sind und unter einer **freien Lizenz** (MIT, BSD, PSF, Apache-2.0) stehen. Sie liegen **im Repo** (`vendor/`) und werden in die `.pyz` gebündelt.
- Verwaltung über eine **menuconfig/whiptail-artige TUI** auf Basis von **Textual**. Kein Benutzer soll DBCs über Kommandozeilenbefehle pflegen müssen.
- Festkomma-Signale werden im C-Struct als **float (Standard) oder double** abgebildet, mit **bewiesener** Darstellbarkeit. Es gibt **kein automatisches Hochstufen**, nur Fehler, die der Benutzer selbst behebt.
- Bei ungültigen Werten **lieber ein Fehler als ein Clamp**.

Stand: überarbeitet nach dem Design-Review in `REVIEW.md` (2026-11-20). Abschnitt 9 zeigt, wo welcher Befund umgesetzt ist.

---

## 1. Bedienkonzept

### 1.1 Start

```
python dbc2h.pyz                  # TUI: Datei öffnen / neue DBC anlegen
python dbc2h.pyz powertrain.dbc   # TUI direkt mit dieser Datei
python dbc2h.pyz --version        # Version von dbc2h und der gebündelten Bibliotheken
```

Für Build-Systeme und CI gibt es zusätzlich drei **nicht-interaktive** Befehle. Sie dienen nur der Automatisierung, nicht der Verwaltung, und laden Textual gar nicht erst (schneller Start, keine Terminal-Anforderungen):

```
python dbc2h.pyz gen   powertrain.dbc -o gen/          # Header erzeugen (z. B. aus CMake)
python dbc2h.pyz gen   powertrain.dbc -o gen/ --check  # Exit != 0, wenn ein erzeugter Header veraltet ist
python dbc2h.pyz check powertrain.dbc                  # Lint + Numerikprüfung, Exit != 0 bei Fehlern
```

`__main__.py` prüft als Allererstes `sys.version_info`. Dafür nutzt es nur Syntax, die auch alte Interpreter verstehen, und meldet bei Python < 3.13 klar, welche Version nötig ist.

Ausgaben in Pipes und Dateien sind auf allen Plattformen **UTF-8**. Python 3.13/3.14 würde unter Windows sonst die ANSI-Codepage (cp1252) verwenden (in M0 gefunden). Ausgaben ins Terminal behalten dessen Encoding.

### 1.2 TUI-Aufbau (menuconfig-Stil)

Zielbild. Das Standard-Theme ist klassisch blau wie menuconfig/whiptail, ein dunkles Theme ist umschaltbar. Umgesetzt wird es mit Textual-Widgets und einem eigenen CSS-Theme (`dbc2h.tcss`).

```
┌──────────────────────── dbc2h – powertrain.dbc * ────────────────────────┐
│  Pfeiltasten navigieren, <Enter> öffnet, <Esc> zurück, </> sucht, <?>    │
│  zeigt Hilfe.  [*] geändert   (!) Fehler   (~) Warnung                   │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │      Netzwerk-Einstellungen  --->                                    │ │
│ │      Knoten (4)  --->                                                │ │
│ │  (!) Nachrichten (37)  --->                                          │ │
│ │      Wertetabellen (12)  --->                                        │ │
│ │      Attribut-Definitionen (9)  --->                                 │ │
│ │      Code-Generierung  --->                                          │ │
│ │  (~) Prüfergebnisse (1 Fehler, 3 Warnungen)  --->                    │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────────┤
│   <Öffnen>   <Speichern>   <Generieren>   <Rückgängig>   <Beenden>       │
└──────────────────────────────────────────────────────────────────────────┘
```

**Menübaum** (jede Ebene ist ein Textual-`Screen` auf einem Screen-Stack):

- **Netzwerk-Einstellungen:** Name, Baudrate, CAN FD ja/nein, Kommentar
- **Knoten:** anlegen, umbenennen, löschen, Kommentar. Beim Umbenennen und Löschen werden alle Verweise mitgezogen (siehe 2.3). Vor dem Löschen erscheint eine Rückfrage mit der Liste aller Elemente, die mit entfernt werden.
- **Nachrichten:** Tabelle (`DataTable`) mit ID, Name, DLC, Sender, Zyklus und Status, sortierbar
  - Nachricht bearbeiten: Formular für ID (Standard/Extended), Name, DLC, Sender, Zykluszeit, Kommentar
  - **Signale:** Tabelle mit Startbit, Länge, Byte-Order, Typ und Numerikstatus
    - Signal bearbeiten: Formular (siehe 1.3)
  - **Bit-Layout:** grafische 8×N-Matrix (eigenes Widget). Mit den Pfeiltasten oder der Maus wird das ausgewählte Signal verschoben. Überlappungen sind rot markiert, freie Bits grau. Bei gemultiplexten Nachrichten wählt man oben den angezeigten Mux-Wert.
  - **Frame testen:** Hex-Bytes eingeben und dekodierte Werte sehen, oder Werte eingeben und den Frame sehen
- **Wertetabellen:** anlegen und bearbeiten (Wert → Text), Signalen zuordnen
- **Attribut-Definitionen:** BA_DEF_/BA_DEF_DEF_ verwalten
- **Code-Generierung:** Präfix, Ausgabeverzeichnis, header-only oder .h/.c, Standard-Gleitkommatyp (**float**/double), Strict-Quantisierung an/aus, Lint-Ausnahmen (siehe 5), **Vorschau mit Syntax-Highlighting**, Erzeugen
- **Prüfergebnisse:** Liste aller Lint- und Numerikbefunde mit Regel-ID. Mit `<Enter>` springt man direkt zum betroffenen Element.
- **Über:** Version und Lizenzen der gebündelten Bibliotheken

**Anzeige von Dateiinhalten:** Alle Texte aus der DBC (Namen, Kommentare, Wertbeschreibungen) sind nicht vertrauenswürdige Eingaben. Steuerzeichen (einschließlich 0x80–0x9F und ESC) werden vor der Anzeige ersetzt, damit eine präparierte Datei das Terminal nicht manipulieren kann.

**Tastatur** (überall gleich, im `Footer` eingeblendet). Die Maus funktioniert zusätzlich, ist aber nie Pflicht.

| Taste | Funktion |
|---|---|
| ↑ ↓ PgUp PgDn Home End | Navigation |
| Enter / Esc | Öffnen / Zurück |
| Tab / Shift+Tab | Fokuswechsel zwischen Liste, Formularfeldern und Buttons |
| `n` / `d` / `r` | Neu / Löschen / Umbenennen im aktuellen Listenkontext |
| `/` | Suche (Name, ID) |
| `u` / `U` | Rückgängig / Wiederholen |
| `s` / `g` | Speichern / Generieren |
| `?` | Kontexthilfe |
| `q` | Beenden (Rückfrage bei ungespeicherten Änderungen) |
| Strg+P | Befehlspalette: Fuzzy-Suche über alle Nachrichten, Signale und Aktionen |

Eigene Belegungen nutzen einfache Buchstaben. Strg-Kombinationen werden außer der Befehlspalette vermieden, weil Terminals sie unterschiedlich durchreichen. Buchstaben-Shortcuts greifen nur, wenn kein Eingabefeld den Fokus hat.

### 1.3 Signal-Formular mit Live-Prüfung

Alle Masken sind für **80×24** entworfen, die Mindestgröße der TUI.

```
┌──────────────── Signal bearbeiten: VehicleStatus.speed ────────────────┐
│ Name ......... [speed              ]   Einheit [km/h    ]              │
│ Startbit ..... [0   ]  Länge [16]  Byte-Order (•) Intel  ( ) Motorola  │
│ Vorzeichen ... (•) unsigned  ( ) signed   Bus-Typ (•) Int  ( ) IEEE    │
│ Faktor ....... [0.25           ]   Offset [0                ]          │
│ Min / Max .... [0              ] / [16383.75         ]                 │
│ Empfänger .... [x] ECU_Brake  [ ] ECU_Body  [x] Gateway                │
│ Wertetabelle . [— keine —          ] ▼   [ ] vollständig               │
│ C-Typ ........ (•) Standard (float)  ( ) float  ( ) double             │
│                                                                        │
│ float EXACT   Leitung 0…65535 → 0…16383.75   max. Fehler 0 LSB         │
│                                                                        │
│                      <  OK  >        <Abbrechen>                       │
└────────────────────────────────────────────────────────────────────────┘
```

- Eingabefelder nutzen Textual-Validatoren (Zahlen, Bereiche, gültige C-Bezeichner) und zeigen Fehler direkt am Feld. Zeichen, die das Encoding der Datei nicht darstellen kann, werden abgelehnt (siehe 2.3).
- Die Prüfzeile aktualisiert sich bei jeder Änderung, entprellt um ca. 150 ms. Die vollständige Prüfung läuft als Textual-Worker im Hintergrund, damit die Oberfläche flüssig bleibt. Bei Signalen nahe 24 Bit kann sie einige Sekunden dauern. Bis dahin steht dort „wird geprüft …“.
- Bei `LOSSY`, `UNREPRESENTABLE` oder `UNPROVEN` wird die Zeile rot und nennt die konkreten Abhilfen: „double wählen“, „LSB vergrößern“ oder „Länge reduzieren“. Mit einem fehlerhaften Signal lässt sich das Formular trotzdem speichern, die DBC darf zwischenzeitlich inkonsistent sein. Die **Generierung wird aber verweigert**, solange Fehler bestehen.

---

## 2. Architektur

Entwicklung als Paket, Auslieferung per Build-Skript als `dbc2h.pyz`.

```
dbc2h/
  __init__.py          Version (alte Syntax, wird vor der Versionsprüfung importiert)
  __main__.py          Versionsprüfung (alte Syntax), dann cli.main
  cli.py               Argumente: ohne Subcommand TUI, sonst gen/check, --version
  core/                ── reines Python, null Abhängigkeiten ──
    model.py           Datenmodell (dataclasses, slots=True)
    statements.py      Statement-Klassen, Originaltext, Dirty-Flag, Verweise
    parser.py          Tokenizer + Recursive-Descent-Parser, Fehler mit Zeile:Spalte
    writer.py          DBC-Ausgabe: Originaltext für unveränderte Statements
    textio.py          Encoding-Erkennung, atomares Speichern, Änderungserkennung
    formula.py         Formelbeschreibung (typisierte Operationen) mit zwei Backends:
                       Python-Auswerter und C-Emitter
    numerics.py        binary32/binary64-Emulation, Bereiche, Einstufung, Cache
    layout.py          Bitmapping Intel/Motorola, Überlappung (mux-bewusst), freie Bereiche
    lint.py            Prüfregeln mit stabiler ID, Severity und Elementverweis
    edit.py            Änderungsoperationen (Command-Pattern) mit Undo/Redo,
                       Verweise werden mitgezogen
    settings.py        Lesen/Schreiben der dbc2h_*-Attribute
    codegen_c.py       C11-Generator (nutzt formula.py)
  tui/                 ── nutzt Textual ──
    app.py             App, Screen-Stack, Bindings, Befehlspalette, Wiederherstellung
    dbc2h.tcss         Themes (menuconfig-blau, dunkel)
    screens/           main_menu, network, nodes, messages, message_form, signals,
                       signal_form, value_tables, attributes, codegen, findings,
                       frame_tester, about
    widgets/
      bitgrid.py       Bit-Layout-Editor
      check_line.py    Live-Numerikprüfung
      dialogs.py       YesNo, MsgBox, InputBox, Auswahl als ModalScreen
    selftest.py        Headless-Selbsttest der .pyz (versteckte Option --selftest)
vendor/
  requirements.in      direkte Laufzeit-Abhängigkeiten, Lizenz- und Ignore-Direktiven
  wheels/              gepinnte Wheels (py3-none-any) aus PyPI
  vendor.lock          Name, Version, sha256, Lizenz, URL je Wheel (JSON)
tools/
  vendor.py            lock / verify / sync der gebündelten Wheels
  build_pyz.py         Bündeln: Code + Ressourcen + Wheels + Lizenzen → dbc2h.pyz
  pyz_main.py          Starter an der Wurzel der .pyz (alte Syntax)
  dev_env.py           venv offline aus vendor/wheels + requirements-dev.txt
tests/
requirements-dev.txt   Nur für Entwicklung/CI, wird nicht ausgeliefert
```

**Schichtregeln:**

- `core/` importiert nichts außerhalb der Standardbibliothek und weiß nichts von der TUI. Das hält den Kern einfach testbar, und `gen`/`check` laufen ohne Textual.
- Die TUI ändert das Modell ausschließlich über `core/edit.py`.
- **Kein Dateizugriff relativ zu `__file__`.** Mitgelieferte Ressourcen werden immer über `importlib.resources` gelesen, sonst funktionieren sie in der `.pyz` nicht.

### 2.1 TUI-Basis: Textual

**Warum Textual:** Textual (MIT) ist reines Python, unterstützt Windows, macOS und Linux offiziell und bringt alles mit, was der Editor braucht: Screens, `DataTable`, `Tree`, `Input` mit Validierung, `Select`, `RadioSet`, `Checkbox`, modale Dialoge, `Footer` mit Tastenanzeige, Befehlspalette, Maus, CSS-Themes und Syntax-Highlighting (über Rich/Pygments). Außerdem kann man die App ohne Terminal testen (`App.run_test()`). Textual stellt das Terminal auch bei Exceptions und Strg+C wieder her.

**Machbarkeit geprüft:** Textual 8.2.8 läuft samt Abhängigkeiten aus einer komprimierten `.pyz` (ca. 2,5 MB) und lässt sich dort ohne Terminal steuern.

**Stylesheet aus der `.pyz` laden:** Textual liest `CSS_PATH`-Dateien mit einfachem `open()`. Innerhalb einer Zip-Datei schlägt das fehl. Das App-Stylesheet wird deshalb über `importlib.resources` gelesen und als `CSS` übergeben:

```python
from importlib.resources import files

class Dbc2hApp(App):
    CSS = files("dbc2h.tui").joinpath("dbc2h.tcss").read_text(encoding="utf-8")
```

Dasselbe gilt für Screens mit eigenem Stylesheet. Beim Arbeiten im Quellbaum darf zusätzlich `CSS_PATH` gesetzt sein, damit Textuals Live-Reload funktioniert.

**Was selbst gebaut wird:**

- `bitgrid.py`: eigenes Widget (Rendering mit Rich-`Text`, Tasten- und Maus-Events)
- Dialog-Flows, Formulare, Menüstruktur
- Theme im menuconfig-Stil
- Wiederherstellung und Schutz vor Datenverlust (siehe 2.3)

**Grenzen und Vorgaben:**

- Mindestgröße 80×24, darunter erscheint ein Hinweis statt eines kaputten Layouts.
- Empfohlene Terminals: Windows Terminal (Standard ab Windows 11), macOS Terminal/iTerm2, gängige Linux-Terminals, tmux und SSH. Die alte Windows-Konsole (conhost) wird getestet, darf aber eingeschränkt aussehen.
- `NO_COLOR` wird respektiert.

### 2.2 Vendoring und Build

- **`vendor.lock`** enthält pro Paket Name, exakte Version, sha256 und SPDX-Lizenz. Heute bedeutet das Textual 8.2.x mit Rich, Pygments (BSD-2), markdown-it-py, mdurl, mdit-py-plugins, linkify-it-py, platformdirs (alle MIT) und typing_extensions (PSF).
- **`tools/vendor.py`** lädt die Wheels und prüft dabei:
  - die sha256-Hashes,
  - dass nur `py3-none-any`-Wheels verwendet werden (keine `.so`/`.pyd`, sonst ist die `.pyz` nicht plattformunabhängig),
  - eine **Lizenz-Whitelist** (MIT, BSD-2/3-Clause, PSF-2.0, Apache-2.0, ISC). Jede andere Lizenz lässt den Build scheitern.
- **`tools/build_pyz.py`** baut die `.pyz`:
  - entpackt die Wheels **an die Wurzel des Archivs**, samt `dist-info`-Verzeichnissen,
  - kopiert `dbc2h/` **einschließlich aller Nicht-Python-Dateien** (`*.tcss`),
  - schreibt `dbc2h/_bundle.json` (Namen, Versionen, Lizenzen für `--version`),
  - erzeugt **`THIRD_PARTY_LICENSES.txt`** aus den Lizenzdateien der Wheels, wie es MIT/BSD verlangen,
  - schreibt das Zip selbst (Deflate, Shebang `/usr/bin/env python3`), mit sortierten Einträgen, festen Zeitstempeln und festen Rechten.
  - Der Build ist reproduzierbar. In M0 gemessen: macOS (Python 3.15) und Windows (Python 3.13) erzeugen eine **bitgleiche** Datei. Weil das bei anderen zlib-Versionen nicht garantiert ist, gibt der Build zusätzlich einen **Inhalts-Digest** über die unkomprimierten Einträge aus. Die CI vergleicht ihn über alle Plattformen.
- **Warum an der Wurzel und nicht in `_vendor/`:** `importlib.metadata` durchsucht keine Unterverzeichnisse einer Zip-Datei (in M0 nachgewiesen), und Textual ermittelt `__version__` darüber. `python dbc2h.pyz` setzt das Archiv an die erste Stelle von `sys.path`, die gebündelten Versionen haben also trotzdem Vorrang vor installierten.
- **Startzeit:** Textual wird nur für die TUI importiert. `gen`/`check` laden ausschließlich `core/`. Aus einer Zip-Datei wird bei jedem Start neu kompiliert, weil kein Bytecode-Cache geschrieben wird. Gemessen in M0 (Median aus 5 Starts, leere venv):

  | | `--version` | TUI bereit (Selbsttest) | davon Import Textual |
  |---|---|---|---|
  | macOS arm64, Python 3.15 | 36 ms | 357 ms | 211 ms |
  | Windows x86-64, Python 3.13 | 95 ms | 998 ms | 730 ms |

  Für ein interaktives Werkzeug ist das akzeptabel. Falls die Startzeit mit wachsendem Code merklich steigt, wäre eine Option, vorkompilierten Bytecode für eine Python-Version ins Archiv zu legen.
- **Versionspflege:** Textual veröffentlicht häufig neue Major-Versionen. Updates erfolgen bewusst: `vendor.lock` anheben, Snapshot-Tests laufen lassen, Abweichungen prüfen.

**Entwicklungsabhängigkeiten** (`requirements-dev.txt`, gepinnt, nur Entwicklung und CI, **nicht** in der `.pyz`):

| Paket | Lizenz | Zweck |
|---|---|---|
| pytest | MIT | Testrunner |
| pytest-textual-snapshot | MIT | Bildschirm-Snapshots der TUI (ab M3) |
| cantools | MIT | Unabhängige Gegenprobe für Parser und Encode/Decode, Rohwerte und Frames (ab M1) |
| hypothesis | MPL-2.0 | Property-Tests (optional, ab M2) |

### 2.3 Datenmodell und Datei-Handling

**Zahlen:** Factor und Offset werden als `fractions.Fraction` plus Originalstring gespeichert. `0.1` ist damit exakt `1/10`, und der Writer gibt die Zahl unverändert aus.

**Statement-Klassen:** Jede Art von DBC-Statement gehört zu genau einer Klasse. Die Zuordnung ist Teil der Parser-Spezifikation.

| Klasse | Behandlung | Beispiele |
|---|---|---|
| Modelliert und editierbar | Vollständige Modellobjekte | `BU_`, `BO_`, `SG_`, `CM_`, `VAL_`, `VAL_TABLE_`, `BA_DEF_`, `BA_DEF_DEF_`, `BA_`, `SIG_VALTYPE_` |
| Mit Verweisen | Originaltext bleibt erhalten, die Positionen aller Verweise (Namen, IDs) sind bekannt | `SIG_GROUP_`, `BO_TX_BU_`, `SG_MUL_VAL_`, `BA_REL_`, `BA_DEF_REL_`, `EV_`-Familie |
| Undurchsichtig | Wörtlich erhalten | Alles, was der Parser nicht kennt |

- Jeder Befehl in `core/edit.py`, der umbenennt, löscht oder eine ID ändert, zieht **alle Verweise der ersten beiden Klassen im selben Undo-Schritt** mit.
- In undurchsichtigen Statements wird nach dem alten Bezeichner als Token gesucht. Kommt er vor, gibt es eine Warnung.
- Lint-Fehler `E108 dangling-reference` für Verweise ins Leere.

**Byte-identischer Round-Trip:**

- Jedes Top-Level-Statement behält seinen Originaltext und ein Dirty-Flag.
- Der Writer gibt für unveränderte Statements den Originaltext aus, für geänderte oder neue eine kanonische Form.
- Neue Statements kommen hinter das letzte Statement derselben Art.
- Daraus folgt: Laden und Speichern ohne Änderung ist **bytegleich**. Nach einer Änderung betrifft der Diff nur die geänderten Statements.

**Encoding:**

- Dekodiert wird in drei Stufen: UTF-8 (mit BOM-Erkennung), dann cp1252, dann latin-1. latin-1 akzeptiert jedes Byte, der Round-Trip bleibt also in jedem Fall verlustfrei, auch bei GBK- oder Shift-JIS-Dateien.
- Codec, BOM und Zeilenende (`newline=''`) werden gemerkt und beim Schreiben wiederverwendet.
- Eingabefelder lehnen Zeichen ab, die der Codec der Datei nicht kodieren kann. Die Umwandlung einer Datei nach UTF-8 ist eine eigene, explizite Aktion.
- Neue Dateien werden standardmäßig als **cp1252** angelegt. Ob CANdb++ UTF-8 sauber verarbeitet, wird in M1 geprüft. Erst dann wird über einen anderen Standard entschieden.

**Speichern:**

- Pfad über Symlinks auflösen, temporäre Datei im Zielverzeichnis schreiben, Dateirechte übernehmen, dann `os.replace`. Beim ersten Speichern jeder Sitzung wird eine `.bak` angelegt.
- **Änderungen auf der Platte erkennen:** Beim Laden und Speichern werden Größe, Änderungszeit und Hash festgehalten. Weicht die Datei vor dem Speichern ab, fragt die TUI: überschreiben, neu laden oder unter anderem Namen speichern.
- **Windows:** `os.replace` scheitert mit `PermissionError`, solange ein anderes Programm die Datei offen hält. Dann bleibt die temporäre Datei erhalten, und die TUI zeigt Pfad und Ursache an.

**Schutz vor Datenverlust:**

- Nach jedem Änderungsbefehl wird der Stand nach ein bis zwei Sekunden atomar nach `<datei>.dbc2h-recovery` geschrieben. Beim Speichern und beim sauberen Beenden wird die Datei gelöscht.
- `SIGHUP` und `SIGTERM` (wo vorhanden) und unbehandelte Exceptions schreiben die Wiederherstellungsdatei sofort, Exceptions zusätzlich ein Crash-Log.
- Findet die TUI beim Öffnen eine Wiederherstellungsdatei, bietet sie die Wiederherstellung an.

### 2.4 Einstellungen in der DBC selbst

Alle dbc2h-Einstellungen liegen als **eigene DBC-Attribute** in der DBC. Die Datei bleibt so in sich vollständig. (`tomllib` könnte nur lesen, die TUI muss aber auch schreiben.)

In DBC wird eine Attribut-Definition über ihren Namen identifiziert. Deshalb hat **jedes Attribut genau einen Objekttyp**, nach Vectors Konvention `GenMsg…`/`GenSig…`. Alle Namen sind höchstens 32 Zeichen lang.

| Objekt | Attribut | Werte | Default |
|---|---|---|---|
| Netzwerk | `dbc2h_Prefix` | String | aus Dateiname |
| Netzwerk | `dbc2h_FloatType` | `float` / `double` | `float` |
| Netzwerk | `dbc2h_StrictQuantization` | 0/1 | 0 |
| Netzwerk | `dbc2h_FloatEpsLsb` | Float | 0.0625 (1/16 LSB) |
| Netzwerk | `dbc2h_OutputMode` | `header_only` / `split` | `header_only` |
| Netzwerk | `dbc2h_OutputDir` | Pfad relativ zur DBC, immer mit `/` | `.` |
| Netzwerk | `dbc2h_Lint` | String, z. B. `E103=warning,W405=off` | leer |
| Nachricht | `dbc2h_MsgIgnore` | 0/1 | 0 |
| Signal | `dbc2h_SigFloatType` | `default` / `float` / `double` | `default` |
| Signal | `dbc2h_SigIgnore` | 0/1 | 0 |
| Signal | `dbc2h_SigEnumComplete` | 0/1 | 0 |

- `BA_DEF_` und `BA_DEF_DEF_` werden erst geschrieben, wenn zum ersten Mal ein Wert vom Default abweicht. `BA_` wird nur für abweichende Werte geschrieben. Eine fremde DBC, die nur geöffnet und zum Generieren genutzt wird, bleibt dadurch unverändert.
- In M1 wird eine von Hand geschriebene Datei mit allen `dbc2h_*`-Attributen einmal in CANdb++ geladen und das Ergebnis festgehalten.

TUI-Vorlieben (Theme, zuletzt geöffnete Dateien) gehören dagegen dem Benutzer und nicht der DBC. Sie liegen im Konfigurationsverzeichnis des Benutzers, das `platformdirs` liefert (eine Textual-Abhängigkeit).

---

## 3. Numerik

### 3.1 Begriffe

| Begriff | Bedeutung |
|---|---|
| **Leitungsbereich** | Alle Bitmuster der Signalbreite: unsigned `[0, 2ⁿ−1]`, signed `[−2ⁿ⁻¹, 2ⁿ⁻¹−1]` |
| **Gültigkeitsbereich** | `[RAW_MIN, RAW_MAX]`: der Teil des Leitungsbereichs, der in [Min, Max] der DBC liegt |
| **LSB** | Betrag des Faktors |

`RAW_MIN` und `RAW_MAX` werden exakt aus den Fractions berechnet: `ceil((Min − Offset) / Faktor)` und `floor((Max − Offset) / Faktor)`, geschnitten mit dem Leitungsbereich. Bei negativem Faktor tauschen Min und Max die Rollen. Min/Max = 0/0 bedeutet nach DBC-Konvention „voller Leitungsbereich“.

`phys = raw · factor + offset`, `raw = round((phys − offset) / factor)`

### 3.2 Typwahl (kein automatisches Hochstufen)

1. **Ganzzahlpfad:** Faktor und Offset sind ganzzahlig.
   - Der Feldtyp ist der kleinste `intN_t`/`uintN_t`, der den physikalischen Wertebereich über den **Leitungsbereich** fasst. Berechnet wird mit Python-Ganzzahlen, bei negativem Faktor sind Minimum und Maximum vertauscht.
   - Passt der Bereich weder in `int64_t` noch in `uint64_t`, ist das ein Fehler (`E204`).
   - `pack` liefert **immer** `ERR_QUANTIZATION`, wenn `(phys − offset)` nicht durch den Faktor teilbar ist. Eine Ganzzahl hat keine Toleranz.
   - Die Prüfung beweist, dass die generierte Arithmetik in keinem Zwischenschritt überläuft.
2. **Gleitkommapfad:** Der gewählte Typ ist der Signal-Override (`dbc2h_SigFloatType`), sonst der Netzwerk-Default (`dbc2h_FloatType`), sonst `float`.
3. Signale mit `SIG_VALTYPE_` (IEEE float/double auf dem Bus) werden 1:1 zu `float` bzw. `double`.
4. Der gewählte Typ wird **geprüft**. Fällt die Prüfung durch, ist das ein **Fehler**. Das Tool ändert den Typ nie selbst.

### 3.3 Formelbeschreibung und Emulation

**Eine Beschreibung, zwei Backends:** `core/formula.py` beschreibt jede Umrechnung als kurze Liste typisierter Operationen: Ganzzahl → Gleitkomma, Multiplikation, Addition, Subtraktion, Division, Rundung, Gleitkomma → Ganzzahl, Vergleich. Der **Python-Auswerter** rechnet sie für die Prüfung, der **C-Emitter** erzeugt daraus den Code. Prüfer und Generator können so nicht auseinanderlaufen.

**Emulation von binary32:**

- Einzelne Operationen: Python rechnet in binary64 und rundet danach auf binary32. Weil 53 ≥ 2·24 + 2 gilt, ist die doppelte Rundung für + − × ÷ harmlos (Figueroa).
- Für Massenberechnungen wird `array.array('f', …)` statt `struct.pack` genutzt. Das ist schneller und liefert bei Überlauf `inf`, während `struct.pack` einen `OverflowError` wirft.
- Für die exakte Fehlermessung wird mit Ganzzahlen gerechnet (`float.as_integer_ratio`), nicht mit `Fraction`-Objekten pro Wert.
- binary64 rechnet Python direkt.

**FMA-Kontraktion:** C-Compiler dürfen `raw * factor + offset` zu einer fused multiply-add zusammenziehen, die nur einmal rundet. Clang tut das standardmäßig, GCC außerhalb des ISO-Modus (`-std=gnu11`). AArch64 und Cortex-M4F/M7 haben immer FMA. Gemessen: Bei 16 Bit, Faktor 0.01, Offset −327.68 unterscheiden sich 33447 von 65536 Ergebnissen. Deshalb:

- Die Formelbeschreibung markiert jedes Paar aus Multiplikation und Addition.
- Der Prüfer rechnet jedes Paar **in beiden Varianten** (getrennt gerundet und kontrahiert) und stuft nach dem schlechteren Ergebnis ein.
- Kontrahiert heißt: exaktes Ergebnis, einmal gerundet. Für binary32 geschieht das mit einer exakten Rundungsfunktion aus Ganzzahlarithmetik, **nicht** über den Umweg `Fraction → double → float`, weil der bei Summen doppelt rundet. Für binary64 gibt es `math.fma` (seit Python 3.13).
- Ist der Faktor eine Zweierpotenz, ist das Produkt exakt und beide Varianten sind gleich. Der zweite Durchlauf entfällt.
- Pragmas werden nicht verwendet: GCC ignoriert `#pragma STDC FP_CONTRACT` mit einer Warnung, die unter `-Wall -Werror` den Build bricht.

### 3.4 Einstufung

Für den **Leitungsbereich 1.0** gilt eine Regel ohne versteckte Bedingungen: Die Einstufung deckt **jedes Bitmuster** ab. Ein Signal ist gültig (`EXACT` oder `ROUNDTRIP`), wenn beide Bedingungen gelten:

1. Für jeden Rohwert im **Leitungsbereich** ist der Decode-Fehler ≤ ε·LSB.
2. Für jeden Rohwert im **Gültigkeitsbereich** gilt: `pack(unpack(raw))` liefert `OK` und denselben Rohwert.

| Stufe | Bedeutung | Ergebnis |
|---|---|---|
| `EXACT` | Jeder Rohwert ergibt den mathematisch exakten Wert | OK |
| `ROUNDTRIP` | Bedingungen 1 und 2 erfüllt, aber nicht exakt | OK, mit Fehlerangabe |
| `LOSSY` | Bedingung 1 oder 2 verletzt, mit konkretem Gegenbeispiel | **Fehler** (`E201`) |
| `UNREPRESENTABLE` | Leitungsbereich breiter als die Mantisse (> 24 Bit bei float, > 53 Bit bei double) | **Fehler** (`E202`) |
| `UNPROVEN` | Die analytische Schranke kann die Bedingungen nicht beweisen | **Fehler** (`E203`) |

- **ε** ist relativ zum LSB, Standard **1/16 LSB**, einstellbar über `dbc2h_FloatEpsLsb`. Für den Round-Trip würde theoretisch < 0.5 LSB reichen, der Rest ist Reserve für Arithmetik beim Anwender.
- **float:** Jedes Signal bis 24 Bit wird **vollständig durchgerechnet**, in beiden FMA-Varianten. Eine analytische Schranke ist nicht nötig, alles Breitere ist ohnehin `UNREPRESENTABLE`. Gemessener Aufwand mit Ganzzahlarithmetik: ca. 2 s für 2²², ca. 8 s für 2²⁴.
- **double:** Hier wird nur eine analytische Schranke verwendet. Sie wird exakt **pro Binade** des Rohwerts ausgewertet, damit sie eng ist, und deckt Decode und Encode ab. Kann sie nichts beweisen, lautet das Ergebnis `UNPROVEN`. Zufallsstichproben dienen nur dazu, für die Fehlermeldung ein konkretes Gegenbeispiel zu finden. Sie beweisen nie etwas.
- **Cache:** Ergebnisse werden nach (Breite, Vorzeichen, Faktor, Offset, Min, Max, Typ, ε) gespeichert, weil DBC-Dateien Signalformen oft wiederholen.
- **Später (optional):** Einstufung nur über den Gültigkeitsbereich, für Ziele mit teurem double. Dann muss die Bereichsprüfung in `unpack` verpflichtend sein, und Werte außerhalb des Gültigkeitsbereichs sind ausdrücklich nur „best effort“.

Beispiel (TUI-Prüfergebnisse bzw. `check`):

```
VehicleStatus.speed    16u  LSB 0.25           float   EXACT
Engine.temp             8s  LSB 0.1  off -40   float   ROUNDTRIP  max. Fehler 1.5e-5 LSB
Trip.distance          20u  LSB 0.001          float   LOSSY      0.063 LSB bei raw=683677 (> 1/16)
                                                       → double wählen, LSB vergrößern oder Länge reduzieren
Odo.distance           32u  LSB 0.001          float   UNREPRESENTABLE  32 Bit > 24 Bit Mantisse
                                                       → double wählen oder Länge reduzieren
```

(Das gleiche Signal wie `Trip.distance` mit 19 Bit ist gültig: max. Fehler 0.055 LSB.)

### 3.5 Encode (Struct → Puffer): Fehler statt Clamp

Die Bereichsprüfung findet auf dem **ganzzahligen Rohwert** statt, nicht auf dem Gleitkommawert. Ein Vergleich gegen das float-Literal von Max würde Werte ablehnen, die `unpack` selbst erzeugt hat: `decode(raw_max)` kann eine ulp über `(float)Max` liegen, z. B. bei 10 Bit, Faktor 0.1, Offset −40 (62.30000305 gegen 62.29999924).

Prüfreihenfolge pro Gleitkomma-Signal:

1. `!(v >= -FLT_MAX && v <= FLT_MAX)` → `ERR_NOT_FINITE` (erkennt NaN und ±Inf)
2. `!(v >= GUARD_LO && v <= GUARD_HI)` → `ERR_RANGE`. Die Guards sind exakt darstellbare Werte, die den gültigen Bereich mit Abstand umschließen. Ihr einziger Zweck: Die folgende Umwandlung in eine Ganzzahl darf kein undefiniertes Verhalten auslösen. Der Prüfer beweist, dass der gerundete Wert an beiden Guards in den Ganzzahltyp passt.
3. `r = round((v − OFFSET) / FACTOR)`, bei Zweierpotenz-Faktor als Multiplikation mit dem exakten Kehrwert
4. `r < RAW_MIN || r > RAW_MAX` → `ERR_RANGE`
5. Optional (Strict-Quantisierung): `|v − decode(r)| > ε·LSB` → `ERR_QUANTIZATION`. Dann sendet z. B. 12.3 bei LSB 0.25 nicht still 12.25.
6. Bei `dbc2h_SigEnumComplete = 1`: Rohwert nicht in der Wertetabelle → `ERR_ENUM`

**Folge, die dokumentiert wird:** Ohne Strict-Quantisierung wird ein Wert knapp über Max akzeptiert, wenn er auf `RAW_MAX` rundet. Mit Strict-Quantisierung wird er abgelehnt.

**Atomarer Vertrag:**

- `pack()` prüft zuerst **alle** Signale. Der Status ist die ODER-Verknüpfung aller Fehlerflags.
- Nur bei `OK` wird geschrieben, sonst bleibt der **Puffer unverändert**.
- Geschrieben werden **alle DLC-Bytes**. Bits, die kein aktives Signal belegt, sind 0. Der alte Inhalt des Puffers gelangt nie in den Frame.

**Decode:** prüft die Pufferlänge. Die optionale Bereichsprüfung nutzt dieselben ganzzahligen Grenzen `RAW_MIN`/`RAW_MAX`. Das Struct wird auch bei `ERR_RANGE` gefüllt, damit Diagnose möglich bleibt. Für jedes Bitmuster ist das Ergebnis definiert (3.4, Bedingung 1).

---

## 4. Generierter C11-Code

Generierter Code und seine Kommentare sind durchgehend **englisch**, passend zu den Bezeichnern.

```c
/* Generated by dbc2h 1.0 from powertrain.dbc
 * (content sha256 3f9a…, line endings normalised to LF). DO NOT EDIT. */
#ifndef PT_CAN_H
#define PT_CAN_H
#include <stdint.h>
#include <stddef.h>
#include <float.h>
#include <limits.h>

#ifdef __cplusplus
#  define PT_STATIC_ASSERT(cond, msg) static_assert(cond, msg)
extern "C" {
#else
#  define PT_STATIC_ASSERT(cond, msg) _Static_assert(cond, msg)
#endif

PT_STATIC_ASSERT(CHAR_BIT == 8, "dbc2h: 8-bit bytes required");
PT_STATIC_ASSERT(FLT_RADIX == 2 && FLT_MANT_DIG == 24, "dbc2h: IEEE-754 binary32 float required");
PT_STATIC_ASSERT(DBL_MANT_DIG == 53, "dbc2h: IEEE-754 binary64 double required"); /* only if double is used */
#if defined(__FAST_MATH__) || defined(_M_FP_FAST)
#  error "dbc2h: fast-math invalidates the verified conversion guarantees"
#endif
#if defined(FLT_EVAL_METHOD) && FLT_EVAL_METHOD != 0
#  error "dbc2h: excess-precision evaluation (e.g. x87) differs from the verified model"
#endif

typedef uint32_t pt_status_t;
#define PT_OK                0u
#define PT_ERR_LENGTH        (1u << 0)
#define PT_ERR_NOT_FINITE    (1u << 1)
#define PT_ERR_RANGE         (1u << 2)
#define PT_ERR_QUANTIZATION  (1u << 3)
#define PT_ERR_ENUM          (1u << 4)

#define PT_VEHICLE_STATUS_ID        0x123u
#define PT_VEHICLE_STATUS_EXTENDED  0
#define PT_VEHICLE_STATUS_DLC       8u
#define PT_VEHICLE_STATUS_CYCLE_MS  20u

#define PT_VEHICLE_STATUS_SPEED_RAW_MIN  0
#define PT_VEHICLE_STATUS_SPEED_RAW_MAX  65535

/* Value table of VehicleStatus.gear (unscaled integer signal) */
#define PT_VEHICLE_STATUS_GEAR_PARK     0u
#define PT_VEHICLE_STATUS_GEAR_REVERSE  1u
#define PT_VEHICLE_STATUS_GEAR_NEUTRAL  2u
#define PT_VEHICLE_STATUS_GEAR_DRIVE    3u
static inline const char *pt_vehicle_status_gear_to_string(uint8_t v)
{
    switch (v) {
    case 0u: return "Park";
    case 1u: return "Reverse";
    case 2u: return "Neutral";
    case 3u: return "Drive";
    default: return NULL;
    }
}

/** VehicleStatus, sender ECU_Engine */
typedef struct {
    float    speed;   /**< [km/h] 0 .. 16383.75, factor 0.25, 16 bit unsigned, Intel @0, EXACT */
    double   odo;     /**< [km] factor 0.001, 32 bit unsigned, double (selected) */
    uint8_t  gear;    /**< see PT_VEHICLE_STATUS_GEAR_* */
    int16_t  torque;  /**< [Nm] -500 .. 1547, 11 bit, offset -500, integer path */
} pt_vehicle_status_t;

static inline pt_status_t pt_vehicle_status_pack(uint8_t *buf, size_t len,
                                                 const pt_vehicle_status_t *msg)
{
    pt_status_t st = PT_OK;
    int32_t speed_raw = 0;
    /* 1. Validate every signal. Nothing is written yet. */
    if (len < PT_VEHICLE_STATUS_DLC) st |= PT_ERR_LENGTH;
    if (!(msg->speed >= -FLT_MAX && msg->speed <= FLT_MAX)) {
        st |= PT_ERR_NOT_FINITE;
    } else if (!(msg->speed >= -1.0f && msg->speed <= 16384.0f)) {  /* guard for the cast */
        st |= PT_ERR_RANGE;
    } else {
        speed_raw = pt_round_f32_i32(msg->speed * 4.0f);          /* factor 0.25: exact inverse */
        if (speed_raw < PT_VEHICLE_STATUS_SPEED_RAW_MIN ||
            speed_raw > PT_VEHICLE_STATUS_SPEED_RAW_MAX) st |= PT_ERR_RANGE;
    }
    /* ... other signals ... */
    if (st != PT_OK) return st;
    /* 2. Write the whole frame. Bits not covered by a signal are 0. */
    buf[0] = (uint8_t)((uint32_t)speed_raw & 0xFFu);
    buf[1] = (uint8_t)((uint32_t)speed_raw >> 8);
    /* ... */
    return PT_OK;
}

static inline pt_status_t pt_vehicle_status_unpack(pt_vehicle_status_t *msg,
                                                   const uint8_t *buf, size_t len);

#ifndef __cplusplus
#define pt_pack(buf, len, msg) _Generic((msg),                        \
        pt_vehicle_status_t *:       pt_vehicle_status_pack,          \
        const pt_vehicle_status_t *: pt_vehicle_status_pack           \
        /* ... */)(buf, len, msg)
#define pt_unpack(msg, buf, len) _Generic((msg),                      \
        pt_vehicle_status_t *:       pt_vehicle_status_unpack         \
        /* ... */)(msg, buf, len)
#endif

/* Message table and dispatch: declared always, defined once. */
typedef struct pt_message_info pt_message_info_t;
extern const pt_message_info_t pt_messages[];
pt_status_t pt_unpack_by_id(uint32_t id, void *msg, const uint8_t *buf, size_t len);
#ifdef PT_IMPLEMENTATION
/* ... definitions ... */
#endif

#ifdef __cplusplus
}
#endif
#endif
```

**Regeln für den Generator:**

- **Keine Bitfelder und keine Unions über dem Puffer**, weil deren Layout implementierungsabhängig ist. Stattdessen werden Byte-Shifts pro Signal ausgerollt, unabhängig von der Endianness des Hosts.
- **Struct-Felder** haben immer den Typ aus 3.2: eine Ganzzahl fester Breite oder den gewählten Gleitkommatyp. **Keine Enum-Typen**, denn:
  - mit `-fshort-enums` (Standard bei Bare-Metal-ARM-EABI) würden Werte außerhalb der Tabelle abgeschnitten,
  - Enum-Konstanten müssen in `int` passen,
  - Wertetabellen sind oft unvollständig.
- **Wertetabellen** erzeugen `#define`-Konstanten und eine `static inline`-Funktion `*_to_string()`, die bei unbekannten Werten `NULL` liefert. In 1.0 gilt das nur für **unskalierte Ganzzahlsignale** (Faktor 1, Offset 0). Bei skalierten Signalen stehen die Einträge im Doxygen-Kommentar, dazu kommt ein Info-Befund (`I501`).
- **Namen aus Wertbeschreibungen** sind Freitext und kollidieren nach der Umwandlung oft („Not available“, „not_available“). Solche Kollisionen werden deterministisch durch Anhängen des Werts aufgelöst (`..._NOT_AVAILABLE_14`), mit Info-Befund `I502`. Kollisionen bei Nachrichten- und Signalnamen bleiben ein harter Fehler (`E301`).
- **Strings aus der DBC** werden sicher in C übertragen: In String-Literalen nur druckbares ASCII, alles andere als Oktal-Escape. In Kommentaren wird `*/` entschärft.
- **Header-only-Modus:** Alle Funktionen sind `static inline`. Die Nachrichtentabelle und `pt_unpack_by_id()` stehen hinter `#ifdef PT_IMPLEMENTATION`. Genau eine Übersetzungseinheit definiert das Makro, damit die Tabelle nicht in jeder Einheit doppelt liegt. Im split-Modus stehen diese Teile in der `.c`.
- **C++-Einbindung** wird unterstützt (gemischte C/C++-Projekte sind im Zielbereich üblich): `PT_STATIC_ASSERT` statt `_Static_assert`, `_Generic` nur in C, keine anonymen Structs oder Unions.
- **Multiplexing (1.0 nur einfaches):** Selektorfeld plus **benannte** Union mit einem benannten Struct pro Mux-Wert, z. B. `msg.mux.m3.signal`. Das ist in C und C++ gültig. `pack` schreibt nur die aktiven Signale, alle übrigen Bits sind 0. Nachrichten mit erweitertem Multiplexing (`SG_MUL_VAL_`, Wertebereiche, verschachtelte Selektoren) führen zu Fehler `E110`. Sie bleiben im Round-Trip unverändert und lassen sich mit `dbc2h_MsgIgnore` ausnehmen.
- **CAN FD:** DLC bis 64 Byte, Längenprüfung gegen die gültigen FD-Längen.
- **MSVC-kompatibel** (`/std:c11`): keine VLAs, kein `[static N]`.
- **Rundung** ohne libm-Abhängigkeit (eigene `round`-Hilfsfunktion mit definiertem Verhalten bei .5). Ganzzahl-Zwischenwerte in `int32_t`, wenn die Guards das erlauben, sonst `int64_t`.
- **Erkennbare unsichere Compiler-Modi** brechen den Build: `__FAST_MATH__` (GCC/Clang), `_M_FP_FAST` (MSVC), `FLT_EVAL_METHOD != 0`. Nicht erkennbar sind z. B. `-funsafe-math-optimizations` ohne `-ffast-math`. Deshalb steht in der Dokumentation: **`test_<prefix>.c` muss mit dem Produktions-Compiler und den Produktions-Flags laufen.**
- **Deterministische Ausgabe:** kein Zeitstempel. Der Hash im Header wird über den Dateiinhalt mit auf LF normalisierten Zeilenenden berechnet, sonst ergäbe Gits `core.autocrlf` unter Windows einen anderen Hash. Erzeugte Dateien werden **immer mit LF** geschrieben.
- **`gen --check`** erzeugt im Speicher neu und vergleicht bytegenau mit den vorhandenen Dateien. Den Hash braucht es dafür nicht. Eine neue dbc2h-Version macht alle Header veraltet, weil die Version in der ersten Zeile steht. Das ist gewollt.
- Doxygen-Kommentare aus CM_, Einheit, Bereich und Numerikstatus.
- Compiler-sauber mit `-Wall -Wextra -Wpedantic -Wconversion -Wdouble-promotion -Werror` (gcc, clang) und `/W4 /WX` (MSVC), als C11 und als C++17.

---

## 5. Prüfregeln (Lint)

Jede Regel hat eine **stabile ID**, die bei jedem Befund ausgegeben wird. Fremde DBC-Dateien (z. B. von OEMs) verletzen manche Regeln regelmäßig, und der Benutzer darf sie oft nicht ändern. Deshalb lässt sich die Severity einzelner Regeln über das Netzwerk-Attribut `dbc2h_Lint` herabstufen (z. B. `E103=warning`). **Regeln, die die Korrektheit des erzeugten Codes schützen, lassen sich nicht herabstufen.** Daran kommt man nur vorbei, indem man das Element mit `dbc2h_MsgIgnore` bzw. `dbc2h_SigIgnore` ausnimmt.

| ID | Regel | Severity | Herabstufbar |
|---|---|---|---|
| E101 | Signale überlappen sich (mux-bewusst: nur wenn beide gleichzeitig aktiv sein können) | Fehler | nein |
| E102 | Signal reicht über das DLC hinaus | Fehler | nein |
| E103 | Min/Max außerhalb des Leitungsbereichs. Herabgestuft: Gültigkeitsbereich = Schnitt aus Min/Max und Leitungsbereich | Fehler | ja |
| E104 | Min > Max | Fehler | ja (dann gilt der volle Leitungsbereich) |
| E105 | Faktor 0 | Fehler | nein |
| E106 | Doppelte Nachrichten-ID | Fehler | nein |
| E107 | Doppelter Name (Nachricht, Signal in einer Nachricht, Knoten) | Fehler | nein |
| E108 | Verweis ins Leere (dangling reference) | Fehler | nein |
| E109 | Multiplexing ohne Selektor oder mit doppeltem Mux-Wert | Fehler | nein |
| E110 | Erweitertes Multiplexing, vom Generator nicht unterstützt | Fehler | nein |
| E201 | Numerik `LOSSY` | Fehler | nein |
| E202 | Numerik `UNREPRESENTABLE` | Fehler | nein |
| E203 | Numerik `UNPROVEN` | Fehler | nein |
| E204 | Ganzzahlpfad: Bereich passt nicht in 64 Bit | Fehler | nein |
| E301 | Kollision von C-Bezeichnern bei Nachrichten oder Signalen | Fehler | nein |
| W401 | Signal ohne Empfänger | Warnung | ja |
| W402 | Nachricht ohne Sender (`Vector__XXX`) | Warnung | ja |
| W403 | Wertetabelle mit Werten außerhalb des Leitungsbereichs | Warnung | ja |
| W404 | Ungenutzte Wertetabelle | Warnung | ja |
| W405 | Fehlende Zykluszeit bei zyklischer Nachricht | Warnung | ja |
| I501 | Wertetabelle an skaliertem Signal, keine Konstanten erzeugt | Info | ja |
| I502 | Namenskollision bei Wertbeschreibungen, durch Wert-Suffix aufgelöst | Info | ja |
| I503 | Namenskonvention verletzt (konfigurierbar) | Info | ja |

Jeder Befund verweist auf ein Element. In der TUI springt man mit `<Enter>` direkt dorthin.

---

## 6. Features

**Im Kernumfang (1.0):**

1. Komplette DBC-Verwaltung in der TUI: Netzwerk, Knoten, Nachrichten, Signale, Wertetabellen, Attribute, Kommentare. Umbenennen und Löschen ziehen alle Verweise mit.
2. **Grafischer Bit-Layout-Editor** mit Verschieben per Pfeiltasten oder Maus, Intel/Motorola-Darstellung, Markierung von Überlappungen und Auswahl des Mux-Werts
3. **Auto-Placement:** Neues Signal mit Länge X wird automatisch an die erste freie Position gesetzt
4. **Live-Numerikprüfung** im Signal-Formular mit konkreten Abhilfen
5. **Frame-Tester** in der TUI: Hex-Bytes eingeben und dekodierte Werte sehen oder umgekehrt, inklusive Fehlerstatus wie im C-Code
6. **Befehlspalette** (Strg+P): Sprung zu jeder Nachricht oder jedem Signal per Fuzzy-Suche
7. Undo/Redo, Suche, Kontexthilfe, Schutz vor ungespeicherten Änderungen, laufende Wiederherstellungsdatei, Erkennung externer Änderungen
8. Generierung aus der TUI mit **Vorschau inklusive Syntax-Highlighting**; nicht-interaktiv mit `gen` / `gen --check` / `check` für CI und Build-Systeme
9. **Generierte C-Tests:** Python erzeugt Testvektoren mit seiner Referenzimplementierung, das Tool schreibt daraus `test_<prefix>.c`. Ist ein Compiler vorhanden, wird er automatisch aufgerufen.
10. Header aus **C und C++** einbindbar

**Später (nach 1.0):**

- Erweitertes Multiplexing im Generator
- Einstufung nur über den Gültigkeitsbereich (für Ziele mit teurem double)
- Semantischer **Diff** zweier DBC-Versionen mit API-Break-Erkennung (in der TUI als Vergleichsansicht)
- Log-Decoding (candump, Vector ASC) mit Export nach CSV
- Kommunikationsmatrix als Markdown/HTML
- Helfer für Rolling Counter und CRC/E2E
- Merge mehrerer DBCs
- Raw-/No-FPU-Modus (nur Ganzzahl-Structs oder skalierte Ganzzahlen)

**Bewusst nicht vorgesehen:** Web-Editor, CLI-Befehle zum Bearbeiten, automatisches Hochstufen von float auf double, Clamping, Laufzeit-Abhängigkeiten mit nativem Code, Enum-Typen in Structs.

---

## 7. Teststrategie

- **pytest** als Testrunner (nur Entwicklung und CI)
- **Parser/Writer:**
  - Korpus echter DBCs (z. B. opendbc, MIT-Lizenz): Laden und Speichern ohne Änderung ist **bytegleich**.
  - Gezielte Grammatik-Eigenheiten: Extended IDs über Bit 31, `VECTOR__INDEPENDENT_SIG_MSG`, escapte Strings, negative Werte in VAL_, `SG_MUL_VAL_`, leeres `BS_:`, die drei Encoding-Stufen samt BOM.
  - Für **jeden Änderungsbefehl**: Ergebnis schreiben, mit dbc2h und cantools neu parsen und sicherstellen, dass kein Verweis ins Leere zeigt und der Diff nur die betroffenen Statements umfasst.
- **Gegenprobe mit cantools:** Struktur (Nachrichten, Signale, Bitpositionen) sowie **Rohwerte und Frames** werden verglichen. Physikalische Werte nicht, weil cantools in double rechnet.
- **Bitmapping:** erschöpfend für alle Startbit/Längen-Kombinationen in 8 und 64 Byte, für Intel und Motorola. Die Motorola-Sägezahnnummerierung ist die klassische Fehlerquelle.
- **Formel-Spike (M2):** Ein Testharness erzeugt aus der Formelbeschreibung ein C-Programm für ein Signal, kompiliert es mit dem Host-Compiler und vergleicht jedes Ergebnis **bitweise** mit der Emulation. Flag-Matrix: `-O0`/`-O2`, `-ffp-contract=off`/`fast`, `-std=c11`/`gnu11`, auf x86-64 und arm64 (der macOS-arm64-Runner hat FMA).
- **Numerik:** Property-Tests mit festem Seed; Grenzfälle (Zweierpotenz-Übergänge, Subnormale, Überlauf) der exakten Rundungsfunktion.
- **Codegen:**
  - Golden-Files
  - generierte C-Tests mit gcc, clang und MSVC, mit derselben Flag-Matrix wie im Formel-Spike
  - Header einmal als **C++17** mit g++, clang++ und MSVC kompilieren, gleiche Warnstufe
  - Fuzz-Round-Trip mit 10⁵ Zufallsframes pro Nachricht
  - `gen --check` auf Windows mit `core.autocrlf=true` ausgecheckt
- **TUI:**
  - Interaktionstests mit `App.run_test()`: Tastenfolgen abspielen, danach Modellzustand und Undo-Stack prüfen
  - Snapshot-Tests mit `pytest-textual-snapshot` für jeden Screen in 80×24 und 120×40
  - Wiederherstellung: Prozess hart beenden, neu starten, Angebot zur Wiederherstellung prüfen
  - manuelle Checkliste je Plattform (Windows Terminal, conhost, macOS Terminal, iTerm2, gnome-terminal, xterm, tmux, SSH)
- **Build:** Die Testsuite läuft auch gegen die **fertige `.pyz`**, nicht nur gegen den Quellbaum. Die `.pyz` wird auf allen drei Systemen in einer **sauberen Umgebung ohne installierte Pakete** gestartet (`--version`, `check`, `gen`, TUI-Rauchtest per `run_test`).
- **CI-Matrix:** Windows, macOS (arm64) und Linux (x86-64) mit Python 3.13, 3.14 und 3.15
- **Manuell, einmalig in M1:** Testdatei mit allen `dbc2h_*`-Attributen und eine UTF-8-Datei in CANdb++ laden und das Ergebnis festhalten.

---

## 8. Meilensteine

| # | Meilenstein | Inhalt | Abnahmekriterium |
|---|---|---|---|
| **M0** | Grundgerüst & Build | Paketstruktur, Versionsprüfung, `vendor.lock` + `tools/vendor.py` (Hash-, Lizenz- und Pure-Python-Prüfung), `tools/build_pyz.py` inkl. Ressourcen, `THIRD_PARTY_LICENSES.txt`, pytest, CI-Matrix | Die `.pyz` startet auf allen drei Systemen mit Python 3.13–3.15 in einer leeren Umgebung eine Textual-Hallo-Welt, **gestylt durch eine gebündelte `.tcss`**. Ein Wheel mit nicht erlaubter Lizenz lässt den Build scheitern. Kaltstartzeit ist gemessen. |
| **M1** | Parser & Writer | Datenmodell, Statement-Klassen und Verweise, Parser mit Fehlerpositionen, Writer mit Originaltext und Dirty-Flag, Encoding-Stufen, atomares Speichern mit Änderungserkennung, Attributnamen, CANdb++-Testdatei | Laden und Speichern ohne Änderung ist für den ganzen Korpus bytegleich. Die Struktur stimmt mit cantools überein. Das CANdb++-Ergebnis ist festgehalten. |
| **M2** | Numerik & Layout | **Formelbeschreibung mit zwei Backends und C-Spike**, binary32/64-Emulation mit beiden FMA-Varianten, Leitungs- und Gültigkeitsbereich, Ganzzahlpfad, Einstufung (float vollständig, double per Schranke), Cache, Bitmapping, Lint-Regeln mit IDs und Ausnahmen, `check` | Emulation und kompiliertes C stimmen für einen festen Satz von Signalen auf x86-64 und arm64 **bitweise** überein, über die ganze Flag-Matrix. Bitmapping-Tests grün. |
| **M3** | TUI-Grundgerüst | App, Theme (menuconfig-blau + dunkel), Screen-Navigation, Standarddialoge, Datei öffnen/neu/speichern, Undo/Redo-Anbindung, laufende Wiederherstellung, Erkennung externer Änderungen, Windows-Speicherfehler, bereinigte Anzeige, Test-Infrastruktur | Navigation durch eine geladene DBC (nur lesend plus Speichern) auf allen Terminals der Checkliste. Wiederherstellung nach hartem Abbruch funktioniert. Snapshot-Tests grün. |
| **M4** | C-Generator MVP | Normale Signale, signed/unsigned, Intel/Motorola, Ganzzahl/float/double, pack/unpack mit Bereichsprüfung auf Rohwerten und atomarem Vertrag, Guards gegen unsichere Compiler-Modi, C++-Einbindung, Präfix/Namen, `gen`/`gen --check` mit LF und Bytevergleich | Generierte Tests kompilieren warnungsfrei mit gcc, clang und MSVC (C11 und C++17) und laufen über die ganze Flag-Matrix grün. Rohwerte und Frames stimmen mit cantools überein. |
| **M5** | TUI-Editor | Alle Formulare und Listen, Änderungsbefehle mit Verweisbehandlung, Eingabeprüfung gegen den Codec der Datei, Live-Prüfung, Prüfergebnisse mit Sprung, Suche, Befehlspalette, Einstellungen über `dbc2h_*`-Attribute, Generieren mit Vorschau | Eine DBC lässt sich komplett in der TUI anlegen und generieren. CANdb++ öffnet sie fehlerfrei. Kein Änderungsbefehl hinterlässt Verweise ins Leere. |
| **M6** | Bit-Layout & Frame-Tester | Bit-Grid-Widget mit Mux-Auswahl, Auto-Placement, Frame-Tester | Signal per Pfeiltasten verschieben ergibt korrektes Startbit für beide Byte-Orders |
| **M7** | Generator vollständig | Wertetabellen-Konstanten mit `to_string`, `dbc2h_SigEnumComplete`, einfaches Multiplexing mit benannter Union, CAN FD, `SIG_VALTYPE_`, `_Generic`, Nachrichtentabelle hinter `PT_IMPLEMENTATION`, Strict-Quantisierung, split-Modus | Golden-Files und Fuzz-Round-Trip grün |
| **M8** | Release 1.0 | Benutzerdokumentation, Beispiele, Changelog, `.pyz` als Release-Artefakt | Frische Installation auf allen drei Systemen nur mit Python ≥ 3.13 |

**Stand M0 (2026-11-20):** umgesetzt und lokal abgenommen.

| Plattform | Python | Tests | `.pyz` in leerer venv | TUI im echten Terminal |
|---|---|---|---|---|
| macOS arm64 | 3.15.2 | 32/32 | ok | – (nur headless geprüft) |
| Windows 11 x86-64 | 3.13 | 32/32 | ok | ok (SSH/ConPTY: Darstellung, Umlaute, `q`, Terminal wiederhergestellt) |
| Linux | – | offen | offen | offen |

Linux und Python 3.14 (sowie 3.13 auf macOS) sind lokal nicht verfügbar. Sie laufen erst in der CI-Matrix (`.github/workflows/ci.yml`), sobald das Repository auf GitHub liegt.

M3 (TUI) und M4 (Generator) hängen nicht voneinander ab und können parallel laufen. Beide bauen auf dem in M2 **gegen echtes C validierten** Numerikmodell auf. Das ist die zentrale Annahme des Projekts und wird deshalb so früh wie möglich geprüft.

**Pflichtinhalte der Benutzerdokumentation (M8):**

- `test_<prefix>.c` mit dem Produktions-Compiler und den Produktions-Flags ausführen
- Empfohlene `.gitattributes`: `*.dbc -text` und `gen/*.h text eol=lf`
- Eine neue dbc2h-Version macht alle erzeugten Header veraltet (gewollt)
- Verhalten knapp über Max mit und ohne Strict-Quantisierung (3.5)
- Lint-Regeln mit IDs und welche sich herabstufen lassen
- Tastenreferenz der TUI

**Risiken:**

| Risiko | Gegenmaßnahme |
|---|---|
| Emulation weicht von echtem C ab (FMA, Compiler-Flags) | Beide FMA-Varianten prüfen, C-Spike in M2, Flag-Matrix auf x86-64 und arm64, Header-Guards, Dokumentationspflicht zu Produktions-Flags |
| Fremde DBC-Dateien verletzen Regeln | Regel-IDs, herabstufbare Regeln, `*Ignore`-Attribute, latin-1 als letzte Encoding-Stufe |
| Verweise ins Leere nach Umbenennen/Löschen | Statement-Klassen mit bekannten Verweisen, Lint `E108`, Test für jeden Änderungsbefehl |
| Datenverlust (SSH-Abbruch, externe Änderung, gesperrte Datei unter Windows) | Laufende Wiederherstellungsdatei, Änderungserkennung, `PermissionError`-Behandlung |
| Textual ändert seine API (häufige Major-Releases) | Version in `vendor.lock` fest; Updates nur bewusst und abgesichert durch Snapshot- und Interaktionstests |
| Pflege von Textual endet | Code liegt im Repo und ist MIT-lizenziert; die gepinnte Version funktioniert weiter und kann notfalls selbst gepflegt werden. Der Kern (`core/`) ist unabhängig. |
| Ressourcen oder Metadaten im Zip nicht auffindbar | `importlib.resources` statt `__file__`, `dist-info` mitbündeln, Testsuite gegen die fertige `.pyz` |
| Attribut-Definitionen in CANdb++ | Ein Name pro Objekttyp, Test mit CANdb++ in M1 |
| Alte Windows-Konsole (conhost) zeigt Farben/Unicode eingeschränkt | Windows Terminal empfehlen; conhost in der manuellen Checkliste |
| Motorola-Bitnummerierung | Erschöpfende Tests plus Abgleich mit cantools und Referenz-DBCs |

---

## 9. Umsetzung des Reviews

Zuordnung der Befunde aus `REVIEW.md` zu den Abschnitten dieses Plans. Alle Empfehlungen wurden übernommen. Die Beispielwerte aus C1 wurden nachgerechnet (beide FMA-Varianten) und bestätigt.

| Befund | Thema | Abschnitt |
|---|---|---|
| A1 | FMA-Kontraktion | 3.3, 4, 7 |
| A2 | Bereichsprüfung auf Rohwerten | 3.1, 3.5, 4 |
| A3 | Leitungs- und Gültigkeitsbereich | 3.1, 3.4 |
| A4 | Keine Enum-Felder, Wertetabellen als Konstanten | 2.4, 3.5, 4 |
| A5 | Ein Attributname pro Objekttyp | 2.4 |
| A6 | Statement-Klassen und Verweise | 2.3, 5, 7 |
| A7 | Stylesheet über `importlib.resources` | 2, 2.1, 2.2, 8 |
| A8 | LF-normalisierter Hash, Bytevergleich in `--check` | 4, 7, 8 |
| A9 | C++-Einbindung | 4, 7 |
| B1 | Ganzzahlpfad | 3.2 |
| B2 | Nur einfaches Multiplexing, ganzer Frame wird geschrieben | 3.5, 4, 5 |
| B3 | Regel-IDs und Herabstufung | 2.4, 5 |
| B4 | Python ≥ 3.13, Versionsprüfung, Kaltstart | 1.1, 2.2, 7, 8 |
| B5 | Encoding-Stufen, bereinigte Anzeige | 1.2, 2.3 |
| B6 | Schutz vor Datenverlust | 2.3 |
| B7 | Formel-Spike in M2 | 3.3, 7, 8 |
| B8 | Bytegleicher Round-Trip | 2.3, 8 |
| B9 | Vollständige Prüfung bis 24 Bit, `UNPROVEN` | 3.3, 3.4 |
| C1–C6 | Korrekturen am Text | 3.4, 1.3, 4, 1.1, 7 |
