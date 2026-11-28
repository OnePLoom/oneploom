"""Windows 합성 파일의 접근/교체 보호. 제품의 변경 기록과 복구 시험은 아니다."""

import ctypes
import errno
import mmap
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from oneploom.files import file_api, protected_file
from oneploom.project import Problem


@unittest.skipUnless(os.name == "nt", "실제 Windows 파일 보호 검사")
class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oneploom-files-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / "한글 ' # 10% & 자료"
        self.folder.mkdir()
        self.path = self.folder / "config.json"
        self.original = b'\xef\xbb\xbf{"key":"value"}\r\n'
        self.path.write_bytes(self.original)

    def run_child(self, source, *args):
        return subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", source, *map(str, args)],
                              capture_output=True, text=True, timeout=10)

    def test_bytes_identity_noninheritance_and_close(self):
        expected = self.path.stat()
        with protected_file(self.path) as stream:
            self.assertEqual(stream.read(), self.original)
            current = os.fstat(stream.fileno())
            self.assertEqual((current.st_dev, current.st_ino), (expected.st_dev, expected.st_ino))
            self.assertFalse(os.get_inheritable(stream.fileno()))
            stream.seek(0)
            self.assertEqual(stream.read(), self.original)
        self.assertTrue(stream.closed)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_default_stream_cannot_write(self):
        with protected_file(self.path) as stream:
            with self.assertRaises((OSError, ValueError)):
                stream.write(b"bad")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_descriptor_does_not_grant_write_access_either(self):
        with protected_file(self.path) as stream:
            with self.assertRaises(OSError):
                os.write(stream.fileno(), b"bad")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_missing_file_or_parent_is_never_created(self):
        for path in (self.folder / "missing.json", self.root / "missing/config.json"):
            with self.subTest(path=path), self.assertRaises(Problem) as caught:
                with protected_file(path):
                    self.fail("missing target accepted")
            self.assertEqual(caught.exception.reason, "file_not_found")
            self.assertFalse(path.exists())
        self.assertFalse((self.root / "missing").exists())

    def test_relative_parent_traversal_network_device_and_stream_are_rejected(self):
        paths = [Path("config.json"), self.folder / ".." / "config.json", Path("C:config.json"),
                 Path("\\\\server\\share\\config.json"), Path("\\\\?\\C:\\config.json"),
                 Path(str(self.path) + ":other"), Path(self.path.anchor)]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(Problem) as caught:
                with protected_file(path):
                    self.fail("unsupported path accepted")
            self.assertEqual(caught.exception.reason, "unsupported_file_location")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_directory_and_hard_links_are_not_normal_files(self):
        with self.assertRaises(Problem) as caught:
            with protected_file(self.folder):
                self.fail("directory accepted as file")
        self.assertEqual(caught.exception.reason, "unsupported_file")
        alias = self.folder / "alias.json"
        os.link(self.path, alias)
        for path in (self.path, alias):
            with self.subTest(path=path), self.assertRaises(Problem) as caught:
                with protected_file(path):
                    self.fail("hard link accepted")
            self.assertEqual(caught.exception.reason, "unsupported_file")
        self.assertEqual(alias.read_bytes(), self.original)

    def test_junction_parent_is_rejected_and_released(self):
        link = self.root / "junction"
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(self.folder)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaises(Problem) as caught:
            with protected_file(link / self.path.name):
                self.fail("junction followed")
        self.assertEqual(caught.exception.reason, "unsupported_file")
        link.rmdir()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_existing_reader_prevents_exclusive_access_then_retry_works(self):
        with self.path.open("rb"):
            with self.assertRaises(Problem) as caught:
                with protected_file(self.path):
                    self.fail("existing reader ignored")
            self.assertEqual(caught.exception.reason, "file_busy")
        with protected_file(self.path) as stream:
            self.assertEqual(stream.read(), self.original)

    def test_held_file_blocks_read_write_delete_and_replace(self):
        replacement = self.folder / "candidate.json"
        replacement.write_bytes(b"different")
        with protected_file(self.path) as stream:
            for mode in ("rb", "wb", "ab"):
                with self.subTest(mode=mode), self.assertRaises(OSError):
                    with self.path.open(mode):
                        self.fail("conflicting open accepted")
            with self.assertRaises(OSError):
                self.path.unlink()
            with self.assertRaises(OSError):
                os.replace(replacement, self.path)
            self.assertEqual(stream.read(), self.original)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(replacement.read_bytes(), b"different")

    def test_parent_rename_is_blocked_but_unrelated_file_can_change(self):
        sibling = self.folder / "unrelated.txt"
        with protected_file(self.path):
            with self.assertRaises(OSError):
                self.folder.rename(self.root / "moved")
            with self.assertRaises(OSError):
                self.root.rename(self.root.with_name(self.root.name + "-moved"))
            sibling.write_bytes(b"allowed")
            self.assertEqual(sibling.read_bytes(), b"allowed")
        moved = self.root / "moved"
        self.folder.rename(moved)
        self.assertEqual((moved / self.path.name).read_bytes(), self.original)
        moved.rename(self.folder)

    def test_readonly_file_can_be_read_without_changing_attributes(self):
        self.path.chmod(stat.S_IREAD)
        try:
            with protected_file(self.path) as stream:
                self.assertEqual(stream.read(), self.original)
            self.assertTrue(self.path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
        finally:
            self.path.chmod(stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_new_hard_link_is_possible_but_alias_cannot_write_while_held(self):
        alias = self.folder / "added.json"
        source = "import os,sys; os.link(sys.argv[1],sys.argv[2])"
        with protected_file(self.path) as stream:
            result = self.run_child(source, self.path, alias)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.fstat(stream.fileno()).st_nlink, 2)
            with self.assertRaises(OSError):
                alias.write_bytes(b"bad")
            self.assertEqual(stream.read(), self.original)
        self.assertEqual(alias.read_bytes(), self.original)
        # 보호가 끝나면 두 이름은 같은 내용을 가리킨다. 이를 파일 쓰기 보호로 확대하지 않는다.
        alias.write_bytes(b"after close")
        self.assertEqual(self.path.read_bytes(), b"after close")

    def test_existing_writable_mapping_prevents_exclusive_access(self):
        with self.path.open("r+b") as source:
            mapped = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_WRITE)
        try:
            with self.assertRaises(Problem) as caught:
                with protected_file(self.path):
                    self.fail("existing writable mapping ignored")
            self.assertEqual(caught.exception.reason, "file_busy")
        finally:
            mapped.close()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_exceptions_and_keyboard_interrupt_release_all_handles(self):
        for error in (RuntimeError("test"), KeyboardInterrupt(), SystemExit(9)):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                with protected_file(self.path):
                    raise error
            self.assertEqual(self.path.read_bytes(), self.original)
            moved = self.root / "moved"
            self.folder.rename(moved)
            moved.rename(self.folder)

    def test_other_process_is_blocked_until_context_exits(self):
        source = """
import sys
from pathlib import Path
try:
    Path(sys.argv[1]).write_bytes(b'changed')
except OSError as error:
    print(type(error).__name__, error.errno)
    raise SystemExit(12)
"""
        with protected_file(self.path):
            blocked = self.run_child(source, self.path)
        self.assertEqual(blocked.returncode, 12, blocked.stderr)
        self.assertEqual(blocked.stdout.strip(), f"PermissionError {errno.EACCES}")
        self.assertEqual(self.path.read_bytes(), self.original)
        result = self.run_child(source, self.path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.path.read_bytes(), b"changed")

    def test_process_exit_releases_handle_without_modifying_file(self):
        source = """
import os, sys
from pathlib import Path
from oneploom.files import protected_file
with protected_file(Path(sys.argv[1])) as stream:
    assert stream.read()
    os._exit(17)
"""
        result = self.run_child(source, self.path)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.folder.rename(self.root / "moved")

    def test_actual_path_mismatch_rejects_and_releases(self):
        import oneploom.files as files
        original = files.open_existing

        def changed(api, path, **options):
            if not options["directory"]:
                path = self.other
            return original(api, path, **options)

        self.other = self.folder / "other.json"
        self.other.write_bytes(b"other")
        with patch("oneploom.files.open_existing", side_effect=changed), self.assertRaises(Problem) as caught:
            with protected_file(self.path):
                self.fail("wrong actual file accepted")
        self.assertEqual(caught.exception.reason, "file_path_changed")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.other.read_bytes(), b"other")

    def test_information_failure_or_incomplete_path_does_not_continue(self):
        for method, value in (("GetFileInformationByHandle", 0), ("GetFinalPathNameByHandleW", 0)):
            api = Mock(wraps=file_api())
            getattr(api, method).return_value = value
            with self.subTest(method=method), patch("oneploom.files.file_api", return_value=api):
                with self.assertRaises(Problem) as caught:
                    with protected_file(self.path):
                        self.fail("failed information ignored")
                self.assertEqual(caught.exception.reason, "file_information_failed")
            self.assertEqual(self.path.read_bytes(), self.original)
        api = Mock(wraps=file_api())
        api.GetFinalPathNameByHandleW.side_effect = [4, 4]
        with patch("oneploom.files.file_api", return_value=api), self.assertRaises(Problem) as caught:
            with protected_file(self.path):
                self.fail("incomplete path accepted")
        self.assertEqual(caught.exception.reason, "file_information_failed")

    def test_failed_descriptor_or_stream_conversion_releases_owner_once(self):
        for target in ("msvcrt.open_osfhandle", "oneploom.files.os.fdopen"):
            with self.subTest(target=target), patch(target, side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    with protected_file(self.path):
                        self.fail("failed conversion ignored")
            self.assertEqual(self.path.read_bytes(), self.original)
            moved = self.root / "moved"
            self.folder.rename(moved)
            moved.rename(self.folder)

    def test_open_errors_release_previously_held_parents(self):
        for code, reason in ((2, "file_not_found"), (5, "file_access_denied"),
                             (32, "file_busy"), (123, "file_open_failed")):
            api = Mock(wraps=file_api())
            create = api.CreateFileW._mock_wraps

            def fail_target(path, *args):
                if Path(path) == self.path:
                    ctypes.set_last_error(code)
                    return ctypes.c_void_p(-1).value
                return create(path, *args)

            api.CreateFileW.side_effect = fail_target
            with self.subTest(code=code), patch("oneploom.files.file_api", return_value=api):
                with self.assertRaises(Problem) as caught:
                    with protected_file(self.path):
                        self.fail("open error ignored")
                self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(self.path.read_bytes(), self.original)
            moved = self.root / "moved"
            self.folder.rename(moved)
            moved.rename(self.folder)

    def test_close_failure_is_not_success_and_other_handles_are_closed(self):
        api = Mock(wraps=file_api())
        close = api.CloseHandle._mock_wraps

        def fail_after_close(handle):
            self.assertTrue(close(handle))
            return 0

        api.CloseHandle.side_effect = fail_after_close
        with patch("oneploom.files.file_api", return_value=api), self.assertRaises(Problem) as caught:
            with protected_file(self.path):
                pass
        self.assertEqual(caught.exception.reason, "file_close_failed")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.folder.rename(self.root / "moved")


if __name__ == "__main__":
    unittest.main()
