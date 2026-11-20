# Launcher at the root of dbc2h.pyz. The build copies this file to __main__.py.
# Keep it parseable by old interpreters (see dbc2h/__main__.py).
#
# The vendored packages sit at the root of the archive. Running "python dbc2h.pyz"
# puts the archive first on sys.path, so they take precedence over installed copies,
# and importlib.metadata finds their dist-info (it does not search subdirectories
# of a zip file).
import sys

from dbc2h.__main__ import main

sys.exit(main())
