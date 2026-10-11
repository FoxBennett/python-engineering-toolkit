"""Quality checks that make written engineering rules enforceable.

Each module is a standalone check with a CLI entry point and a process exit
code, so it can run from a pre-commit hook or a CI job without extra wiring.

    python -m qakit.silent_except --root .
    python -m qakit.line_endings  --root .
    python -m qakit.secret_scan   --root .

`quiet_launch` is a utility rather than a check: it starts desktop
applications without stealing the foreground window.

Every module uses the standard library only.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
