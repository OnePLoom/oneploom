"""실제 Windows Python/Git/PowerShell 프로세스의 입력과 출력 검사."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from oneploom.project import Problem, read_json


@unittest.skipUnless(os.name == "nt", "실제 Windows 프로세스 검사")
class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oneploom-process-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "한글 프로젝트 ' A"
        self.root.mkdir()
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True, capture_output=True)
        module = self.root / "oneploom"
        module.mkdir()
        (module / "project.json").write_text('{"project_id":"test","wiki":{"path":"docs","map":"map.json"}}', encoding="utf-8")
        (module / "cli.py").write_text('raise RuntimeError("must not run")', encoding="utf-8")
        self.source = Path(__file__).resolve().parents[1]
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(self.source)

    def run_cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-B", "-m", "oneploom.cli", *args],
                              cwd=cwd or self.root, env=self.env, input=b"", capture_output=True)

    def test_actual_subprocess_preserves_project_and_returns_utf8_json(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        result = self.run_cli("start", "--project", str(self.root), "--preview", "--json")
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual(result.stderr, b"")
        output = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(output["project_root"], str(self.root))
        self.assertEqual(output["reason"], "runtime_not_implemented")
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_current_directory_and_nested_directory_resolve_same_root(self):
        child = self.root / "nested"
        child.mkdir()
        for cwd in (self.root, child):
            with self.subTest(cwd=cwd):
                result = self.run_cli("start", "--preview", "--json", cwd=cwd)
                self.assertEqual(result.returncode, 5, result.stderr)
                self.assertEqual(json.loads(result.stdout)["project_root"], str(self.root))

    def test_valid_explicit_target_outside_current_project(self):
        result = self.run_cli("start", "--project", str(self.root), "--preview", "--json", cwd=self.base)
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual(json.loads(result.stdout)["project_root"], str(self.root))

    def test_outside_json_is_not_interactive(self):
        result = self.run_cli("start", "--preview", "--json", cwd=self.base)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(result.stdout)["reason"], "project_selection_required")
        self.assertEqual(result.stderr, b"")

    def test_help_without_git(self):
        self.env["PATH"] = ""
        result = self.run_cli("--help", cwd=self.base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("프로젝트", result.stdout.decode("utf-8"))

    def test_actual_powershell_keeps_project_argument(self):
        # PowerShell 리터럴의 작은따옴표는 두 번 쓴다. 셸 변수로 경로를 재해석하지 않는다.
        python = str(Path(sys.executable)).replace("'", "''")
        project = str(self.root).replace("'", "''")
        command = f"& '{python}' -B -m oneploom.cli start --project '{project}' --preview --json; exit $LASTEXITCODE"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                cwd=self.source, env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertEqual(json.loads(result.stdout.decode("utf-8"))["project_root"], str(self.root))

    def test_actual_windows_share_denial_is_not_empty_config(self):
        path = self.root / "oneploom/project.json"
        before = path.read_bytes()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        create.restype = ctypes.c_void_p
        close = kernel.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        handle = create(str(path), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            with self.assertRaises(Problem) as caught:
                read_json(path)
            self.assertEqual(caught.exception.reason, "invalid_config")
        finally:
            close(handle)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
