"""검사 누락이나 실패가 성공 종료로 보고되지 않는지 실제 프로세스로 확인한다."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oneploom-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        (self.root / "tests/__init__.py").write_text("", encoding="utf-8")
        self.source = Path(__file__).resolve().parents[1]

    def run_suite(self, body):
        (self.root / "tests/test_example.py").write_text(body, encoding="utf-8")
        # 별도 프로세스로 실행해 발견된 tests 모듈이 현재 검사와 섞이지 않게 한다.
        return subprocess.run([
            sys.executable, "-B", "-X", "utf8", "-W", "error::DeprecationWarning", "-c",
            "from pathlib import Path; import sys; from tools.run_tests import run_tests; "
            "raise SystemExit(run_tests(Path(sys.argv[1])))",
            str(self.root),
        ], cwd=self.source, capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_success_requires_actual_tests(self):
        result = self.run_suite(
            "import unittest\nclass Example(unittest.TestCase):\n"
            "    def test_first(self): self.assertEqual(1, 1)\n"
            "    def test_second(self): self.assertTrue(True)\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 2 tests", result.stderr)

    def test_no_tests_fails(self):
        result = self.run_suite("# A module without tests.\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("No tests were discovered.", result.stderr)

    def test_assertion_failure_fails(self):
        result = self.run_suite(
            "import unittest\nclass Example(unittest.TestCase):\n"
            "    def test_failure(self): self.fail('expected failure')\n"
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("failures=1", result.stderr)

    def test_skip_with_or_without_passing_test_fails(self):
        body = (
            "import unittest\nclass Example(unittest.TestCase):\n"
            "    @unittest.skip('required case not run')\n"
            "    def test_skip(self): pass\n"
        )
        for extra in ("", "    def test_pass(self): pass\n"):
            with self.subTest(passing_test=bool(extra)):
                result = self.run_suite(body + extra)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("Skipped tests do not count", result.stderr)

    def test_import_error_fails(self):
        result = self.run_suite("raise ImportError('fixture import failure')\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("fixture import failure", result.stderr)

    def test_setup_error_fails(self):
        result = self.run_suite(
            "import unittest\nclass Example(unittest.TestCase):\n"
            "    def setUp(self): raise RuntimeError('fixture setup failure')\n"
            "    def test_pass(self): pass\n"
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("errors=1", result.stderr)

    def test_deprecation_warning_fails(self):
        result = self.run_suite(
            "import unittest, warnings\nclass Example(unittest.TestCase):\n"
            "    def test_warning(self): warnings.warn('fixture warning', DeprecationWarning)\n"
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("DeprecationWarning", result.stderr)

    def test_interruption_does_not_pass(self):
        result = self.run_suite(
            "import unittest\nclass Example(unittest.TestCase):\n"
            "    def test_interrupt(self): raise KeyboardInterrupt\n"
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("KeyboardInterrupt", result.stderr)

    @unittest.skipUnless(sys.platform == "win32", "Windows 검사 환경 확인")
    def test_missing_required_tool_does_not_start_tests(self):
        environment = os.environ.copy()
        environment["PATH"] = ""
        result = subprocess.run([
            sys.executable, "-B", "-X", "utf8", "-W", "error::DeprecationWarning",
            str(self.source / "tools/run_tests.py"),
        ], cwd=self.root, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Cannot verify the test environment", result.stderr)
        self.assertNotIn("Ran ", result.stderr)

    @unittest.skipUnless(sys.platform == "win32", "Windows 전체 검사 진입점")
    def test_entrypoint_uses_its_repository_instead_of_working_directory(self):
        (self.root / "tests/test_example.py").write_text(
            "import unittest\nclass Example(unittest.TestCase):\n"
            "    def test_pass(self): pass\n", encoding="utf-8",
        )
        tools = self.root / "tools"
        tools.mkdir()
        script = tools / "run_tests.py"
        script.write_bytes((self.source / "tools/run_tests.py").read_bytes())
        result = subprocess.run([
            sys.executable, "-B", "-X", "utf8", "-W", "error::DeprecationWarning", str(script),
        ], cwd=tools, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)


if __name__ == "__main__":
    unittest.main()
