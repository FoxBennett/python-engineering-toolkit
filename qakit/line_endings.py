"""Check line-ending conventions and markdown fence pairing.

Why this exists
---------------
Line endings are the classic "rule that lives in a config file and is never
enforced". A repository ends up with a few dozen files that disagree with its
own convention - often including the README on the front page - and nobody
notices, because nothing fails.

This turns the convention into a number that shows up on every commit. It does
not rewrite files: it reports the count and the names.

It also checks a second, less obvious thing: **unbalanced markdown code
fences**. Dropping a paragraph into a document and forgetting to close the
previous fence silently renders the rest of the file as prose. Nothing else
catches that.

Usage
-----
    python -m qakit.line_endings --root .
    python -m qakit.line_endings --root . --list

Exit code is 1 if anything was reported, 0 otherwise.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

#: File types worth checking. Anything binary is skipped by omission.
TEXT_SUFFIXES = (
    ".py", ".pyi", ".md", ".rst", ".txt", ".json", ".jsonl", ".yml", ".yaml",
    ".toml", ".cfg", ".ini", ".bat", ".cmd", ".ps1", ".vbs", ".sh",
    ".css", ".scss", ".html", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
)

#: Patterns that should use CRLF regardless of the default.
DEFAULT_CRLF_PATTERNS = ("*.bat", "*.cmd", "*.ps1", "*.vbs")

#: Patterns that are reported but never required to change. Use this for
#: vendored or read-only areas where "fixing" the endings would be wrong.
DEFAULT_REPORT_ONLY_PATTERNS: tuple[str, ...] = ()

VERDICT_REASON = {
    "crlf": "should be LF but the whole file is CRLF",
    "lf": "should be CRLF but the file is LF",
    "mixed": "CRLF and LF mixed in one file",
    "no-final-newline": "missing newline at end of file",
}


def wants_crlf(rel: str, patterns: tuple[str, ...]) -> bool:
    """True if this path should use CRLF."""
    name = rel.replace("\\", "/").rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def is_report_only(rel: str, patterns: tuple[str, ...]) -> bool:
    """True if violations here should be reported but not treated as failures."""
    normalised = rel.replace("\\", "/")
    return any(fnmatch.fnmatch(normalised, pattern) for pattern in patterns)


def classify(rel: str, raw: bytes, crlf_patterns: tuple[str, ...]) -> str:
    """Return ok / crlf / lf / mixed / no-final-newline for one file."""
    if not raw:
        return "ok"

    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf

    if crlf and lf:
        return "mixed"

    if wants_crlf(rel, crlf_patterns):
        if lf and not crlf:
            return "lf"
    elif crlf and not lf:
        return "crlf"

    if not raw.endswith(b"\n"):
        return "no-final-newline"

    return "ok"


def tracked_files(root: Path) -> list[str]:
    """List git-tracked files, or an empty list if this is not a git repo."""
    try:
        done = subprocess.run(
            ["git", "ls-files"], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    except Exception:
        return []
    if done.returncode != 0:
        return []
    return [line for line in done.stdout.splitlines() if line.strip()]


def walk_files(root: Path) -> list[str]:
    """Fallback for when git is unavailable: walk the tree instead."""
    skip = {"__pycache__", ".git", ".venv", "venv", "node_modules", "build", "dist"}
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in skip for part in path.parts):
            continue
        try:
            out.append(str(path.relative_to(root)).replace("\\", "/"))
        except ValueError:
            # Not underneath root - a symlink pointing into another tree.
            # There is no meaningful repo-relative name for it, so skip it.
            continue
    return out


def scan(root: Path, crlf_patterns: tuple[str, ...], report_only: tuple[str, ...]) -> dict:
    """Return the counts and name lists. Pure: prints nothing, raises nothing."""
    files = tracked_files(root)
    source = "git"
    if not files:
        files = walk_files(root)
        source = "walk"

    problems: list[tuple[str, str]] = []
    report_only_hits: list[str] = []
    checked = 0

    for rel in files:
        if not rel.lower().endswith(TEXT_SUFFIXES):
            continue
        path = root / rel
        try:
            if not path.is_file():
                continue
            raw = path.read_bytes()
        except OSError:
            # Unreadable (deleted mid-run, permissions). Increment happens
            # after this point, so skipped files are not counted as clean.
            continue

        checked += 1
        verdict = classify(rel, raw, crlf_patterns)
        if verdict == "ok":
            continue

        if is_report_only(rel, report_only):
            report_only_hits.append("%s (%s)" % (rel, verdict))
        else:
            problems.append((rel, verdict))

    return {
        "source": source,
        "checked": checked,
        "problems": problems,
        "report_only": report_only_hits,
        "files": files,
    }


def scan_fences(root: Path, files: list[str]) -> list[tuple[str, int]]:
    """Return markdown files whose code-fence count is odd.

    An odd count means at least one fence is unclosed, so everything after it
    renders as prose instead of code.
    """
    broken: list[tuple[str, int]] = []
    for rel in files:
        if not rel.lower().endswith((".md", ".markdown")):
            continue
        path = root / rel
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # Unreadable markdown file. The line-ending pass has already
            # reported it, so there is nothing extra to say here.
            continue
        count = sum(1 for line in text.splitlines() if line.lstrip().startswith("```"))
        if count % 2:
            broken.append((rel, count))
    return broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check line endings and markdown fences.")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--crlf", action="append", default=None,
                        help="glob that should use CRLF (repeatable). "
                             "Defaults to *.bat, *.cmd, *.ps1, *.vbs")
    parser.add_argument("--report-only", action="append", default=None,
                        help="glob to report but never fail on (repeatable)")
    parser.add_argument("--list", action="store_true", help="print every finding")
    parser.add_argument("--limit", type=int, default=8, help="how many to print (default 8)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    crlf_patterns = tuple(args.crlf) if args.crlf else DEFAULT_CRLF_PATTERNS
    report_only = tuple(args.report_only) if args.report_only else DEFAULT_REPORT_ONLY_PATTERNS

    result = scan(root, crlf_patterns, report_only)
    if not result["checked"]:
        print("line-endings: no text files found under %s - nothing to do" % root)
        return 0

    problems = result["problems"]
    print("line-endings: checked %d file(s) via %s" % (result["checked"], result["source"]))
    print("  files disagreeing with the convention : %d" % len(problems))

    for rel, verdict in problems[:max(0, args.limit)]:
        print("    - %s: %s" % (VERDICT_REASON.get(verdict, verdict), rel))
    if len(problems) > args.limit:
        print("      ... and %d more (use --list)" % (len(problems) - args.limit))

    if result["report_only"]:
        print("  report-only areas (never failed on): %d" % len(result["report_only"]))
        for item in result["report_only"][:3]:
            print("    · %s" % item)

    fences = scan_fences(root, result["files"])
    print("  markdown files with an unclosed fence  : %d" % len(fences))
    for rel, count in fences[:max(0, args.limit)]:
        print("    - %d fences (odd) in %s" % (count, rel))
    if fences:
        print("      An odd fence count means the rest of the file renders as prose.")

    if args.list:
        for rel, verdict in problems:
            print("  eol     %s: %s" % (VERDICT_REASON.get(verdict, verdict), rel))
        for rel, count in fences:
            print("  fence   %s: %d" % (rel, count))

    return 1 if (problems or fences) else 0


if __name__ == "__main__":
    raise SystemExit(main())
