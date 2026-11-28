"""연결 설정 소비와 실제 Windows WIKI 경계 검사. 호스트 실행 검사는 아니다."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from oneploom.connection import execution_settings, resolve_connection
from oneploom.project import Module, Problem


class ExecutionSettingsTests(unittest.TestCase):
    def test_default_does_not_create_saved_choices(self):
        local = {}
        self.assertEqual(execution_settings(local), (None, None))
        self.assertEqual(local, {})

    def test_explicit_choices_preserve_values_without_running_them(self):
        launch = {"args": ["--sandbox", "workspace-write"], "context": "a" * 64}
        local = {"codex_path": "C:/도구 폴더/codex.exe", "launch": launch}
        before = json.dumps(local)
        codex, selected = execution_settings(local)
        self.assertEqual(codex, Path(local["codex_path"]))
        self.assertEqual(selected, launch)
        self.assertEqual(json.dumps(local), before)

    def test_invalid_codex_path_does_not_echo_its_value(self):
        for value in [None, 1, "", "codex", "C:relative", "\\\\host\\share\\codex.exe", "https://SECRET_VALUE"]:
            with self.subTest(value=value), self.assertRaises(Problem) as caught:
                execution_settings({"codex_path": value})
            self.assertEqual(caught.exception.reason, "invalid_config")
            self.assertNotIn("SECRET_VALUE", str(caught.exception))

    def test_launch_requires_both_known_fields(self):
        for value in [None, [], {}, {"args": ["--sandbox", "workspace-write"]},
                      {"context": "a" * 64}, {"args": [], "context": "a" * 64, "extra": True}]:
            with self.subTest(value=value), self.assertRaises(Problem) as caught:
                execution_settings({"launch": value})
            self.assertEqual(caught.exception.reason, "invalid_config")

    def test_launch_arguments_must_be_nonempty_strings(self):
        for args in [[], None, "--sandbox workspace-write", [1], [None], [""], [" "], ["a\x00b"], ["a\nb"]]:
            with self.subTest(args=args), self.assertRaises(Problem):
                execution_settings({"launch": {"args": args, "context": "a" * 64}})

    def test_context_requires_exact_lowercase_sha256_format(self):
        for context in [None, 1, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "a" * 64 + "\n"]:
            with self.subTest(context=context), self.assertRaises(Problem):
                execution_settings({"launch": {"args": ["--sandbox", "workspace-write"], "context": context}})


@unittest.skipUnless(os.name == "nt", "실제 Windows 파일과 경로 검사")
class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oneploom-connection-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "프로젝트 A"
        self.root.mkdir()
        self.wiki = self.root / "문서 WIKI"
        self.wiki.mkdir()
        (self.wiki / "구성").mkdir()
        self.mapping = self.wiki / "구성/위치표.json"
        self.mapping.write_text('{"documents": []}\n', encoding="utf-8")
        self.module = Module(self.root / "oneploom", "test", {"path": "문서 WIKI", "map": "구성/위치표.json"}, None)

    def snapshot(self):
        return {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.base.rglob("*") if p.is_file()}

    def junction(self, link, target):
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_internal_wiki_uses_project_base_without_local_file(self):
        before = self.snapshot()
        with contextlib.chdir(self.base):
            result = resolve_connection(self.root, self.module)
        self.assertEqual(result.wiki_root, self.wiki)
        self.assertEqual(result.map_path, self.mapping)
        self.assertIsNone(result.codex_path)
        self.assertIsNone(result.launch)
        self.assertFalse((self.root / ".harness").exists())
        self.assertEqual(before, self.snapshot())

    def test_external_wiki_uses_registered_folder_and_wiki_relative_map(self):
        external = self.base / "외부 문서 ' B"
        self.wiki.rename(external)
        self.module.wiki["path"] = None
        self.module.local = {"wiki_path": str(external)}
        before = self.snapshot()
        result = resolve_connection(self.root, self.module)
        self.assertEqual(result.wiki_root, external)
        self.assertEqual(result.map_path, external / "구성/위치표.json")
        self.assertEqual(before, self.snapshot())

    def test_internal_and_external_settings_are_not_silently_prioritized(self):
        self.module.local = {"wiki_path": str(self.wiki)}
        with self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "connection_conflict")
        self.assertEqual(caught.exception.code, 3)
        self.assertEqual(self.module.local, {"wiki_path": str(self.wiki)})

    def test_external_wiki_missing_registration_is_not_internal_fallback(self):
        self.module.wiki["path"] = None
        with self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "wiki_not_connected")
        self.assertEqual(caught.exception.code, 3)

    def test_external_path_rejects_relative_uri_and_invalid_values(self):
        self.module.wiki["path"] = None
        for value in [None, "", "docs", "C:docs", "\\docs", "https://SECRET_VALUE", "\\\\host\\share"]:
            self.module.local = {"wiki_path": value}
            with self.subTest(value=value), self.assertRaises(Problem) as caught:
                resolve_connection(self.root, self.module)
            self.assertEqual(caught.exception.reason, "invalid_config")
            self.assertNotIn("SECRET_VALUE", str(caught.exception))

    def test_missing_wiki_is_not_created(self):
        self.module.wiki["path"] = "missing"
        before = self.snapshot()
        with self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "wiki_not_found")
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(before, self.snapshot())

    def test_file_is_not_wiki_directory(self):
        self.module.wiki["path"] = "문서 WIKI/구성/위치표.json"
        with self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "invalid_wiki")

    def test_internal_junction_cannot_escape_project_or_select_root(self):
        for name, target in [("outside", self.base), ("root-link", self.root)]:
            self.junction(self.root / name, target)
            self.module.wiki["path"] = name
            with self.subTest(target=target), self.assertRaises(Problem) as caught:
                resolve_connection(self.root, self.module)
            self.assertEqual(caught.exception.reason, "wiki_outside_project")

    def test_internal_junction_within_project_returns_real_location(self):
        self.junction(self.root / "alias", self.wiki)
        self.module.wiki["path"] = "alias"
        result = resolve_connection(self.root, self.module)
        self.assertEqual(result.wiki_root, self.wiki)
        self.assertEqual(result.map_path, self.mapping)

    def test_missing_map_is_not_replaced_by_another_file(self):
        self.module.wiki["map"] = "missing.json"
        with self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "wiki_map_not_found")
        self.assertTrue(self.mapping.is_file())
        self.assertFalse((self.wiki / "missing.json").exists())

    def test_directory_is_not_map_file(self):
        self.module.wiki["map"] = "구성"
        with self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "invalid_wiki_map")

    def test_map_junction_cannot_escape_wiki(self):
        (self.base / "outside.json").write_text("unchanged", encoding="utf-8")
        self.junction(self.wiki / "outside", self.base)
        self.module.wiki["map"] = "outside/outside.json"
        with self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "map_outside_wiki")
        self.assertEqual((self.base / "outside.json").read_text(), "unchanged")

    def test_network_or_unknown_drive_is_unsupported(self):
        for kind in (0, 1, 4):
            with self.subTest(kind=kind), patch("oneploom.project.ctypes.WinDLL") as native:
                native.return_value.GetDriveTypeW.return_value = kind
                with self.assertRaises(Problem) as caught:
                    resolve_connection(self.root, self.module)
                self.assertEqual(caught.exception.reason, "unsupported_wiki_location")
                self.assertEqual(caught.exception.code, 5)

    def test_access_error_is_not_missing_folder(self):
        with patch.object(Path, "resolve", side_effect=PermissionError), self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "wiki_access_failed")

    def test_map_read_error_preserves_source(self):
        before = self.snapshot()
        with patch.object(Path, "open", side_effect=PermissionError), self.assertRaises(Problem) as caught:
            resolve_connection(self.root, self.module)
        self.assertEqual(caught.exception.reason, "wiki_map_access_failed")
        self.assertEqual(before, self.snapshot())

    def test_interrupt_during_read_is_not_reported_as_success(self):
        before = self.snapshot()
        with patch.object(Path, "open", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            resolve_connection(self.root, self.module)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
