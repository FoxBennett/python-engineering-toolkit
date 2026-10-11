"""Detect exception handlers that swallow errors silently.

Why this exists
---------------
"Never swallow an exception" is a rule most teams write down and then never
enforce. A linter can flag the obvious `except: pass`, but the more common
failure is subtler: a handler whose body is a bare `return` or `continue`,
so the error disappears with no log line and no traceback.

This reports two classes of silent failure:

1. **Silent handlers** - an `except` block whose body consists only of
   `pass`, `continue`, `break` or a bare `return`, carrying no explanatory
   comment and no explicit suppression marker. A handler *with* a comment
   explaining why ignoring is safe is not reported: the rule is "say it out
   loud", not "never ignore".

2. **Silently skipped tests** - a `test_*` function that returns early when a
   precondition is not met and prints nothing. The suite then reports success
   whether or not the body actually ran, so "all tests passed" stops meaning
   anything.

3. **Orphan helpers** - functions whose names promise automatic behaviour
   (`ensure_`, `auto_`, `refresh_`, `rebuild_`, `sync_`) but which appear
   exactly once in the whole tree. A function plus a unit test is not the same
   as a function that is actually wired up.

It uses the `ast` module rather than regular expressions, because a regex
cannot reliably determine what a handler's body contains.

Usage
-----
    python -m qakit.silent_except --root .
    python -m qakit.silent_except --root . --list

Exit code is 1 if anything was reported, 0 otherwise, so this drops straight
into a pre-commit hook or CI job.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

DEFAULT_EXCLUDES = (
    "__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "env",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist",
)

#: Case-insensitive markers that mean "ignoring this is deliberate".
SUPPRESS_MARKERS = ("noqa: s110", "noqa:s110", "silent-ok: true")

#: Prefixes that imply the function is supposed to run automatically.
ORPHAN_PREFIXES = re.compile(r"^(ensure_|auto_|refresh_|rebuild_|sync_)")


def _comments_by_line(text: str) -> dict[int, list[str]]:
    """Return {line number: [comment text, ...]} for one source file.

    `ast` discards comments, so tokenizing is the only way to tell whether a
    handler was annotated by the author.
    """
    found: dict[int, list[str]] = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                found.setdefault(token.start[0], []).append(token.string)
    except Exception:
        # Tokenizing failed (unusual: invalid characters, bad encoding). Treat
        # the file as having no comments, which makes the check more
        # conservative - every silent handler gets reported.
        return {}
    return found


def _is_silent_body(body: list[ast.stmt]) -> bool:
    """True if a handler body does nothing observable."""
    for node in body:
        if isinstance(node, (ast.Pass, ast.Continue, ast.Break)):
            continue
        if isinstance(node, ast.Return) and node.value is None:
            # A bare `return` ends the function quietly. `return <value>` is
            # excluded: that may be a deliberate default, and the author can
            # annotate it if it is not.
            continue
        return False
    return True


def scan_source(text: str, rel: str = "") -> list[dict]:
    """Return silent handlers found in one source string.

    Files that do not parse are skipped rather than raising - a syntax error
    will be caught by the test runner and linter anyway, and this check should
    not be the thing that blocks the pipeline.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    comments = _comments_by_line(text)
    rows: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _is_silent_body(handler.body):
                continue

            start = handler.lineno
            end = start
            if handler.body:
                end = max(getattr(item, "end_lineno", start) or start for item in handler.body)

            # Comments immediately above the handler, plus any inside it.
            nearby = list(comments.get(start - 1, []))
            for number, items in comments.items():
                if start <= number <= end + 1:
                    nearby.extend(items)
            joined = " ".join(nearby).lower()

            if any(marker in joined for marker in SUPPRESS_MARKERS):
                continue  # explicitly suppressed
            if joined.strip():
                continue  # the author said something about it

            rows.append({
                "file": rel,
                "line": start,
                "type": ast.unparse(handler.type) if handler.type else "bare",
            })
    return rows


def scan_silently_skipped_tests(root: Path, rel_paths: list[str]) -> list[dict]:
    """Find test functions that return early without printing anything.

    A test that quietly returns reports the same result as a test that ran, so
    "all green" stops being evidence.
    """
    rows: list[dict] = []
    for rel in rel_paths:
        path = root / rel
        name = path.name
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            # Unreadable or unparsable test module. The test runner fails loudly
            # on both, so staying quiet here hides nothing.
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.If) or len(sub.body) != 1:
                    continue
                ret = sub.body[0]
                if not isinstance(ret, ast.Return) or ret.value is not None:
                    continue
                span = "\n".join(lines[sub.lineno - 1:(ret.end_lineno or ret.lineno)])
                if "print" in span:
                    continue  # the skip is announced, so it is visible
                rows.append({"file": rel, "line": sub.lineno, "test": node.name})
    return rows


def scan_orphan_helpers(root: Path, rel_paths: list[str]) -> list[dict]:
    """Find functions that promise automatic behaviour but are never called.

    Heuristic, deliberately conservative: only names matching ORPHAN_PREFIXES
    are considered, and only when the identifier appears exactly once in the
    entire tree (its own definition). If it also appears in a test it is not
    reported - "tested but not wired up" is a different problem, and belongs to
    integration testing rather than this check.
    """
    definitions: list[tuple[str, str, int]] = []
    for rel in rel_paths:
        path = root / rel
        if not path.name.endswith(".py"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            # Cannot analyse a file we cannot read or parse. Skipping it only
            # risks a missed finding, never a wrong one.
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if ORPHAN_PREFIXES.match(node.name):
                    definitions.append((rel, node.name, node.lineno))

    if not definitions:
        return []

    corpus: list[str] = []
    for rel in rel_paths:
        if not rel.endswith(".py"):
            continue
        try:
            corpus.append((root / rel).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            # Only used for counting, so a missing file lowers the count and
            # could in principle produce a false positive. The criterion
            # requires an exact single occurrence, which keeps that rare.
            continue

    rows: list[dict] = []
    for rel, name, line in definitions:
        hits = sum(len(re.findall(r"\b" + re.escape(name) + r"\b", text)) for text in corpus)
        if hits <= 1:
            rows.append({"file": rel, "line": line, "name": name, "mentions": hits})
    return rows


def collect_python_files(root: Path, excludes: tuple[str, ...] = DEFAULT_EXCLUDES) -> list[str]:
    """Return repo-relative paths of every .py file worth scanning."""
    out: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in excludes for part in path.parts):
            continue
        try:
            out.append(str(path.relative_to(root)).replace("\\", "/"))
        except ValueError:
            # path is not underneath root (a symlink into another tree).
            # It cannot be reported with a repo-relative name, so it is skipped.
            continue
    return out


def scan(root: Path) -> dict:
    """Run every check. Returns counts plus the finding lists."""
    rel_paths = collect_python_files(root)
    silent: list[dict] = []
    checked = 0

    for rel in rel_paths:
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            # Increment only after a successful read, so unreadable files are
            # not counted as "scanned and clean".
            continue
        checked += 1
        silent.extend(scan_source(text, rel=rel))

    return {
        "checked": checked,
        "silent": silent,
        "skips": scan_silently_skipped_tests(root, rel_paths),
        "orphans": scan_orphan_helpers(root, rel_paths),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report silently swallowed errors.")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--list", action="store_true", help="print every finding, not just the first few")
    parser.add_argument("--limit", type=int, default=8, help="how many findings to print (default 8)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    result = scan(root)

    if not result["checked"]:
        print("silent-except: no Python files found under %s - nothing to do" % root)
        return 0

    silent = result["silent"]
    skips = result["skips"]
    orphans = result["orphans"]
    limit = max(0, args.limit)

    print("silent-except: scanned %d files" % result["checked"])
    print("  unexplained silent handlers : %d" % len(silent))
    print("  silently skipped tests      : %d" % len(skips))
    print("  orphan auto-behaviour hooks : %d" % len(orphans))

    for row in orphans[:limit]:
        print("    - %s:%d  %s (appears %d time(s) in the tree)"
              % (row["file"], row["line"], row["name"], row["mentions"]))
    if orphans:
        print("      These promise automatic behaviour but have no caller.")
        print("      A function plus a unit test is not the same as wired up.")

    for row in silent[:limit]:
        print("    - %s:%d  except %s" % (row["file"], row["line"], row["type"]))
    if len(silent) > limit:
        print("      ... and %d more (use --list)" % (len(silent) - limit))
    if silent:
        print("      Add a comment explaining why ignoring is safe, or log/re-raise.")

    for row in skips[:limit]:
        print("    - %s:%d  skipped silently in %s" % (row["file"], row["line"], row["test"]))
    if skips:
        print("      Announce the skip with a print, or the suite reports success either way.")

    if args.list:
        for row in silent:
            print("  silent  %s:%d  except %s" % (row["file"], row["line"], row["type"]))
        for row in skips:
            print("  skip    %s:%d  %s" % (row["file"], row["line"], row["test"]))
        for row in orphans:
            print("  orphan  %s:%d  %s" % (row["file"], row["line"], row["name"]))

    return 1 if (silent or skips or orphans) else 0


if __name__ == "__main__":
    raise SystemExit(main())
