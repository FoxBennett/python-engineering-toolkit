"""Run every check in one command and report a single verdict.

Why this exists
---------------
Checks that require remembering four separate commands do not get run. This
turns them into one command with one exit code, which is what a pre-commit
hook or a CI step actually needs.

    python -m qakit.precommit --root .

Each check runs in a subprocess so that one crash cannot take the others down,
and every check runs even after an earlier one fails - fixing four problems in
four runs is worse than seeing all four at once.

Exit code is 1 if any check reported findings, 0 otherwise.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

#: (module, human label). Order is chosen so the fastest failure appears first.
CHECKS = (
    ("qakit.secret_scan", "credentials"),
    ("qakit.silent_except", "silent failures"),
    ("qakit.line_endings", "conventions"),
)


def run_check(module: str, root: Path, extra: list[str] | None = None) -> tuple[int, float, str]:
    """Run one check as a subprocess. Returns (exit code, seconds, output tail)."""
    started = time.time()
    try:
        done = subprocess.run(
            [sys.executable, "-m", module, "--root", str(root), *(extra or [])],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600,
        )
        elapsed = time.time() - started
        return done.returncode, elapsed, (done.stdout or done.stderr or "").rstrip()
    except subprocess.TimeoutExpired:
        return 1, time.time() - started, "timed out after 600s"
    except Exception as exc:
        return 1, time.time() - started, "could not run: %s" % exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run every check and report one verdict.")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--quiet", action="store_true",
                        help="only print checks that found something")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    print("precommit: %s" % root)
    print("")

    failed: list[str] = []
    for module, label in CHECKS:
        code, elapsed, output = run_check(module, root)
        status = "PASS" if code == 0 else "FINDINGS"
        if code != 0:
            failed.append(label)

        if args.quiet and code == 0:
            print("  %-9s %-18s %5.2fs" % (status, label, elapsed))
            continue

        print("  %-9s %-18s %5.2fs" % (status, label, elapsed))
        if output:
            for line in output.splitlines():
                print("      " + line)
        print("")

    print("-" * 62)
    if failed:
        print("  %d check(s) reported findings: %s" % (len(failed), ", ".join(failed)))
        print("  Nothing was changed - these tools only report.")
        return 1

    print("  All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
