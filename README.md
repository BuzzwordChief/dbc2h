# dbc2h

Werkzeug zum Erstellen, Verwalten und Exportieren von DBC-Dateien als C11-Header.
Plan und Meilensteine: [PLAN.md](PLAN.md).

Stand: **M2 (Layout, Numerik, Lint)**. Der Kern liest und schreibt DBC-Dateien verlustfrei und prüft sie (`dbc2h check`); die TUI zeigt bisher nur eine Startseite.

## Benutzen

Voraussetzung ist Python 3.13 oder neuer, sonst nichts.

```
python dbc2h.pyz                  # TUI starten
python dbc2h.pyz powertrain.dbc   # TUI mit Datei
python dbc2h.pyz --version        # Version und gebündelte Bibliotheken
python dbc2h.pyz check net.dbc    # Prüfen (Lint, Numerik); --signals listet jedes Signal
```

## Entwickeln

```
python tools/dev_env.py           # .venv mit gebündelten Bibliotheken und Testwerkzeugen
source .venv/bin/activate         # Windows: .venv\Scripts\activate
python -m pytest                  # Tests (inkl. Build und Test der .pyz in leerer Umgebung)
python -m dbc2h                   # aus dem Quellbaum starten
python tools/build_pyz.py         # dist/dbc2h.pyz bauen
```

### Gebündelte Bibliotheken

Laufzeit-Abhängigkeiten stehen in `vendor/requirements.in`. Die Wheels liegen in
`vendor/wheels/`, Versionen, Hashes und Lizenzen in `vendor/vendor.lock`.

```
python tools/vendor.py lock       # nach Änderung an requirements.in (braucht Netz und pip)
python tools/vendor.py verify     # offline prüfen (macht der Build automatisch)
python tools/vendor.py sync       # fehlende Wheels laut Lock nachladen
```

Erlaubt sind nur reine Python-Wheels (`py3-none-any`) unter MIT, BSD-2/3-Clause,
PSF-2.0, Apache-2.0 oder ISC. Alles andere lässt Lock, Verify und Build scheitern.
Die Lizenztexte landen als `THIRD_PARTY_LICENSES.txt` in der `.pyz`.
