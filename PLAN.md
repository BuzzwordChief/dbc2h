# dbc2h – Umsetzungsplan

Werkzeug zum **Erstellen, Verwalten und Exportieren** von DBC-Dateien als benutzbare **C11-Header**, die auch aus C++ eingebunden werden können.

- **Python 3.13 oder neuer**, läuft unter **Windows, macOS und Linux**
- Auslieferung als **eine Datei `dbc2h.pyz`** (zipapp). Der Benutzer braucht nur Python, kein `pip install`.
- Fremdbibliotheken nur, wenn sie **reines Python** sind und unter einer **freien Lizenz** (MIT, BSD, PSF, Apache-2.0) stehen. Sie liegen **im Repo** (`vendor/`) und werden in die `.pyz` gebündelt.
- Verwaltung über eine **menuconfig/whiptail-artige TUI** auf Basis von **Textual**. Kein Benutzer soll DBCs über Kommandozeilenbefehle pflegen müssen.
- Festkomma-Signale werden im C-Struct als **float (Standard) oder double** abgebildet, mit **geprüfter** Darstellbarkeit (Fehler ≤ ε·LSB). Es gibt **kein automatisches Hochstufen**, nur Fehler, die der Benutzer selbst behebt.
- Beim Packen gilt: **lieber ein Fehler als ein Clamp** (NaN/Inf, Werte außerhalb des Bereichs). Eine zusätzliche, strengere Quantisierungsprüfung ist optional.

Stand: 2026-11-20. Nach dem Design-Review (`REVIEW.md`) wurde der Plan auf den Kern zurückgeschnitten. Abschnitt 9 zeigt, was aus jedem Review-Befund geworden ist. Abschnitt 10 sammelt alles, was zurückgestellt wurde.

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

Zielbild. Das Theme ist klassisch blau wie menuconfig/whiptail. Umgesetzt wird es mit Textual-Widgets und einem eigenen CSS-Theme (`dbc2h.tcss`).

```
┌──────────────────────── dbc2h – powertrain.dbc * ────────────────────────┐
│  Pfeiltasten navigieren, <Enter> öffnet, <Esc> zurück, </> sucht, <?>    │
│  zeigt Hilfe.  [*] geändert   (!) Fehler   (~) Warnung                   │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │      Netzwerk-Einstellungen  --->                                    │ │
│ │      Knoten (4)  --->                                                │ │
│ │  (!) Nachrichten (37)  --->                                          │ │
│ │      Code-Generierung  --->                                          │ │
│ │  (~) Prüfergebnisse (1 Fehler, 3 Warnungen)  --->                    │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────────┤
│   <Öffnen>   <Speichern>   <Generieren>   <Rückgängig>   <Beenden>       │
└──────────────────────────────────────────────────────────────────────────┘
```

**Menübaum** (jede Ebene ist ein Textual-`Screen` auf einem Screen-Stack):

- **Netzwerk-Einstellungen:** Kommentar und die dbc2h-Einstellungen (Präfix, Gleitkommatyp, ε, strenge Quantisierungsprüfung, Ausgabeverzeichnis)
- **Knoten:** anlegen, umbenennen, löschen, Kommentar. Beim Umbenennen und Löschen werden alle Verweise mitgezogen (siehe 2.3). Vor dem Löschen erscheint eine Rückfrage mit der Liste aller Elemente, die mit entfernt werden.
- **Nachrichten:** Tabelle (`DataTable`) mit ID, Name, DLC, Sender, Zyklus und Status, sortierbar
  - Nachricht bearbeiten: Formular für ID (Standard/Extended), Name, DLC, Sender, Zykluszeit (`GenMsgCycleTime`), Kommentar
  - **Signale:** Tabelle mit Startbit, Länge, Byte-Order, Typ und Numerikstatus
    - Signal bearbeiten: Formular (siehe 1.3), inklusive der Wertbeschreibungen des Signals (`VAL_`)
  - **Bit-Layout:** grafische 8×N-Matrix (eigenes Widget). Mit den Pfeiltasten wird das ausgewählte Signal verschoben. Überlappungen sind rot markiert, freie Bits grau. Bei gemultiplexten Nachrichten wählt man oben den angezeigten Mux-Wert.
  - **Frame testen:** Hex-Bytes eingeben und dekodierte Werte sehen, oder Werte eingeben und den Frame sehen
- **Code-Generierung:** Vorschau des Headers, Erzeugen
- **Prüfergebnisse:** Liste aller Lint- und Numerikbefunde mit Regel-ID. Mit `<Enter>` springt man direkt zum betroffenen Element.
- **Über:** Version und Lizenzen der gebündelten Bibliotheken

Vorhandene Wertetabellen (`VAL_TABLE_`), Attribut-Definitionen und fremde Attribute bleiben beim Bearbeiten unverändert erhalten. Eigene Editoren dafür sind zurückgestellt (Abschnitt 10).

**Anzeige von Dateiinhalten:** Alle Texte aus der DBC (Namen, Kommentare, Wertbeschreibungen) sind nicht vertrauenswürdige Eingaben. Steuerzeichen (einschließlich 0x80–0x9F und ESC) werden vor der Anzeige ersetzt, damit eine präparierte Datei das Terminal nicht manipulieren kann.

**Tastatur** (überall gleich, im `Footer` eingeblendet). Die Maus funktioniert dort, wo Textual sie von sich aus unterstützt, ist aber nie Pflicht.

| Taste | Funktion |
|---|---|
| ↑ ↓ PgUp PgDn Home End | Navigation |
| Enter / Esc | Öffnen / Zurück |
| Tab / Shift+Tab | Fokuswechsel zwischen Liste, Formularfeldern und Buttons |
| `n` / `d` / `r` | Neu / Löschen / Umbenennen im aktuellen Listenkontext |
| `/` oder Strg+P | Suche: Textuals Befehlspalette mit Fuzzy-Suche über alle Nachrichten und Signale |
| `u` / `U` | Rückgängig / Wiederholen |
| `s` / `g` | Speichern / Generieren |
| `?` | Kontexthilfe |
| `q` | Beenden (Rückfrage bei ungespeicherten Änderungen) |

Eigene Belegungen nutzen einfache Buchstaben, weil Terminals Strg-Kombinationen unterschiedlich durchreichen. Buchstaben-Shortcuts greifen nur, wenn kein Eingabefeld den Fokus hat.

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
│ Werte ........ 0 = Park, 1 = Reverse, …            <Bearbeiten>        │
│ C-Typ ........ (•) Standard (float)  ( ) float  ( ) double             │
│                                                                        │
│ float EXACT   Leitung 0…65535 → 0…16383.75   max. Fehler 0 LSB         │
│                                                                        │
│                      <  OK  >        <Abbrechen>                       │
└────────────────────────────────────────────────────────────────────────┘
```

- Eingabefelder nutzen Textual-Validatoren (Zahlen, Bereiche, gültige C-Bezeichner) und zeigen Fehler direkt am Feld. Zeichen, die das Encoding der Datei nicht darstellen kann, werden abgelehnt (siehe 2.3).
- Die Prüfzeile aktualisiert sich bei jeder Änderung, entprellt um ca. 150 ms. Die Prüfung läuft als Textual-Worker im Hintergrund, damit die Oberfläche flüssig bleibt. Bei float-Signalen nahe 24 Bit kann sie einige Sekunden dauern. Bis dahin steht dort „wird geprüft …“.
- Bei `LOSSY` wird die Zeile rot und nennt die konkreten Abhilfen: „double wählen“, „LSB vergrößern“ oder „Länge reduzieren“. Mit einem fehlerhaften Signal lässt sich das Formular trotzdem speichern, die DBC darf zwischenzeitlich inkonsistent sein. Die **Generierung wird aber verweigert**, solange Fehler bestehen.

---

## 2. Architektur

Entwicklung als Paket, Auslieferung per Build-Skript als `dbc2h.pyz`.

```
dbc2h/
  __init__.py          Version (alte Syntax, wird vor der Versionsprüfung importiert)
  __main__.py          Versionsprüfung (alte Syntax), dann cli.main
  cli.py               Argumente: ohne Subcommand TUI, sonst gen/check, --version
  core/                ── reines Python, null Abhängigkeiten ──
    model.py           Datenmodell (dataclasses, slots=True), snapshot() für Vergleiche
    statements.py      Statement-Klassen, Originaltext, Dirty-Flag, Verweise
    lexer.py           Tokenizer mit Zeile:Spalte
    parser.py          Statements abgrenzen, dann interpretieren; Fehler mit Zeile:Spalte
    document.py        Modell + Statements, Einfügen/Entfernen/Dirty, Attributwerte
    writer.py          kanonische Ausgabe geänderter und neuer Statements
    textio.py          Encoding-Erkennung, atomares Speichern, Änderungserkennung
    dbcfile.py         Datei laden/neu/speichern (.bak einmal pro Sitzung)
    settings.py        Lesen/Schreiben der dbc2h_*-Attribute
    layout.py          Bitmapping Intel/Motorola, Überlappung (mux-bewusst), freie Bereiche
    numerics.py        Bereiche, Typwahl, Umrechnungsformeln (Conversion), float32-Emulation,
                       Einstufung; Python-Referenz für Encode/Decode
    lint.py            Prüfregeln mit stabiler ID, Severity und Elementverweis
    edit.py            Änderungsoperationen (Command-Pattern) mit Undo/Redo,
                       Verweise werden mitgezogen
    codegen_c.py       C11-Generator (nutzt numerics.Conversion)
  tui/                 ── nutzt Textual ──
    app.py             App, Screen-Stack, Bindings, Suche, Notfall-Speicherung
    dbc2h.tcss         Theme (menuconfig-blau)
    screens/           main_menu, network, nodes, messages, message_form, signals,
                       signal_form, codegen, findings, frame_tester, about
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
  make_candbpp_check.py  Testdateien für den manuellen CANdb++-Check
  vendor.py            lock / verify / sync der gebündelten Wheels
  build_pyz.py         Bündeln: Code + Ressourcen + Wheels + Lizenzen → dbc2h.pyz
  pyz_main.py          Starter an der Wurzel der .pyz (alte Syntax)
  dev_env.py           venv offline aus vendor/wheels + requirements-dev.txt
tests/
requirements-dev.txt   Nur für Entwicklung/CI, wird nicht ausgeliefert
```

**Schichtregeln:**

- `core/` importiert nichts außerhalb der Standardbibliothek und weiß nichts von der TUI. Das hält den Kern einfach testbar, und `gen`/`check` laufen ohne Textual. Ein Test prüft das.
- Die TUI ändert das Modell ausschließlich über `core/edit.py`.
- **Kein Dateizugriff relativ zu `__file__`.** Mitgelieferte Ressourcen werden immer über `importlib.resources` gelesen, sonst funktionieren sie in der `.pyz` nicht.

### 2.1 TUI-Basis: Textual

**Warum Textual:** Textual (MIT) ist reines Python, unterstützt Windows, macOS und Linux offiziell und bringt mit, was der Editor braucht: Screens, `DataTable`, `Input` mit Validierung, `Select`, `RadioSet`, `Checkbox`, modale Dialoge, `Footer` mit Tastenanzeige, Befehlspalette, CSS-Themes und Syntax-Highlighting (über Rich/Pygments). Außerdem kann man die App ohne Terminal testen (`App.run_test()`). Textual stellt das Terminal auch bei Exceptions und Strg+C wieder her.

**Machbarkeit geprüft (M0):** Textual 8.2.8 läuft samt Abhängigkeiten aus einer komprimierten `.pyz` (ca. 2,6 MB) und lässt sich dort ohne Terminal steuern.

**Stylesheet aus der `.pyz` laden:** Textual liest `CSS_PATH`-Dateien mit einfachem `open()`. Innerhalb einer Zip-Datei schlägt das fehl. Das App-Stylesheet wird deshalb über `importlib.resources` gelesen und als `CSS` übergeben (umgesetzt in M0).

**Was selbst gebaut wird:**

- `bitgrid.py`: eigenes Widget (Rendering mit Rich-`Text`, Tasten-Events)
- Dialog-Flows, Formulare, Menüstruktur
- Theme im menuconfig-Stil

**Grenzen und Vorgaben:**

- Mindestgröße 80×24, darunter erscheint ein Hinweis statt eines kaputten Layouts.
- Empfohlene Terminals: Windows Terminal, macOS Terminal/iTerm2, gängige Linux-Terminals, SSH. Die alte Windows-Konsole (conhost) darf eingeschränkt aussehen.
- `NO_COLOR` wird respektiert.

### 2.2 Vendoring und Build

- **`vendor.lock`** enthält pro Paket Name, exakte Version, sha256 und SPDX-Lizenz. Heute bedeutet das Textual 8.2.8 mit Rich, Pygments (BSD-2), markdown-it-py, mdurl, mdit-py-plugins, linkify-it-py, platformdirs (alle MIT) und typing_extensions (PSF).
- **`tools/vendor.py`** lädt die Wheels und prüft dabei:
  - die sha256-Hashes (beim Lock zusätzlich gegen PyPI),
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
- **Startzeit:** Textual wird nur für die TUI importiert. `gen`/`check` laden ausschließlich `core/`. Gemessen in M0 (Median aus 5 Starts, leere venv):

  | | `--version` | TUI bereit (Selbsttest) | davon Import Textual |
  |---|---|---|---|
  | macOS arm64, Python 3.15 | 36 ms | 357 ms | 211 ms |
  | Windows x86-64, Python 3.13 | 95 ms | 998 ms | 730 ms |

  Für ein interaktives Werkzeug ist das akzeptabel.
- **Versionspflege:** Textual veröffentlicht häufig neue Major-Versionen. Updates erfolgen bewusst: `vendor.lock` anheben, Tests laufen lassen, Abweichungen prüfen.

**Entwicklungsabhängigkeiten** (`requirements-dev.txt`, gepinnt, nur Entwicklung und CI, **nicht** in der `.pyz`):

| Paket | Lizenz | Zweck |
|---|---|---|
| pytest | MIT | Testrunner |
| cantools | MIT | Unabhängige Gegenprobe für Parser (M1) und für Rohwerte und Frames (M4) |

### 2.3 Datenmodell und Datei-Handling (umgesetzt in M1)

**Zahlen:** Factor und Offset werden als `fractions.Fraction` plus Originalstring gespeichert. `0.1` ist damit exakt `1/10`, und der Writer gibt die Zahl unverändert aus.

**Statement-Klassen:** Jede Art von DBC-Statement gehört zu genau einer Klasse.

| Klasse | Behandlung | Beispiele |
|---|---|---|
| Modelliert und editierbar | Vollständige Modellobjekte | `BU_`, `BO_`, `SG_`, `CM_`, `VAL_`, `VAL_TABLE_`, `BA_DEF_`, `BA_DEF_DEF_`, `BA_`, `SIG_VALTYPE_` |
| Mit Verweisen | Originaltext bleibt erhalten, die Positionen aller Verweise (Namen, IDs) sind bekannt | `SIG_GROUP_`, `BO_TX_BU_`, `SG_MUL_VAL_`, `BA_REL_`, `BA_DEF_REL_`, `EV_`-Familie, Statements mit Verweisen auf nicht vorhandene Objekte |
| Undurchsichtig | Wörtlich erhalten | Alles, was der Parser nicht kennt |

- Jeder Befehl in `core/edit.py`, der umbenennt, löscht oder eine ID ändert, zieht **alle Verweise der ersten beiden Klassen im selben Undo-Schritt** mit (M5).
- Lint-Fehler `E108` für Verweise ins Leere.

**Byte-identischer Round-Trip:**

- Jedes Top-Level-Statement behält seinen Originaltext und ein Dirty-Flag.
- Der Writer gibt für unveränderte Statements den Originaltext aus, für geänderte oder neue eine kanonische Form im Stil von CANdb++.
- Neue Statements kommen hinter das letzte Statement derselben Art.
- Daraus folgt: Laden und Speichern ohne Änderung ist **bytegleich**. Nach einer Änderung betrifft der Diff nur die geänderten Statements.

**Encoding:**

- Dekodiert wird in drei Stufen: UTF-8 (mit BOM-Erkennung), dann cp1252, dann latin-1. latin-1 akzeptiert jedes Byte, der Round-Trip bleibt also in jedem Fall verlustfrei.
- Codec, BOM und Zeilenende werden gemerkt und beim Schreiben wiederverwendet.
- Eingabefelder lehnen Zeichen ab, die der Codec der Datei nicht kodieren kann.
- Neue Dateien werden als **cp1252** mit CRLF angelegt. Ob CANdb++ UTF-8 sauber verarbeitet, klärt der manuelle Check (`docs/candbpp-check.md`).

**Speichern:**

- Pfad über Symlinks auflösen, temporäre Datei im Zielverzeichnis schreiben, Dateirechte übernehmen, dann `os.replace`. Beim ersten Speichern jeder Sitzung wird eine `.bak` angelegt.
- **Änderungen auf der Platte erkennen:** Beim Laden und Speichern wird der Hash festgehalten. Weicht die Datei vor dem Speichern ab, fragt die TUI: überschreiben, neu laden oder unter anderem Namen speichern.
- **Windows:** `os.replace` scheitert mit `PermissionError`, solange ein anderes Programm die Datei offen hält. Dann bleibt die temporäre Datei erhalten, und die TUI zeigt Pfad und Ursache an.

**Schutz vor Datenverlust in der TUI (M3):** Rückfrage beim Beenden mit ungespeicherten Änderungen. Bei einer unbehandelten Exception schreibt die App den aktuellen Stand nach `<datei>.dbc2h-recovery` und bietet ihn beim nächsten Öffnen an. Eine laufend mitgeschriebene Wiederherstellungsdatei (auch bei SSH-Abbruch) ist zurückgestellt (Abschnitt 10).

### 2.4 Einstellungen in der DBC selbst

Alle dbc2h-Einstellungen liegen als **eigene DBC-Attribute** in der DBC. Die Datei bleibt so in sich vollständig. In DBC wird eine Attribut-Definition über ihren Namen identifiziert. Deshalb hat **jedes Attribut genau einen Objekttyp**, nach Vectors Konvention `GenMsg…`/`GenSig…`. Alle Namen sind höchstens 32 Zeichen lang.

| Objekt | Attribut | Werte | Default | Wirkung ab |
|---|---|---|---|---|
| Netzwerk | `dbc2h_Prefix` | String | aus Dateiname | M4 |
| Netzwerk | `dbc2h_FloatType` | `float` / `double` | `float` | M2 |
| Netzwerk | `dbc2h_StrictQuantization` | 0/1 | 0 (siehe 3.5) | M4 |
| Netzwerk | `dbc2h_FloatEpsLsb` | Float, 0 … 0.5 | 0.0625 (1/16 LSB) | M2 |
| Netzwerk | `dbc2h_OutputDir` | Pfad relativ zur DBC, immer mit `/` | `.` | M4 |
| Nachricht | `dbc2h_MsgIgnore` | 0/1 | 0 | M2 |
| Signal | `dbc2h_SigFloatType` | `default` / `float` / `double` | `default` | M2 |
| Signal | `dbc2h_SigIgnore` | 0/1 | 0 | M2 |

- `BA_DEF_` und `BA_DEF_DEF_` werden erst geschrieben, wenn zum ersten Mal ein Wert vom Default abweicht. `BA_` wird nur für abweichende Werte geschrieben. Eine fremde DBC, die nur geöffnet und zum Generieren genutzt wird, bleibt dadurch unverändert.
- `settings.py` kennt aus M1 noch drei weitere Attribute: `dbc2h_OutputMode`, `dbc2h_Lint`, `dbc2h_SigEnumComplete`. Ihre Features sind zurückgestellt (Abschnitt 10). Die TUI zeigt sie nicht an. Sie werden beim Start von M2 aus `settings.py` entfernt, und die CANdb++-Testdateien werden neu erzeugt.

TUI-Vorlieben (zuletzt geöffnete Dateien) gehören dem Benutzer und nicht der DBC. Sie liegen im Konfigurationsverzeichnis des Benutzers, das `platformdirs` liefert (eine Textual-Abhängigkeit).

---

## 3. Numerik

### 3.1 Begriffe

| Begriff | Bedeutung |
|---|---|
| **Leitungsbereich** | Alle Bitmuster der Signalbreite: unsigned `[0, 2ⁿ−1]`, signed `[−2ⁿ⁻¹, 2ⁿ⁻¹−1]` |
| **Gültigkeitsbereich** | `[RAW_MIN, RAW_MAX]`: der Teil des Leitungsbereichs, der in [Min, Max] der DBC liegt |
| **LSB** | Betrag des Faktors |

`RAW_MIN` und `RAW_MAX` werden exakt aus den Fractions berechnet: `ceil((Min − Offset) / Faktor)` und `floor((Max − Offset) / Faktor)`, geschnitten mit dem Leitungsbereich. Bei negativem Faktor tauschen Min und Max die Rollen. Min/Max = 0/0 bedeutet nach DBC-Konvention „voller Leitungsbereich“. Liegt Min/Max außerhalb des Leitungsbereichs oder ist Min > Max, gibt es eine Warnung (`W103`, `W104`), und es gilt der Schnitt bzw. der volle Leitungsbereich.

### 3.2 Typwahl (kein automatisches Hochstufen)

1. **Ganzzahlpfad:** Faktor und Offset sind ganzzahlig.
   - Der Feldtyp ist der kleinste `intN_t`/`uintN_t`, der den physikalischen Wertebereich über den Leitungsbereich fasst. Berechnet wird mit Python-Ganzzahlen.
   - Passt der Bereich weder in `int64_t` noch in `uint64_t`, ist das ein Fehler (`E202`).
   - `pack` liefert `ERR_QUANTIZATION`, wenn `(phys − offset)` nicht durch den Faktor teilbar ist.
2. **Gleitkommapfad:** Der gewählte Typ ist der Signal-Override (`dbc2h_SigFloatType`), sonst der Netzwerk-Default (`dbc2h_FloatType`), sonst `float`.
3. Signale mit `SIG_VALTYPE_` (IEEE float/double auf dem Bus) werden 1:1 zu `float` bzw. `double`.
4. Der gewählte Typ wird **geprüft**. Fällt die Prüfung durch, ist das ein **Fehler**. Das Tool ändert den Typ nie selbst.

### 3.3 Umrechnung und Prüfung

**Die Formeln stehen an einer Stelle.** `numerics.Conversion` hält für jedes Signal fest, wie umgerechnet wird: Zieltyp, die Konstanten Faktor und Offset als Literale des Zieltyps (auf den nächsten darstellbaren Wert gerundet) und ob beim Encode mit dem exakten Kehrwert multipliziert wird (nur bei Zweierpotenz-Faktor). Prüfer, Python-Referenz und C-Generator nutzen dasselbe Objekt.

```
decode:  phys = (T)raw * F + O
encode:  raw  = round((v - O) / F)      bzw.  round((v - O) * (1/F))  bei Zweierpotenz-Faktor
```

**float32 nachrechnen:** Python rechnet in binary64 und rundet nach jeder Operation auf binary32 (`array.array('f', …)`). Weil 53 ≥ 2·24 + 2 gilt, ist das für + − × ÷ bitgenau (Figueroa).

**Zusammengefasste Multiplikation-Addition (FMA):** Compiler dürfen `raw * F + O` zu einer Operation mit nur einer Rundung zusammenziehen (Clang standardmäßig, GCC bei `-std=gnu11`, AArch64 und Cortex-M4F/M7 haben FMA). Das Ergebnis kann dann in der letzten Stelle abweichen. Diese Variante wird **nicht exakt nachgerechnet**, sondern nach oben abgeschätzt: Fehler der beiden Konstanten plus eine halbe ulp des Ergebnisses. Geprüft wird gegen das Maximum aus nachgerechneter getrennter Variante und dieser Schranke. Ob das auf echten Compilern hält, zeigen die generierten C-Tests in M4 (ein CI-Job mit `-ffp-contract=fast` auf arm64).

### 3.4 Einstufung

Ein Signal ist gültig, wenn beide Bedingungen gelten:

1. Für jeden Rohwert im **Leitungsbereich** ist der Decode-Fehler ≤ ε·LSB.
2. Für jeden Rohwert im **Gültigkeitsbereich** liefert Encode nach Decode wieder denselben Rohwert.

| Stufe | Bedeutung | Ergebnis |
|---|---|---|
| `EXACT` | Jeder Rohwert ergibt den mathematisch exakten Wert | OK |
| `OK` | Beide Bedingungen erfüllt, mit Angabe des maximalen Fehlers | OK |
| `LOSSY` | Eine Bedingung verletzt, oder der Leitungsbereich ist breiter als die Mantisse (> 24 Bit bei float, > 53 Bit bei double). Mit Gegenbeispiel und Abhilfe | **Fehler** (`E201`) |

- **ε** ist relativ zum LSB, Standard **1/16 LSB**, einstellbar über `dbc2h_FloatEpsLsb`. Für den Round-Trip würde theoretisch < 0.5 LSB reichen, der Rest ist Reserve für Arithmetik beim Anwender.
- **float:** Jedes Signal bis 24 Bit wird **vollständig durchgerechnet**. Ein 16-Bit-Signal dauert Millisekunden, ein 24-Bit-Signal einige Sekunden (im Hintergrund).
- **double:** eine einfache Schranke über den ganzen Leitungsbereich (Fehler der Konstanten · |raw|max plus 1,5 ulp des größten Zwischenwerts). Bei double ist der Abstand zu ε·LSB in der Praxis riesig. Hält die Schranke nicht, ist das `LOSSY`.

Beispiel (TUI-Prüfergebnisse bzw. `check`), Werte nachgerechnet:

```
VehicleStatus.speed    16u  LSB 0.25           float   EXACT
Engine.temp             8s  LSB 0.1  off -40   float   OK      max. Fehler 1.5e-5 LSB
Trip.distance          20u  LSB 0.001          float   LOSSY   0.063 LSB bei raw=683677 (> 1/16)
                                                               → double wählen, LSB vergrößern oder Länge reduzieren
Odo.distance           32u  LSB 0.001          float   LOSSY   32 Bit > 24 Bit Mantisse
                                                               → double wählen oder Länge reduzieren
```

(Das gleiche Signal wie `Trip.distance` mit 19 Bit ist gültig: max. Fehler 0.055 LSB.)

### 3.5 Encode (Struct → Puffer): Fehler statt Clamp

Die Bereichsprüfung findet auf dem **ganzzahligen Rohwert** statt, nicht auf dem Gleitkommawert. Ein Vergleich gegen das float-Literal von Max würde Werte ablehnen, die `unpack` selbst erzeugt hat: `decode(raw_max)` kann eine ulp über `(float)Max` liegen, z. B. bei 10 Bit, Faktor 0.1, Offset −40 (62.30000305 gegen 62.29999924).

Prüfreihenfolge pro Gleitkomma-Signal:

1. `!(v >= -FLT_MAX && v <= FLT_MAX)` → `ERR_NOT_FINITE` (erkennt NaN und ±Inf)
2. `!(v >= GUARD_LO && v <= GUARD_HI)` → `ERR_RANGE`. Die Guards sind exakt darstellbare Werte, die den gültigen Bereich mit Abstand umschließen. Ihr einziger Zweck: Die folgende Umwandlung in eine Ganzzahl darf kein undefiniertes Verhalten auslösen.
3. `r = round((v − O) / F)` gemäß `Conversion`
4. `r < RAW_MIN || r > RAW_MAX` → `ERR_RANGE`
5. **Strenge Quantisierungsprüfung (optional, Standard aus):** `|v − decode(r)| > ε·LSB` → `ERR_QUANTIZATION`. Dann wird z. B. 12.3 bei LSB 0.25 nicht still als 12.25 gesendet. Standardmäßig ist sie aus, weil normale Rechenwerte fast nie genau auf dem Raster liegen; ohne sie wird auf den nächsten Rohwert gerundet. Einschalten über `dbc2h_StrictQuantization = 1`.

**Folge, die dokumentiert wird:** Ohne strenge Quantisierung wird ein Wert knapp über Max akzeptiert, wenn er auf `RAW_MAX` rundet. Mit ihr wird er abgelehnt.

**Atomarer Vertrag:**

- `pack()` prüft zuerst **alle** Signale. Der Status ist die ODER-Verknüpfung aller Fehlerflags.
- Nur bei `OK` wird geschrieben, sonst bleibt der **Puffer unverändert**.
- Geschrieben werden **alle DLC-Bytes**. Bits, die kein aktives Signal belegt, sind 0. Der alte Inhalt des Puffers gelangt nie in den Frame.

**Decode:** prüft die Pufferlänge und optional den Gültigkeitsbereich (über dieselben ganzzahligen Grenzen). Das Struct wird auch bei `ERR_RANGE` gefüllt, damit Diagnose möglich bleibt. Für jedes Bitmuster ist das Ergebnis definiert (3.4, Bedingung 1).

---

## 4. Generierter C11-Code

Generierter Code und seine Kommentare sind durchgehend **englisch**, passend zu den Bezeichnern. Ziel ist **C11**; der Header lässt sich **auch aus C++ einbinden** (C++11 oder neuer). Gemischte C/C++-Projekte sind im Zielbereich üblich.

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
#  error "dbc2h: fast-math invalidates the checked conversion guarantees"
#endif
#if defined(FLT_EVAL_METHOD) && FLT_EVAL_METHOD != 0
#  error "dbc2h: excess-precision evaluation (e.g. x87) differs from the checked model"
#endif

typedef uint32_t pt_status_t;
#define PT_OK                0u
#define PT_ERR_LENGTH        (1u << 0)
#define PT_ERR_NOT_FINITE    (1u << 1)
#define PT_ERR_RANGE         (1u << 2)
#define PT_ERR_QUANTIZATION  (1u << 3)

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
        /* only with dbc2h_StrictQuantization = 1:
         *   float diff = msg->speed - (float)speed_raw * 0.25f;
         *   if (diff < -0.015625f || diff > 0.015625f) st |= PT_ERR_QUANTIZATION;   (1/16 LSB) */
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
- **Wertetabellen** (M7) erzeugen `#define`-Konstanten und eine `static inline`-Funktion `*_to_string()`, die bei unbekannten Werten `NULL` liefert. Das gilt nur für **unskalierte Ganzzahlsignale** (Faktor 1, Offset 0). Bei skalierten Signalen stehen die Einträge im Doxygen-Kommentar, dazu kommt ein Info-Befund (`I501`).
- **Namen aus Wertbeschreibungen** sind Freitext und kollidieren nach der Umwandlung oft („Not available“, „not_available“). Solche Kollisionen werden deterministisch durch Anhängen des Werts aufgelöst (`..._NOT_AVAILABLE_14`), mit Info-Befund `I502`. Kollisionen bei Nachrichten- und Signalnamen bleiben ein harter Fehler (`E301`).
- **Namen:** Grundlage ist der lange Name (`System*LongSymbol`, sonst der DBC-Name). Namen, die mit einer Ziffer beginnen, bekommen einen Unterstrich vorangestellt.
- **Strings aus der DBC** werden sicher in C übertragen: In String-Literalen nur druckbares ASCII, alles andere als Oktal-Escape. In Kommentaren wird `*/` entschärft.
- **Header-only:** Alle Funktionen sind `static inline`.
- **C++-Einbindung:** `PT_STATIC_ASSERT` statt `_Static_assert`, `extern "C"`, keine anonymen Structs oder Unions, keine C-only-Konstrukte wie `_Generic` oder compound literals. Explizite Casts bei jeder einschränkenden Umwandlung, kein `void *`.
- **Multiplexing (M7, nur einfaches):** Selektorfeld plus **benannte** Union mit einem benannten Struct pro Mux-Wert, z. B. `msg.mux.m3.signal`. Das ist in C und C++ gültig. `pack` schreibt nur die aktiven Signale, alle übrigen Bits sind 0. Nachrichten mit erweitertem Multiplexing (`SG_MUL_VAL_`) führen zu Fehler `E110`. Sie bleiben im Round-Trip unverändert und lassen sich mit `dbc2h_MsgIgnore` ausnehmen.
- **CAN FD (M7):** DLC bis 64 Byte, Längenprüfung gegen die gültigen FD-Längen.
- **MSVC-kompatibel** (`/std:c11`): keine VLAs, kein `[static N]`.
- **Rundung** ohne libm-Abhängigkeit (eigene `round`-Hilfsfunktion mit definiertem Verhalten bei .5). Ganzzahl-Zwischenwerte in `int32_t`, wenn die Guards das erlauben, sonst `int64_t`.
- **Erkennbare unsichere Compiler-Modi** brechen den Build: `__FAST_MATH__` (GCC/Clang), `_M_FP_FAST` (MSVC), `FLT_EVAL_METHOD != 0`. Nicht erkennbar sind z. B. `-funsafe-math-optimizations` ohne `-ffast-math`. Deshalb steht in der Dokumentation: **`test_<prefix>.c` muss mit dem Produktions-Compiler und den Produktions-Flags laufen.**
- **Deterministische Ausgabe:** kein Zeitstempel. Der Hash im Header wird über den Dateiinhalt mit auf LF normalisierten Zeilenenden berechnet. Erzeugte Dateien werden **immer mit LF** geschrieben.
- **`gen --check`** erzeugt im Speicher neu und vergleicht bytegenau mit den vorhandenen Dateien. Eine neue dbc2h-Version macht alle Header veraltet, weil die Version in der ersten Zeile steht. Das ist gewollt.
- **Generierte C-Tests** (`test_<prefix>.c`): Python erzeugt mit der Referenz aus `numerics` Testvektoren (Grenzwerte, Zufallswerte mit festem Seed, Werte knapp neben der Quantisierungsgrenze). Ist ein Compiler vorhanden, ruft dbc2h ihn auf.
- Doxygen-Kommentare aus CM_, Einheit, Bereich und Numerikstatus.
- Compiler-sauber mit `-Wall -Wextra -Wpedantic -Wconversion -Wdouble-promotion -Werror` (gcc, clang) und `/W4 /WX` (MSVC), als C11 und als C++17.

---

## 5. Prüfregeln (Lint)

Jede Regel hat eine **stabile ID**, die bei jedem Befund ausgegeben wird. **Fehler** verhindern falschen Code und blockieren die Generierung. Daran kommt man nur vorbei, indem man das Element mit `dbc2h_MsgIgnore` bzw. `dbc2h_SigIgnore` ausnimmt. **Warnungen** haben ein definiertes Verhalten und blockieren nichts. So lassen sich auch fremde OEM-Dateien nutzen, ohne dass es einen Mechanismus zum Herabstufen braucht.

**Ab M2:**

| ID | Regel | Severity |
|---|---|---|
| E101 | Signale überlappen sich (mux-bewusst: nur wenn beide gleichzeitig aktiv sein können) | Fehler |
| E102 | Signal reicht über das DLC hinaus | Fehler |
| E105 | Faktor 0 | Fehler |
| E106 | Doppelte Nachrichten-ID | Fehler |
| E107 | Doppelter Name (Nachricht, Signal in einer Nachricht, Knoten) | Fehler |
| E108 | Verweis ins Leere (dangling reference) | Fehler |
| E109 | Multiplexing ohne Selektor oder mit doppeltem Mux-Wert | Fehler |
| E110 | Erweitertes Multiplexing (`SG_MUL_VAL_`), vom Generator nicht unterstützt | Fehler |
| E111 | Ungültige Multiplex-Angabe (z. B. ein einzelnes `m`) | Fehler |
| E112 | Standard-ID größer als 0x7FF (fehlt das Extended-Flag?) | Fehler |
| E201 | Numerik `LOSSY` | Fehler |
| E202 | Ganzzahlpfad: Bereich passt nicht in 64 Bit | Fehler |
| W103 | Min/Max außerhalb des Leitungsbereichs; es gilt der Schnitt | Warnung |
| W104 | Min > Max; es gilt der volle Leitungsbereich | Warnung |
| W406 | Name beginnt mit einer Ziffer (in DBC nicht erlaubt, kommt aber vor) | Warnung |

**Mit dem Generator (M4/M7):**

| ID | Regel | Severity |
|---|---|---|
| E301 | Kollision von C-Bezeichnern bei Nachrichten oder Signalen | Fehler |
| I501 | Wertetabelle an skaliertem Signal, keine Konstanten erzeugt | Info |
| I502 | Namenskollision bei Wertbeschreibungen, durch Wert-Suffix aufgelöst | Info |

Jeder Befund verweist auf ein Element. In der TUI springt man mit `<Enter>` direkt dorthin. Weitere Komfort-Regeln sind zurückgestellt (Abschnitt 10).

---

## 6. Features in 1.0

1. DBC-Verwaltung in der TUI: Knoten, Nachrichten, Signale mit Wertbeschreibungen, Kommentare, Zykluszeit, dbc2h-Einstellungen. Umbenennen und Löschen ziehen alle Verweise mit.
2. **Bit-Layout-Editor** mit Verschieben per Pfeiltasten, Intel/Motorola-Darstellung, Markierung von Überlappungen und Auswahl des Mux-Werts
3. **Auto-Placement:** Neues Signal mit Länge X wird automatisch an die erste freie Position gesetzt
4. **Live-Numerikprüfung** im Signal-Formular mit konkreten Abhilfen
5. **Frame-Tester** in der TUI: Hex-Bytes eingeben und dekodierte Werte sehen oder umgekehrt, mit demselben Fehlerstatus wie im C-Code (nutzt die Python-Referenz aus `numerics`)
6. Suche über Nachrichten und Signale (Textuals Befehlspalette)
7. Undo/Redo, Kontexthilfe, Rückfrage bei ungespeicherten Änderungen, Notfall-Speicherung bei Absturz, Erkennung externer Änderungen
8. Generierung aus der TUI mit Vorschau; nicht-interaktiv mit `gen` / `gen --check` / `check` für CI und Build-Systeme
9. **Generierte C-Tests** (`test_<prefix>.c`)
10. Header aus **C und C++** einbindbar

Ideen nach 1.0 und alles Zurückgestellte stehen in Abschnitt 10.

---

## 7. Teststrategie

- **pytest** als Testrunner (nur Entwicklung und CI)
- **Parser/Writer (M1, umgesetzt):** Korpus aus 147 echten DBCs: Laden und Speichern ohne Änderung ist bytegleich, kanonisches Neuschreiben ändert das Modell nicht, Verweispositionen stimmen, Struktur stimmt mit cantools überein. Dazu gezielte Tests für Grammatik-Eigenheiten, Encoding-Stufen und Speichern.
- **Änderungsbefehle (M5):** Für jeden Befehl wird das Ergebnis geschrieben und neu geparst. Kein Verweis darf ins Leere zeigen, und der Diff darf nur die betroffenen Statements umfassen.
- **Bitmapping (M2):** erschöpfend für alle Startbit/Längen-Kombinationen in 8 und 64 Byte, für Intel und Motorola. Die Motorola-Sägezahnnummerierung ist die klassische Fehlerquelle.
- **Numerik (M2):** feste Fälle mit nachgerechneten Ergebnissen (darunter die aus 3.4), Grenzfälle an Zweierpotenzen, negativer Faktor, signed/unsigned.
- **Codegen (M4/M7):**
  - Golden-Files
  - generierte C-Tests mit gcc und clang (Linux), MSVC (Windows) und clang mit `-ffp-contract=fast` auf macOS arm64 (FMA)
  - derselbe Test einmal als **C++17** mit g++, clang++ und MSVC übersetzt, gleiche Warnstufe
  - Rohwerte und Frames im Vergleich mit cantools
  - `gen --check` auf Windows mit `core.autocrlf=true` ausgecheckt
- **TUI (M3/M5/M6):** Interaktionstests mit `App.run_test()`: Tastenfolgen abspielen, danach Modellzustand und Undo-Stack prüfen. Manuelle Sichtprüfung in Windows Terminal (auch über SSH) und macOS Terminal.
- **Build:** Die Testsuite läuft auch gegen die **fertige `.pyz`**, die in einer **sauberen Umgebung ohne installierte Pakete** gestartet wird.
- **CI-Matrix:** Windows, macOS (arm64) und Linux (x86-64) mit Python 3.13, 3.14 und 3.15
- **Manuell, einmalig:** CANdb++-Check (`docs/candbpp-check.md`).

---

## 8. Meilensteine

| # | Meilenstein | Inhalt | Abnahmekriterium |
|---|---|---|---|
| **M0** | Grundgerüst & Build | Paketstruktur, Versionsprüfung, Vendoring mit Hash-, Lizenz- und Pure-Python-Prüfung, reproduzierbarer `.pyz`-Build, CI-Matrix | **erledigt** |
| **M1** | Parser & Writer | Datenmodell, Statement-Klassen und Verweise, Parser mit Fehlerpositionen, Writer mit Originaltext und Dirty-Flag, Encoding-Stufen, atomares Speichern mit Änderungserkennung, Einstellungs-Attribute | **erledigt** bis auf den manuellen CANdb++-Check |
| **M2** | Layout, Numerik, Lint | `layout.py` (Bitmapping, Überlappung, DLC), `numerics.py` (Bereiche, Typwahl, `Conversion`, float-Prüfung durch vollständiges Durchrechnen, double per Schranke, Python-Referenz für Encode/Decode), `lint.py` mit den Regeln aus Abschnitt 5, `check`; Aufräumen von `settings.py` (2.4) | Bitmapping-Tests erschöpfend grün. Einstufung stimmt für die festen Fälle. `check` meldet über den ganzen Korpus nur erklärbare Befunde. |
| **M3** | TUI-Grundgerüst | App, Theme, Screen-Navigation, Standarddialoge, Datei öffnen/neu/speichern, Undo/Redo-Anbindung, Notfall-Speicherung, Dialoge für externe Änderungen und gesperrte Dateien, bereinigte Anzeige, Interaktionstests | Navigation durch eine geladene DBC (nur lesend plus Speichern) in Windows Terminal und macOS Terminal. Interaktionstests grün. |
| **M4** | C-Generator MVP | Normale Signale, signed/unsigned, Intel/Motorola, Ganzzahl/float/double, pack/unpack mit Bereichsprüfung auf Rohwerten, optionaler strenger Quantisierungsprüfung und atomarem Vertrag, Guards gegen unsichere Compiler-Modi, C++-Einbindung, Präfix/Namen (lange Namen, Ziffern am Anfang), generierte C-Tests, `gen`/`gen --check` | Generierte Tests kompilieren warnungsfrei mit gcc, clang und MSVC (C11 und C++17) und laufen grün, auch mit `-ffp-contract=fast` auf arm64. Rohwerte und Frames stimmen mit cantools überein. |
| **M5** | TUI-Editor | Formulare und Listen für Knoten, Nachrichten, Signale (inkl. Wertbeschreibungen), Netzwerk-Einstellungen; Änderungsbefehle mit Verweisbehandlung, Eingabeprüfung gegen den Codec der Datei, Live-Prüfung, Prüfergebnisse mit Sprung, Suche, Generieren mit Vorschau | Eine DBC lässt sich komplett in der TUI anlegen und generieren. CANdb++ öffnet sie fehlerfrei. Kein Änderungsbefehl hinterlässt Verweise ins Leere. |
| **M6** | Bit-Layout & Frame-Tester | Bit-Grid-Widget (Tastatur) mit Mux-Auswahl, Auto-Placement, Frame-Tester | Signal per Pfeiltasten verschieben ergibt korrektes Startbit für beide Byte-Orders |
| **M7** | Generator vollständig | Wertetabellen-Konstanten mit `to_string`, einfaches Multiplexing mit benannter Union, CAN FD, `SIG_VALTYPE_` | Golden-Files und generierte C-Tests grün |
| **M8** | Release 1.0 | Benutzerdokumentation, Beispiele, Changelog, `.pyz` als Release-Artefakt | Frische Installation auf allen drei Systemen nur mit Python ≥ 3.13 |

M3 (TUI) und M4 (Generator) hängen nicht voneinander ab und können parallel laufen.

**Stand M0 (2026-11-20):** umgesetzt und abgenommen.

| Plattform | Python | Tests | `.pyz` in leerer venv | TUI im echten Terminal |
|---|---|---|---|---|
| macOS arm64 | 3.15.2 | 32/32 | ok | – (nur headless geprüft) |
| Windows 11 x86-64 | 3.13 | 32/32 | ok | ok (SSH/ConPTY: Darstellung, Umlaute, `q`, Terminal wiederhergestellt) |
| CI (GitHub Actions) | 3.13, 3.14, 3.15 | 9/9 Jobs grün (Linux x86-64, Windows x86-64, macOS arm64) | ok | – |

Der Inhalts-Digest der `.pyz` ist in allen 9 CI-Builds gleich.

**Stand M1 (2026-11-20):** umgesetzt. Offen ist nur der manuelle CANdb++-Check (`docs/candbpp-check.md`).

- Korpus: 147 DBC-Dateien (57 aus opendbc, 90 aus den cantools-Tests, beide MIT, gepinnt in `tests/corpus/README.md`). Alle werden geladen. Laden und Speichern ohne Änderung ist **bytegleich**. Werden alle modellierten Statements kanonisch neu geschrieben, ergibt das erneute Laden dasselbe Modell.
- Gegenprobe mit cantools: 137 Dateien stimmen in der Struktur überein. Die übrigen 10 lädt cantools nicht (7 × Standard-ID > 11 Bit, 2 × Name beginnt mit Ziffer, 1 × fehlendes `;`); dbc2h lädt sie.
- Tests: 672 grün auf macOS (Python 3.15), 671 auf Windows (Python 3.13); CI 10/10 Jobs grün.

**Erkenntnisse aus dem Korpus**, eingearbeitet in Parser und Lint-Tabelle (Abschnitt 5):

- Namen, die mit einer Ziffer beginnen (`2017_5`, `0_COUNTER`), sind in DBC nicht erlaubt, kommen aber vor. dbc2h liest sie mit Warnung (`W406`). Der Generator stellt einen Unterstrich voran.
- Ungültige Mux-Angaben (ein einzelnes `m`) werden unverändert erhalten, haben keine Multiplex-Bedeutung und ergeben `E111`.
- Standard-IDs über 0x7FF ohne Extended-Flag kommen in opendbc häufig vor: `E112`.
- Fehlende `;` vor dem nächsten Statement werden toleriert.
- `//`-Zeilenkommentare werden als Leerraum erhalten.
- Namen über 32 Zeichen stehen in den Attributen `System*LongSymbol`. Das Modell bietet sie als `long_name` an, der Generator verwendet sie.

**Pflichtinhalte der Benutzerdokumentation (M8):**

- `test_<prefix>.c` mit dem Produktions-Compiler und den Produktions-Flags ausführen
- Empfohlene `.gitattributes`: `*.dbc -text` und `gen/*.h text eol=lf`
- Eine neue dbc2h-Version macht alle erzeugten Header veraltet (gewollt)
- Verhalten beim Packen mit und ohne strenge Quantisierungsprüfung (3.5)
- Lint-Regeln mit IDs
- Tastenreferenz der TUI

**Risiken:**

| Risiko | Gegenmaßnahme |
|---|---|
| float-Rechnung auf echten Compilern weicht vom geprüften Modell ab (FMA, Compiler-Flags) | FMA-Schranke in der Prüfung, generierte C-Tests in der CI (auch `-ffp-contract=fast` auf arm64), Header-Guards, Dokumentationspflicht zu Produktions-Flags |
| Fremde DBC-Dateien verletzen Regeln | Unkritisches ist Warnung mit definiertem Verhalten, `*Ignore`-Attribute, toleranter Parser, latin-1 als letzte Encoding-Stufe |
| Verweise ins Leere nach Umbenennen/Löschen | Statement-Klassen mit bekannten Verweisen, Lint `E108`, Test für jeden Änderungsbefehl |
| Datenverlust (SSH-Abbruch, externe Änderung, gesperrte Datei unter Windows) | Rückfrage beim Beenden, Notfall-Speicherung bei Absturz, Änderungserkennung, `PermissionError`-Behandlung. Bei SSH-Abbruch gehen ungespeicherte Änderungen verloren (laufende Wiederherstellung zurückgestellt). |
| Textual ändert seine API (häufige Major-Releases) | Version in `vendor.lock` fest; Updates nur bewusst und abgesichert durch Interaktionstests |
| Pflege von Textual endet | Code liegt im Repo und ist MIT-lizenziert; die gepinnte Version funktioniert weiter. Der Kern (`core/`) ist unabhängig. |
| Attribut-Definitionen in CANdb++ | Ein Name pro Objekttyp, manueller CANdb++-Check |
| Motorola-Bitnummerierung | Erschöpfende Tests plus Abgleich mit cantools |

---

## 9. Umsetzung des Reviews

Was aus den Befunden in `REVIEW.md` geworden ist. Zurückgestellte Teile stehen zusätzlich in Abschnitt 10.

| Befund | Thema | Status | Abschnitt |
|---|---|---|---|
| A1 | FMA-Kontraktion | **vereinfacht:** Schranke statt exakter Nachrechnung; Header-Guards und CI-Job mit `-ffp-contract=fast` bleiben | 3.3, 4, 7 |
| A2 | Bereichsprüfung auf Rohwerten | übernommen | 3.1, 3.5, 4 |
| A3 | Leitungs- und Gültigkeitsbereich | übernommen | 3.1, 3.4 |
| A4 | Keine Enum-Felder, Wertetabellen als Konstanten | übernommen; `dbc2h_SigEnumComplete`/`ERR_ENUM` zurückgestellt | 4 |
| A5 | Ein Attributname pro Objekttyp | umgesetzt (M1) | 2.4 |
| A6 | Statement-Klassen und Verweise | umgesetzt (M1); Token-Suche in undurchsichtigen Statements zurückgestellt | 2.3 |
| A7 | Stylesheet über `importlib.resources` | umgesetzt (M0) | 2.1 |
| A8 | LF-normalisierter Hash, Bytevergleich in `--check` | übernommen | 4 |
| A9 | C++-Einbindung | übernommen | 4, 7 |
| B1 | Ganzzahlpfad | übernommen | 3.2 |
| B2 | Nur einfaches Multiplexing, ganzer Frame wird geschrieben | übernommen | 3.5, 4, 5 |
| B3 | Regel-IDs und Herabstufung | **vereinfacht:** IDs ja; unkritische Regeln sind Warnungen statt herabstufbarer Fehler | 5 |
| B4 | Python ≥ 3.13, Versionsprüfung, Kaltstart | umgesetzt (M0) | 1.1, 2.2 |
| B5 | Encoding-Stufen, bereinigte Anzeige | Encoding umgesetzt (M1), Anzeige in M3 | 1.2, 2.3 |
| B6 | Schutz vor Datenverlust | **vereinfacht:** Änderungserkennung und gesperrte Dateien umgesetzt (M1); Notfall-Speicherung nur bei Absturz | 2.3 |
| B7 | Formel-Spike in M2 | **vereinfacht:** kein Spike und keine Flag-Matrix; Prüfung gegen echtes C durch die generierten Tests in M4 | 3.3, 7 |
| B8 | Bytegleicher Round-Trip | umgesetzt (M1) | 2.3 |
| B9 | Vollständige Prüfung, `UNPROVEN` | **vereinfacht:** float vollständig bis 24 Bit; double einfache Schranke; `UNPROVEN` und Cache entfallen | 3.4 |
| C1–C6 | Korrekturen am Text | umgesetzt | 3.4, 1.3, 4, 1.1, 7 |

---

## 10. Zurückgestellt

Aus dem Plan entfernt, um 1.0 auf den Kern zu begrenzen (2026-11-20). Alles hier kann später ohne Umbau ergänzt werden; die Spalte „Wieder aufnehmen, wenn“ nennt den Anlass.

### 10.1 Numerik und Prüfung

| Feature | War geplant in | Grund für das Zurückstellen | Wieder aufnehmen, wenn |
|---|---|---|---|
| Exakte Nachrechnung der FMA-Variante (Rundungsfunktion aus Ganzzahlarithmetik) | M2 | Eine Schranke reicht für das Kriterium „Fehler ≤ ε·LSB“; Bitgleichheit mit dem Compiler ist nicht das Ziel | die Schranke Signale ablehnt, die nachweislich in Ordnung wären |
| Formelbeschreibung als Operationsliste mit zwei Backends (`formula.py`) | M2 | Es gibt nur zwei Formeln; `numerics.Conversion` als gemeinsame Quelle genügt | weitere Umrechnungsarten dazukommen (z. B. No-FPU-Modus) |
| C-Spike mit Flag-Matrix (O0/O2 × contract off/fast × c11/gnu11 × x86/arm) | M2 | Die generierten C-Tests in M4 prüfen dasselbe gezielter | in der Praxis Abweichungen auf einer bestimmten Plattform auftauchen |
| Exakte double-Schranke pro Binade, Status `UNPROVEN` | M2 | Bei double ist der Abstand praktisch immer riesig | Signale nahe 53 Bit mit nicht-dyadischem Faktor vorkommen |
| Ergebnis-Cache der Numerikprüfung | M2 | Prüfung dauert für typische Signale Millisekunden | große Dateien mit vielen 24-Bit-float-Signalen spürbar langsam sind |
| Einstufung nur über den Gültigkeitsbereich | nach 1.0 | Für 1.0 gilt die einfache Regel „jedes Bitmuster“ | Ziele mit teurem double breite Signale mit engem Min/Max brauchen |
| Property-Tests mit hypothesis | M2 | Erschöpfende Tests und feste Fälle reichen | |

### 10.2 Lint

| Feature | War geplant in | Grund | Wieder aufnehmen, wenn |
|---|---|---|---|
| Severity-Overrides per `dbc2h_Lint` und Liste nicht herabstufbarer Regeln | M2 | Unkritisches ist ohnehin Warnung; `*Ignore` reicht für den Rest | OEM-Dateien trotzdem an Fehlern hängen, die man nicht ignorieren will |
| W401 Signal ohne Empfänger, W402 Nachricht ohne Sender, W403 Wertetabelle außerhalb des Leitungsbereichs, W404 ungenutzte Wertetabelle, W405 fehlende Zykluszeit, I503 Namenskonventionen | M2 | Komfort, verhindert keinen falschen Code | |
| Warnung bei alten Bezeichnern in undurchsichtigen Statements nach Umbenennen | M5 | sehr selten | |

### 10.3 Generator

| Feature | War geplant in | Grund | Wieder aufnehmen, wenn |
|---|---|---|---|
| `_Generic`-Makros `pt_pack`/`pt_unpack` (nur in C) | M7 | Komfort, die typisierten Funktionen reichen | |
| Nachrichtentabelle und `pt_unpack_by_id()` hinter `PT_IMPLEMENTATION` | M7 | nützlich für Gateways und Logger, aber nicht für den Kern | ein Gateway oder Logger gebaut wird |
| split-Modus (`.h` + `.c`), Attribut `dbc2h_OutputMode` | M7 | header-only reicht | große Netze die Compile-Zeit spürbar belasten |
| `dbc2h_SigEnumComplete` und `ERR_ENUM` | M7 | Wertetabellen sind meist unvollständig; Bereichsprüfung reicht | |
| Fuzz-Round-Trip mit 10⁵ Zufallsframes pro Nachricht | M7 | Die generierten Tests mit Grenz- und Zufallswerten decken das ab | |

### 10.4 TUI

| Feature | War geplant in | Grund | Wieder aufnehmen, wenn |
|---|---|---|---|
| Dunkles Theme | M3 | ein Theme reicht | jemand danach fragt |
| Laufende Wiederherstellungsdatei (nach jeder Änderung), Behandlung von `SIGHUP`/`SIGTERM` | M3 | Notfall-Speicherung bei Absturz plus Rückfrage beim Beenden reichen für 1.0 | SSH-Abbrüche in der Praxis zu Datenverlust führen |
| Snapshot-Tests (`pytest-textual-snapshot`) für jeden Screen in zwei Größen | M3 | hoher Pflegeaufwand bei jedem Textual-Update; Interaktionstests reichen | |
| Manuelle Prüfung auf 8 Terminals (conhost, iTerm2, gnome-terminal, xterm, tmux, …) | M3 | Windows Terminal (lokal und über SSH) und macOS Terminal decken den Großteil ab | Fehlerberichte für ein bestimmtes Terminal kommen |
| Eigene Taste `/` neben der Befehlspalette | M5 | beide öffnen jetzt dieselbe Suche | |
| Editor für Wertetabellen (`VAL_TABLE_`) | M5 | Wertbeschreibungen werden direkt am Signal gepflegt; vorhandene Tabellen bleiben erhalten | Tabellen über viele Signale geteilt gepflegt werden sollen |
| Editor für Attribut-Definitionen und beliebige Attributwerte | M5 | dbc2h-Einstellungen und Zykluszeit haben eigene Felder; fremde Attribute bleiben erhalten | andere Attribute (z. B. `GenMsgSendType`) gepflegt werden sollen |
| Baudrate (`BS_`) und CAN-FD-Schalter in den Netzwerk-Einstellungen | M5 | `BS_` ist veraltet; CAN FD ergibt sich aus den DLCs | |
| Signal per Maus verschieben im Bit-Layout | M6 | Tastatur reicht und ist leichter zu testen | |
| Syntax-Highlighting in der Header-Vorschau | M5 | Komfort | |

### 10.5 Ideen nach 1.0

- Erweitertes Multiplexing im Generator
- Semantischer **Diff** zweier DBC-Versionen mit API-Break-Erkennung (in der TUI als Vergleichsansicht)
- Log-Decoding (candump, Vector ASC) mit Export nach CSV
- Kommunikationsmatrix als Markdown/HTML
- Helfer für Rolling Counter und CRC/E2E
- Merge mehrerer DBCs
- Raw-/No-FPU-Modus (nur Ganzzahl-Structs oder skalierte Ganzzahlen)
- Umwandlung einer Datei nach UTF-8 als eigene Aktion
- Vorkompilierter Bytecode in der `.pyz`, falls die Startzeit wächst
