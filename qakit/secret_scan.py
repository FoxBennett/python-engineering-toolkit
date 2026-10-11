"""Find credentials before they reach a commit.

Why this exists
---------------
A secret that lands in git is not removed by deleting the line in a later
commit: it stays in the history, in every clone, and in any fork. The only
cheap moment to catch it is before it is committed.

This scans the files that could end up in a commit - tracked *and* untracked,
but not ignored - plus any extra directories you point it at (runtime logs are
a common place for tokens to leak).

Two properties matter more than coverage:

**It never prints the secret.** Output shows a masked form: the first six
characters and the length. That is enough to locate the value and not enough
to use it. A scanner that echoes the credential into your terminal or CI log
has moved the leak, not fixed it.

**It distinguishes "looks like a secret" from "is shaped like one".** Real
credentials are long and mixed-case or digit-heavy. Model names, environment
variable references, `getenv(...)` calls and obvious placeholders match the
naive patterns but are not secrets. Reporting those trains people to ignore
the tool.

Usage
-----
    python -m qakit.secret_scan --root .
    python -m qakit.secret_scan --root . --json
    python -m qakit.secret_scan --root . --vault "config.py" --extra-dir logs

Exit code is 1 if a probable credential was found outside a known vault,
0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

#: (label, compiled pattern). Each pattern's last group is the candidate value.
PATTERNS = (
    ("sk- style key", re.compile(r"sk-[A-Za-z0-9_\-]{12,}")),
    ("bearer token", re.compile(r"bearer\s+[A-Za-z0-9._\-]{12,}", re.IGNORECASE)),
    (
        "assigned value",
        re.compile(
            r"\b(api[_-]?key|access[_-]?key|token|secret|password)\b\s*[=:]\s*[\"']?"
            r"([A-Za-z0-9_\-]{16,})",
            re.IGNORECASE,
        ),
    ),
)

#: Files that are *expected* to hold credentials. Hits here are reported
#: separately and never fail the build, because that is where secrets belong.
DEFAULT_VAULT_SUFFIXES = ("config.py", ".env", ".env.local", "secrets.yaml")

DEFAULT_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "build", "dist",
    ".ruff_cache", ".pytest_cache", ".mypy_cache",
}

TEXT_SUFFIXES = {
    ".py", ".pyi", ".md", ".txt", ".json", ".jsonl", ".yml", ".yaml", ".toml",
    ".cfg", ".ini", ".bat", ".cmd", ".ps1", ".sh", ".env", ".properties",
    ".js", ".ts", ".html", ".css", ".xml",
}

#: Context words that mean the match is almost certainly not a live secret.
NOISE_WORDS = (
    "vosk", "environ", "getenv", "os.environ", "example", "placeholder",
    "your_", "xxxx", "<your", "changeme", "dummy", "sample", "redacted",
)

#: Anything shorter than this is not treated as a probable credential.
MIN_SECRET_LENGTH = 20


def mask(token: str) -> str:
    """Return a form that is enough to locate the value and not enough to use it."""
    if len(token) <= 8:
        return "<masked>"
    return "%s... (%d chars)" % (token[:6], len(token))


def looks_like_secret(token: str, line: str) -> bool:
    """Separate plausible credentials from things merely shaped like one.

    A real credential is long enough, and either mixed case or digit-heavy.
    Excluded: environment variable *names* (all upper case), references such as
    `os.getenv(...)`, model identifiers, and obvious placeholders.
    """
    if len(token) < MIN_SECRET_LENGTH:
        return False

    lowered = line.lower()
    if any(word in lowered for word in NOISE_WORDS):
        return False

    if token.isupper():
        # A NAME_IN_CAPS is a variable name, not a literal secret.
        return False

    digits = sum(character.isdigit() for character in token)
    has_upper = any(character.isupper() for character in token)
    has_lower = any(character.islower() for character in token)
    return (has_upper and has_lower) or digits >= 6


def candidate_files(root: Path) -> list[Path]:
    """Files that could enter a commit: tracked plus untracked-but-not-ignored.

    Uses `--cached --others --exclude-standard` rather than `--cached` alone.
    Scanning only tracked files misses the most dangerous case - a key that was
    written five minutes ago and has not been committed yet. Ignored files stay
    out of scope by design; put them in the vault list to have them reported.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [root / line for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        # No git, or not a repository. Fall back to walking the tree: slower,
        # but it still produces an answer.
        pass

    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in DEFAULT_SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
    return files


def scan_file(path: Path, root: Path, vault_suffixes: tuple[str, ...]) -> list[dict]:
    """Return masked findings for one file. Never includes raw values."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)

    normalised = rel.replace("\\", "/")
    in_vault = any(normalised.endswith(suffix) for suffix in vault_suffixes)

    hits: list[dict] = []
    for name, pattern in PATTERNS:
        for match in pattern.finditer(text):
            token = match.group(match.lastindex or 0)

            # For "bearer <token>", judge the token, not the word "Bearer".
            # Otherwise every `Bearer TEST-...` fixture reads as mixed case.
            if name == "bearer token" and " " in token:
                token = token.split(None, 1)[-1]

            line_number = text[: match.start()].count("\n") + 1
            lines = text.splitlines()
            line = lines[line_number - 1] if 0 < line_number <= len(lines) else ""

            hits.append({
                "file": normalised,
                "line": line_number,
                "kind": name,
                "masked": mask(token),
                "vault": in_vault,
                "suspicious": looks_like_secret(token, line),
            })
    return hits


def extra_files(directories: list[str], root: Path) -> list[Path]:
    """Files under directories the caller asked for explicitly (logs, etc.)."""
    out: list[Path] = []
    for entry in directories:
        base = (root / entry).resolve()
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                out.append(path)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find probable credentials without printing them.")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--vault", action="append", default=None,
                        help="suffix expected to hold secrets; reported but never fails "
                             "(repeatable). Defaults to config.py, .env, .env.local, secrets.yaml")
    parser.add_argument("--extra-dir", action="append", default=None,
                        help="additional directory to scan, e.g. logs (repeatable)")
    parser.add_argument("--json", action="store_true", help="machine-readable output, still masked")
    parser.add_argument("--verbose", action="store_true",
                        help="also list matches judged to be ordinary text")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    vault_suffixes = tuple(args.vault) if args.vault else DEFAULT_VAULT_SUFFIXES

    targets = candidate_files(root)
    extras = extra_files(args.extra_dir or [], root)
    targets = targets + extras

    hits: list[dict] = []
    for path in targets:
        if path.is_file():
            hits.extend(scan_file(path, root, vault_suffixes))

    leaks = [h for h in hits if not h["vault"] and h["suspicious"]]
    notes = [h for h in hits if not h["vault"] and not h["suspicious"]]
    vault = [h for h in hits if h["vault"]]

    if args.json:
        print(json.dumps({
            "root": str(root),
            "scanned": len(targets),
            "extra_scanned": len(extras),
            "leaks": leaks,
            "notes": notes,
            "vault": vault,
        }, ensure_ascii=False, indent=2))
        return 1 if leaks else 0

    print("secret-scan: %s" % root)
    print("  files scanned        : %d" % len(targets))
    print("  from --extra-dir     : %d" % len(extras))
    print("  expected vault hits  : %d" % len(vault))
    print("  probable leaks       : %d" % len(leaks))
    print("  shaped-like-a-secret : %d  (judged ordinary text)" % len(notes))

    if vault:
        print("")
        print("  In a known vault (expected - this is where secrets belong):")
        for item in vault:
            print("    - %s:%d  %s  %s" % (item["file"], item["line"], item["kind"], item["masked"]))

    if leaks:
        print("")
        print("  PROBABLE LEAK - a credential appears outside a vault:")
        for item in leaks:
            print("    - %s:%d  %s  %s" % (item["file"], item["line"], item["kind"], item["masked"]))
        print("")
        print("  Rotate the credential first, then clean the history. Removing the")
        print("  line in a new commit does not remove it from existing clones.")
        return 1

    print("")
    print("  No probable credentials found.")
    if args.verbose:
        for item in notes:
            print("    ~ %s:%d  %s  %s" % (item["file"], item["line"], item["kind"], item["masked"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
