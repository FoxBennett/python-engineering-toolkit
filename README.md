# Python Engineering Quality Checks

Four small tools that turn **written rules into enforceable numbers**.

Most teams have a convention document. Almost none of them have anything that
reads it. The result is a repository that disagrees with its own rules — files
with the wrong line endings, exception handlers that swallow errors without a
word, tests that report success whether or not they ran — and nobody finds out
until it matters.

These tools only report. They never rewrite your files. The point is to make
the number visible on every commit, so drift gets caught in a pull request
instead of during an incident.

**Zero third-party dependencies.** Standard library only, so they drop into any
project — no install step, no version conflicts, nothing to keep updated.

---

## The tools

| Module | What it does |
| :--- | :--- |
| `silent_except` | Finds exception handlers that discard errors with no log, no re-raise and no explanation. Also flags test functions that `return` early without printing, and functions named `ensure_*` / `auto_*` / `sync_*` that nothing ever calls. |
| `secret_scan` | Finds credentials before they reach a commit. **Never prints the secret** — output is masked. Separates "is shaped like a key" from "is a key". |
| `line_endings` | Finds files that disagree with the line-ending convention, mixed CRLF/LF, missing final newlines, and markdown files with an unclosed code fence. |
| `precommit` | Runs all of the above in one command and reports a single verdict. |
| `quiet_launch` | Utility, not a check: starts desktop applications on Windows **without stealing the foreground window**. |

---

## Quick start

```bash
git clone <this repo>
cd python-engineering-toolkit

# one command, one verdict
python -m qakit.precommit --root /path/to/project

# or run a single check
python -m qakit.secret_scan   --root /path/to/project
python -m qakit.silent_except --root /path/to/project
python -m qakit.line_endings  --root /path/to/project

# against the bundled examples, which contain deliberate violations
python -m qakit.precommit --root example/fixtures
```

Every entry point exits **1** when it finds something and **0** when it does
not, so they work as a pre-commit hook or a CI step with no glue code.

---

## Example output

```
$ python -m qakit.precommit --root example/fixtures
precommit: /home/dev/project/example/fixtures

  FINDINGS  credentials         0.11s
      secret-scan: /home/dev/project/example/fixtures
        files scanned        : 7
        probable leaks       : 3
        PROBABLE LEAK - a credential appears outside a vault:
          - leaked_key.py:11  sk- style key  sk-FAK... (29 chars)
          - leaked_key.py:14  bearer token  FAKE-a... (24 chars)

  FINDINGS  silent failures     0.12s
      silent-except: scanned 7 files
        unexplained silent handlers : 2
          - silent_handler.py:7  except OSError

  FINDINGS  conventions         0.10s
      line-endings: checked 8 file(s)
        files disagreeing with the convention : 3
          - CRLF and LF mixed in one file: mixed_endings.py

--------------------------------------------------------------
  3 check(s) reported findings: credentials, silent failures, conventions
  Nothing was changed - these tools only report.
```

Note what the credential output contains: `sk-FAK... (29 chars)`. Enough to
find the value, not enough to use it. A scanner that echoes the key into your
terminal or CI log has moved the leak rather than fixed it.

---

## This repository passes its own checks

Running the checks against their own source is part of the workflow:

```
$ python -m qakit.precommit --root qakit
  PASS      credentials         0.11s
  PASS      silent failures     0.12s
  PASS      conventions         0.11s
  All checks passed.
```

It was not always like this.

The first run of `silent_except` flagged **five handlers in these modules** —
every one a "read failed, carry on" path with no comment saying why carrying on
was safe. That is exactly the pattern the check exists to find, and the fix was
to say so in a comment rather than to silence the check.

The tool caught its own author on its first run. That is the honest version of
the story, and a better argument for the approach than a clean first report
would have been.

---

## Design notes

**It is a report, not a fixer.** The original problem was never "nobody can
change these files" — it was "nobody can see the number". A tool that silently
rewrites files also silently rewrites them at the wrong moment.

**AST, not regular expressions.** Deciding whether an `except` block is silent
means knowing exactly what its body contains. A regex cannot answer that; the
`ast` module can.

**Explaining is enough.** A handler with a comment saying why ignoring is safe is
*not* reported. The rule is "say it out loud", not "never ignore". A check that
punishes legitimate suppressions gets disabled within a week.

**The credential scanner scans untracked files too.** It uses
`git ls-files --cached --others --exclude-standard` rather than `--cached`
alone. Scanning only tracked files misses the most dangerous case: a key
written five minutes ago that has not been committed yet.

**Deliberately conservative orphan detection.** Only names that promise
automatic behaviour are considered, and only when the identifier appears
exactly once in the whole tree. A function that also appears in a test is not
reported — "tested but not wired up" is a different problem.

**Unreadable files are not counted as clean.** Both checks increment their
"checked" counter *after* a successful read, so a file that could not be opened
never inflates the number of files reported as passing.

---

## Launching a GUI without stealing focus

`quiet_launch` exists because `subprocess.Popen` brings windows to the
foreground by default, which is a problem for anything that runs while the user
is typing. The obvious fixes do not all work. What follows was measured:

| Approach | Result |
| :--- | :--- |
| default `Popen` | steals focus — Notepad and Chrome both tested |
| `SW_SHOWNOACTIVATE` (4) | works for Notepad, **unreliable for Chrome** |
| `SW_SHOWMINNOACTIVE` (7) | **works for both** — used here |
| off-screen position | does not steal focus |
| `SW_HIDE` | does not steal focus |

Two caveats worth knowing:

- Off-screen and hidden windows are not free. Chromium **does not build an
  accessibility tree** for an invisible window, so automation that reads the
  page through UIA stops working. Hiding a window to be polite can quietly
  break the thing you launched it for.
- `os.startfile` has no equivalent switch. That path will take focus, so the
  module offers a minimise-as-fallback.

`launch_or_reuse()` applies all three layers: **probe** (if it is already
running, reuse it — restarting is itself an interruption), **launch quietly**,
**minimise as a fallback** if focus was taken anyway.

---

## Layout

```
qakit/
  silent_except.py    exception handlers, skipped tests, orphan hooks
  secret_scan.py      credential detection with masked output
  line_endings.py     line-ending convention, markdown fence pairing
  precommit.py        runs every check, single verdict
  quiet_launch.py     Windows GUI launch without stealing focus
tests/                32 unit tests across all four checks
example/fixtures/     files with deliberate violations
example/clean/        files that must produce zero findings
```

---

## Licence

MIT. See `LICENSE`.
