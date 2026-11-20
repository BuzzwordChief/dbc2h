# Entry point for "python -m dbc2h" and for the launcher inside dbc2h.pyz.
# Keep this file parseable by old interpreters: it must be able to report
# an unsupported Python version instead of failing with a SyntaxError.
import sys

from dbc2h import MIN_PYTHON


def main(argv=None):
    if sys.version_info < MIN_PYTHON:
        sys.stderr.write(
            "dbc2h benötigt Python %d.%d oder neuer (gefunden: %d.%d).\n"
            % (MIN_PYTHON + tuple(sys.version_info[:2]))
        )
        return 1
    from dbc2h.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
