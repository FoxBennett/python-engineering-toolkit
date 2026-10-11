"""Tests for the quality checks.

Run with the standard library only:

    python -m unittest discover -s tests -v

Deliberately uses no third-party test runner, so the project keeps its
"standard library only" property end to end.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qakit import line_endings, quiet_launch, secret_scan, silent_except

FIXTURES = Path(__file__).resolve().parents[1] / "example" / "fixtures"
CLEAN = Path(__file__).resolve().parents[1] / "example" / "clean"


class TestSilentHandlers(unittest.TestCase):
    def test_handler_with_comment_is_not_reported(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        return int('x')\n"
            "    except ValueError:\n"
            "        # Blank input is expected; treat it as zero.\n"
            "        return 0\n"
        )
        self.assertEqual(silent_except.scan_source(source), [])

    def test_bare_pass_is_reported(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except Exception:\n"
            "        pass\n"
        )
        found = silent_except.scan_source(source)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["line"], 4)

    def test_bare_return_is_reported(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except OSError:\n"
            "        return\n"
        )
        self.assertEqual(len(silent_except.scan_source(source)), 1)

    def test_returning_a_value_is_not_reported(self):
        """A value-bearing return may be a deliberate default."""
        source = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except OSError:\n"
            "        return None\n"
        )
        self.assertEqual(silent_except.scan_source(source), [])

    def test_noqa_suppression_is_honoured(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        import optional\n"
            "    except ImportError:  # noqa: S110 - optional dependency\n"
            "        pass\n"
        )
        self.assertEqual(silent_except.scan_source(source), [])

    def test_handler_that_logs_is_not_reported(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except OSError as exc:\n"
            "        log.warning('failed: %s', exc)\n"
        )
        self.assertEqual(silent_except.scan_source(source), [])

    def test_unparsable_source_returns_empty(self):
        """A syntax error must not raise; other tools will catch it."""
        self.assertEqual(silent_except.scan_source("def broken(:\n"), [])

    def test_fixture_directory_reports_expected_counts(self):
        result = silent_except.scan(FIXTURES)
        self.assertEqual(len(result["silent"]), 2)
        self.assertEqual(len(result["skips"]), 1)


class TestLineEndings(unittest.TestCase):
    def test_lf_file_is_ok(self):
        self.assertEqual(line_endings.classify("a.py", b"line\n", ()), "ok")

    def test_crlf_where_lf_expected(self):
        self.assertEqual(line_endings.classify("a.py", b"line\r\n", ()), "crlf")

    def test_mixed_endings(self):
        self.assertEqual(line_endings.classify("a.py", b"a\r\nb\nc\n", ()), "mixed")

    def test_missing_final_newline(self):
        self.assertEqual(line_endings.classify("a.py", b"line", ()), "no-final-newline")

    def test_crlf_expected_and_present(self):
        self.assertEqual(line_endings.classify("run.bat", b"line\r\n", ("*.bat",)), "ok")

    def test_crlf_expected_but_lf_found(self):
        self.assertEqual(line_endings.classify("run.bat", b"line\n", ("*.bat",)), "lf")

    def test_empty_file_is_ok(self):
        self.assertEqual(line_endings.classify("a.py", b"", ()), "ok")

    def test_odd_fence_count_is_reported(self):
        fences = line_endings.scan_fences(FIXTURES, ["notes.md"])
        self.assertEqual(len(fences), 1)
        self.assertEqual(fences[0][1] % 2, 1)


class TestCleanDirectory(unittest.TestCase):
    """The bundled clean example must produce zero findings."""

    def test_clean_directory_is_clean(self):
        silent = silent_except.scan(CLEAN)
        endings = line_endings.scan(CLEAN, line_endings.DEFAULT_CRLF_PATTERNS, ())
        self.assertEqual(silent["silent"], [])
        self.assertEqual(silent["skips"], [])
        self.assertEqual(silent["orphans"], [])
        self.assertEqual(endings["problems"], [])
        self.assertEqual(line_endings.scan_fences(CLEAN, endings["files"]), [])

    def test_clean_directory_scans_something(self):
        """Guards against a check that passes only because it found no files."""
        self.assertGreater(silent_except.scan(CLEAN)["checked"], 0)


class TestSecretScan(unittest.TestCase):
    def test_mask_never_reveals_the_value(self):
        secret = "sk-FAKE1234567890abcdefGHIJKL"
        masked = secret_scan.mask(secret)
        self.assertNotIn(secret, masked)
        self.assertNotIn(secret[6:], masked)
        self.assertIn(str(len(secret)), masked)

    def test_short_values_are_not_masked_as_secrets(self):
        self.assertEqual(secret_scan.mask("abc"), "<masked>")

    def test_mixed_case_long_value_is_suspicious(self):
        token = "FAKEabcdef1234567890XYZ"
        line = "API_KEY = '%s'" % token
        self.assertTrue(secret_scan.looks_like_secret(token, line))

    def test_environment_variable_reference_is_not_a_secret(self):
        token = "MY_SERVICE_API_KEY_LONG"
        line = "API_KEY = os.environ['%s']" % token
        self.assertFalse(secret_scan.looks_like_secret(token, line))

    def test_placeholder_is_not_a_secret(self):
        token = "your-api-key-here-replace-me"
        line = "KEY = '%s'" % token
        self.assertFalse(secret_scan.looks_like_secret(token, line))

    def test_all_upper_case_name_is_not_a_secret(self):
        token = "SOMETHING_LIKE_THIS_LONG"
        self.assertFalse(secret_scan.looks_like_secret(token, "X = " + token))

    def test_fixture_reports_a_suspicious_hit(self):
        hits = secret_scan.scan_file(
            FIXTURES / "leaked_key.py", FIXTURES, secret_scan.DEFAULT_VAULT_SUFFIXES)
        self.assertTrue(any(hit["suspicious"] for hit in hits))

    def test_vault_file_is_flagged_as_vault(self):
        hits = secret_scan.scan_file(
            FIXTURES / "leaked_key.py", FIXTURES, ("leaked_key.py",))
        self.assertTrue(all(hit["vault"] for hit in hits))


class TestQuietLaunch(unittest.TestCase):
    def test_startupinfo_never_raises(self):
        """Off Windows this returns None; either way it must not raise."""
        info = quiet_launch.quiet_startupinfo()
        self.assertTrue(info is None or hasattr(info, "wShowWindow"))

    def test_unknown_process_is_not_running(self):
        probe = quiet_launch.AppSpec("probe", processes=("definitely-not-a-real-process.exe",))
        self.assertFalse(quiet_launch.is_running(probe))

    def test_spec_without_process_names_is_not_running(self):
        self.assertFalse(quiet_launch.is_running(quiet_launch.AppSpec("empty")))

    def test_minimize_with_no_titles_returns_zero(self):
        self.assertEqual(quiet_launch.minimize_windows(()), 0)

    def test_launch_missing_executable_reports_failure(self):
        ok, detail = quiet_launch.launch("definitely-not-a-real-binary-xyz")
        self.assertFalse(ok)
        self.assertTrue(detail)

    def test_registry_entries_resolve_without_raising(self):
        for spec in quiet_launch.DEFAULT_APPS:
            resolved = quiet_launch.find_executable(spec)
            self.assertIsInstance(resolved, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
