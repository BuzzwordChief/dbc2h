# DBC test corpus

Real-world and edge-case DBC files for parser and writer tests. Used unchanged.

| Directory | Source | Commit | License |
|---|---|---|---|
| `opendbc/` | https://github.com/commaai/opendbc, `opendbc/dbc/*.dbc` (files under 260 KB, without `generator/`) | `79ddcc7bb72d557fc5b20d038c0fad9c9d423446` | MIT, see `opendbc/LICENSE` |
| `cantools/` | https://github.com/cantools/cantools, `tests/files/dbc/` | `a2a35a63aa82c6ce37015f4e7d8de1d8719770b2` | MIT, see `cantools/LICENSE` |

These files must keep their exact bytes (line endings, encoding). `.gitattributes`
marks them as binary for that reason.
