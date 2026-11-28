import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from oneploom.project import Problem, git_root, find_module, read_catalog, read_json, resolve_project, windows_path


class PathFormatTests(unittest.TestCase):
    def test_ambiguous_or_unsupported_inputs(self):
        for value in ["", " ", None, 123, "C:relative", "\\root-relative", "/root-relative", "https://host/path", "\\\\server\\share", "\\\\?\\C:\\x", "a\x00b", "a:b", "file.", "file ", "NUL", "a/COM1.txt", "COM¹.txt", "CONIN$", "LPT1 .txt"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                windows_path(value)

    def test_relative_scope_rules(self):
        for value in ["C:/project", ".", "..", "a/..", "a/../../b"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                windows_path(value, relative=True)
        self.assertEqual(str(windows_path("tools/한글 폴더", relative=True)), "tools\\한글 폴더")
        self.assertEqual(str(windows_path("a/../b", relative=True)), "a\\..\\b")

    def test_absolute_paths_are_required_for_saved_roots(self):
        for value in ["relative", ".", "../A"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                windows_path(value, absolute=True)


class JsonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"

    def test_invalid_json_and_duplicate_keys_preserve_bytes(self):
        for content in [b"{", b"\xff", b'{"id":"one","id":"two"}', b'{"wiki":{"path":null,"path":"docs"}}', b'{"launch":NaN}', b'Infinity']:
            self.path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(Problem):
                read_json(self.path)
            self.assertEqual(self.path.read_bytes(), content)

    def test_catalog_missing_does_not_create_files(self):
        self.assertEqual(read_catalog(self.path), [])
        self.assertFalse(self.path.exists())

    def test_catalog_does_not_open_registered_projects(self):
        content = '["C:/missing A", "D:/missing A"]'
        self.path.write_text(content, encoding="utf-8")
        with patch("oneploom.project.git_root", side_effect=AssertionError("not allowed")):
            self.assertEqual(read_catalog(self.path), ["C:/missing A", "D:/missing A"])
        self.assertEqual(self.path.read_text(), content)

    def test_bad_catalog_is_not_repaired(self):
        for value in [{}, [1], [["C:/A"]], ["C:relative"], ["C:/A", "C:/A"], [""]]:
            content = json.dumps(value)
            self.path.write_text(content)
            with self.subTest(value=value), self.assertRaises(Problem) as caught:
                read_catalog(self.path)
            self.assertEqual(caught.exception.reason, "invalid_project_catalog")
            self.assertEqual(self.path.read_text(), content)

    def test_permission_failure_is_not_missing_catalog(self):
        with patch.object(Path, "lstat", side_effect=PermissionError), self.assertRaises(Problem) as caught:
            read_catalog(self.path)
        self.assertEqual(caught.exception.reason, "invalid_project_catalog")


@unittest.skipUnless(os.name == "nt", "실제 Windows Git과 파일 검사")
class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oneploom-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "프로젝트 A"
        self.root.mkdir()
        self.git(self.root, "init", "-q")

    def git(self, root, *args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def install_fixture(self, module="oneploom", local=None):
        folder = self.root / module
        folder.mkdir(parents=True)
        (folder / "project.json").write_text(json.dumps({"project_id": "test-project", "wiki": {"path": "docs", "map": "map.json"}}), encoding="utf-8")
        (folder / "cli.py").write_text('raise RuntimeError("discovery must not execute this")', encoding="utf-8")
        if local is not None:
            (self.root / ".harness").mkdir(exist_ok=True)
            (self.root / ".harness/local.json").write_text(json.dumps(local), encoding="utf-8")
        return folder

    def local(self, **extra):
        return {"project_id": "test-project", "project_root": str(self.root), **extra}

    def test_root_and_nested_directory(self):
        nested = self.root / "하위 폴더"
        nested.mkdir()
        self.assertEqual(resolve_project(".", nested), self.root)
        self.assertEqual(resolve_project("프로젝트 A", self.base), self.root)

    def test_nested_repository_is_not_parent(self):
        nested = self.root / "child"
        nested.mkdir()
        self.git(nested, "init", "-q")
        self.assertEqual(resolve_project(".", nested), nested)
        self.install_fixture()
        with self.assertRaises(Problem) as caught:
            find_module(nested)
        self.assertEqual(caught.exception.reason, "module_not_found")

    def test_worktree_git_file_is_supported(self):
        self.git(self.root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "test fixture")
        other = self.base / "linked worktree"
        self.git(self.root, "worktree", "add", "--detach", str(other))
        self.assertTrue((other / ".git").is_file())
        self.assertEqual(resolve_project(str(other), self.root), other)

    def test_inherited_git_directory_is_not_used(self):
        other = self.base / "other"
        other.mkdir()
        self.git(other, "init", "-q")
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}):
            self.assertEqual(resolve_project(str(self.root), self.base), self.root)

    def test_project_outside_does_not_scan_children(self):
        self.assertIsNone(git_root(self.base))
        with self.assertRaises(Problem) as caught:
            resolve_project(str(self.base), self.root)
        self.assertEqual(caught.exception.reason, "project_not_found")

    def test_missing_or_file_target_is_rejected(self):
        file = self.base / "file"
        file.write_text("unchanged")
        for value in [str(self.base / "absent"), str(file)]:
            with self.subTest(value=value), self.assertRaises(Problem):
                resolve_project(value, self.root)
        self.assertFalse((self.base / "absent").exists())
        self.assertEqual(file.read_text(), "unchanged")

    def test_broken_git_boundary_is_not_outside(self):
        child = self.root / "child"
        child.mkdir()
        (child / ".git").write_text("not a gitfile")
        with self.assertRaises(Problem) as caught:
            git_root(child)
        self.assertEqual(caught.exception.reason, "project_lookup_failed")

    def test_bare_repository_is_not_a_project(self):
        bare = self.base / "bare"
        bare.mkdir()
        self.git(bare, "init", "--bare", "-q")
        with self.assertRaises(Problem):
            git_root(bare)

    def test_missing_git_is_explicit(self):
        with patch("oneploom.project.shutil.which", return_value=None), self.assertRaises(Problem) as caught:
            git_root(self.root)
        self.assertEqual(caught.exception.reason, "git_not_found")

    def test_default_install_is_read_only(self):
        self.install_fixture()
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        result = find_module(self.root)
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(result.path, self.root / "oneploom")
        self.assertEqual(result.project_id, "test-project")
        self.assertEqual(result.wiki, {"path": "docs", "map": "map.json"})
        self.assertIsNone(result.local)
        self.assertEqual(before, after)
        self.assertFalse((self.root / ".harness").exists())

    def test_custom_install_is_used(self):
        self.install_fixture("tools/custom", self.local(module_path="tools/custom"))
        result = find_module(self.root)
        self.assertEqual(result.path, self.root / "tools/custom")
        self.assertEqual(result.local, self.local(module_path="tools/custom"))

    def test_missing_registered_location_does_not_fall_back(self):
        self.install_fixture(local=self.local(module_path="missing"))
        with self.assertRaises(Problem) as caught:
            find_module(self.root)
        self.assertEqual(caught.exception.reason, "invalid_config")

    def test_local_configuration_copied_from_other_clone(self):
        self.install_fixture(local=self.local(project_root=str(self.base)))
        with self.assertRaises(Problem) as caught:
            find_module(self.root)
        self.assertEqual(caught.exception.reason, "project_mismatch")

    def test_module_path_invalid_values_do_not_select_default(self):
        self.install_fixture(local=self.local())
        path = self.root / ".harness/local.json"
        for value in [None, "", ".", "../other", "C:/elsewhere", ".harness/module"]:
            path.write_text(json.dumps(self.local(module_path=value)), encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(Problem):
                find_module(self.root)

    def test_different_project_id_and_unknown_fields_are_rejected(self):
        self.install_fixture(local=self.local())
        path = self.root / ".harness/local.json"
        for value in [self.local(project_id="other"), self.local(extra=True), self.local(project_id="")]:
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(Problem):
                find_module(self.root)

    def test_shared_wiki_shape_is_checked(self):
        folder = self.install_fixture()
        for wiki in [{"map": "map.json"}, {"path": "", "map": "map.json"}, {"path": None, "map": "../map.json"}, {"path": "C:/outside", "map": "map.json"}]:
            (folder / "project.json").write_text(json.dumps({"project_id": "test", "wiki": wiki}), encoding="utf-8")
            with self.subTest(wiki=wiki), self.assertRaises(Problem):
                find_module(self.root)

    def test_nested_repository_cannot_be_module(self):
        folder = self.install_fixture()
        self.git(folder, "init", "-q")
        with self.assertRaises(Problem) as caught:
            find_module(self.root)
        self.assertEqual(caught.exception.reason, "project_mismatch")

    def test_junction_outside_project_is_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(self.root / "oneploom"), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaises(Problem) as caught:
            find_module(self.root)
        self.assertEqual(caught.exception.reason, "project_mismatch")

    def test_external_local_directory_is_rejected_even_without_config(self):
        self.install_fixture()
        outside = self.base / "outside"
        outside.mkdir()
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(self.root / ".harness"), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaises(Problem) as caught:
            find_module(self.root)
        self.assertEqual(caught.exception.reason, "project_mismatch")

    def test_moved_checkout_with_old_local_path_is_not_repaired(self):
        self.install_fixture(local=self.local())
        moved = self.base / "moved"
        self.root.rename(moved)
        before = (moved / ".harness/local.json").read_bytes()
        with self.assertRaises(Problem) as caught:
            find_module(moved)
        self.assertEqual(caught.exception.reason, "project_mismatch")
        self.assertEqual((moved / ".harness/local.json").read_bytes(), before)

    def test_project_identity_not_found_in_another_registered_clone(self):
        self.install_fixture(local=self.local())
        other = self.base / "other"
        other.mkdir()
        self.git(other, "init", "-q")
        (self.root / ".harness/local.json").write_text(json.dumps(self.local(project_root=str(other))), encoding="utf-8")
        with self.assertRaises(Problem) as caught:
            find_module(self.root)
        self.assertEqual(caught.exception.reason, "project_mismatch")

    def test_home_and_drive_root_are_not_targets(self):
        with patch("oneploom.project.Path.home", return_value=self.root), self.assertRaises(Problem) as caught:
            resolve_project(str(self.root), self.base)
        self.assertEqual(caught.exception.reason, "invalid_project")

    def test_permissions_error_is_not_missing_installation(self):
        self.install_fixture()
        with patch.object(Path, "lstat", side_effect=PermissionError), self.assertRaises(Problem) as caught:
            find_module(self.root)
        self.assertEqual(caught.exception.reason, "invalid_config")

    def test_network_drive_classification_stops_before_git(self):
        with patch("oneploom.project.ctypes.WinDLL") as native, patch("oneploom.project.git_root") as git:
            native.return_value.GetDriveTypeW.return_value = 4
            with self.assertRaises(Problem) as caught:
                resolve_project(str(self.root), self.base)
            self.assertEqual(caught.exception.reason, "unsupported_project_location")
            git.assert_not_called()

    def test_git_worktree_configuration_cannot_redirect_explicit_target(self):
        other = self.base / "redirected"
        other.mkdir()
        self.git(self.root, "config", "core.worktree", str(other))
        with self.assertRaises(Problem) as caught:
            resolve_project(str(self.root), self.base)
        self.assertEqual(caught.exception.reason, "project_mismatch")

    def test_normalized_module_cannot_use_private_state_directory(self):
        self.install_fixture(local=self.local(module_path="oneploom/../.harness"))
        with self.assertRaises(Problem) as caught:
            find_module(self.root)
        self.assertEqual(caught.exception.reason, "invalid_config")


if __name__ == "__main__":
    unittest.main()
