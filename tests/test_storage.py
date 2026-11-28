"""Windows 파일 DB의 연결/트랜잭션 검사. 설치와 전체 복원 시험은 아니다."""

from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from oneploom.project import Problem
from oneploom.storage import transaction


@unittest.skipUnless(os.name == "nt", "실제 Windows SQLite 파일 검사")
class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oneploom-storage-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "기록 # 10% & state.db"
        # 제품 DDL을 대신하지 않는 작은 관계 fixture다. DB 설정은 제품 함수를 사용한다.
        with closing(sqlite3.connect(self.path, autocommit=True)) as db:
            db.execute("CREATE TABLE metadata(singleton, db_id, schema_version)")
            db.execute("INSERT INTO metadata VALUES(1, 'test-db', 1)")
            db.execute("CREATE TABLE parents(id INTEGER PRIMARY KEY)")
            db.execute("CREATE TABLE children(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parents(id))")

    def tx(self, **kwargs):
        return transaction(self.path, db_id="test-db", schema_version=1, **kwargs)

    def rows(self):
        with self.tx() as db:
            return db.execute("SELECT id FROM parents ORDER BY id").fetchall()

    def snapshot(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.iterdir() if p.is_file()}

    def test_write_settings_and_commit_are_visible_after_reopen(self):
        with self.tx(write=True) as db:
            self.assertTrue(db.autocommit)
            self.assertTrue(db.in_transaction)
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone(), (1,))
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone(), ("delete",))
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone(), (3,))
            self.assertEqual(db.execute("PRAGMA busy_timeout").fetchone(), (0,))
            db.execute("INSERT INTO parents VALUES(?)", (1,))
            db.execute("INSERT INTO children VALUES(?, ?)", (1, 1))
        self.assertEqual(self.rows(), [(1,)])
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_read_only_query_preserves_bytes_and_rejects_write(self):
        before = self.snapshot()
        with self.tx() as db:
            self.assertEqual(db.execute("SELECT db_id FROM metadata").fetchone(), ("test-db",))
        self.assertEqual(before, self.snapshot())
        with self.assertRaises(Problem) as caught:
            with self.tx() as db:
                db.execute("INSERT INTO parents VALUES(1)")
        self.assertEqual(caught.exception.reason, "state_database_failed")
        self.assertEqual(before, self.snapshot())

    def test_uri_characters_do_not_create_or_open_another_file(self):
        with self.tx(write=True) as db:
            db.execute("INSERT INTO parents VALUES(7)")
        self.assertEqual(set(self.snapshot()), {self.path.name})
        self.assertEqual(self.rows(), [(7,)])

    def test_missing_db_and_parent_are_not_created_in_either_mode(self):
        before = self.snapshot()
        for path in (self.root / "missing.db", self.root / "missing/state.db"):
            for write in (False, True):
                with self.subTest(path=path, write=write), self.assertRaises(Problem) as caught:
                    with transaction(path, db_id="test-db", schema_version=1, write=write):
                        self.fail("missing DB was opened")
                self.assertEqual(caught.exception.reason, "state_not_found")
                self.assertFalse(path.exists())
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(before, self.snapshot())

    def test_missing_between_path_check_and_open_is_not_created(self):
        missing = self.root / "disappeared.db"
        with patch("oneploom.storage.state_path", return_value=missing):
            for write in (False, True):
                with self.subTest(write=write), self.assertRaises(Problem):
                    with self.tx(write=write):
                        self.fail("mode created the DB")
        self.assertFalse(missing.exists())

    def test_relative_uri_and_network_paths_are_rejected(self):
        for value in ("state.db", ":memory:", "file:///C:/state.db", "C:state.db", "\\\\host\\share\\state.db"):
            with self.subTest(value=value), self.assertRaises(Problem) as caught:
                with transaction(Path(value), db_id="test-db", schema_version=1):
                    self.fail("invalid path accepted")
            self.assertEqual(caught.exception.reason, "unsupported_state_location")

    def test_directory_and_hard_link_are_rejected(self):
        with self.assertRaises(Problem) as caught:
            with transaction(self.root, db_id="test-db", schema_version=1):
                self.fail("directory accepted")
        self.assertEqual(caught.exception.reason, "unsupported_state_file")
        alias = self.root / "alias.db"
        os.link(self.path, alias)
        before = self.snapshot()
        for path in (self.path, alias):
            with self.subTest(path=path), self.assertRaises(Problem) as caught:
                with transaction(path, db_id="test-db", schema_version=1, write=True):
                    self.fail("hard link accepted")
            self.assertEqual(caught.exception.reason, "unsupported_state_file")
        self.assertEqual(before, self.snapshot())

    def test_request_types_are_checked_before_open(self):
        for values in ({"db_id": ""}, {"db_id": None}, {"schema_version": True},
                       {"schema_version": 0}, {"schema_version": 1.0},
                       {"schema_version": 2**63}, {"write": 1}):
            args = {"db_id": "test-db", "schema_version": 1, **values}
            with self.subTest(values=values), patch("oneploom.storage.state_path") as path_check:
                with self.assertRaises(Problem) as caught:
                    with transaction(self.path, **args):
                        self.fail("invalid request accepted")
                self.assertEqual(caught.exception.reason, "invalid_state_request")
                path_check.assert_not_called()

    def test_wrong_identity_or_version_preserves_db(self):
        before = self.snapshot()
        for identity, version, reason in (("other", 1, "state_mismatch"), ("test-db", 2, "unsupported_state_version")):
            for write in (False, True):
                with self.subTest(identity=identity, write=write), self.assertRaises(Problem) as caught:
                    with transaction(self.path, db_id=identity, schema_version=version, write=write):
                        self.fail("wrong DB accepted")
                self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(before, self.snapshot())

    def test_malformed_identity_is_not_repaired(self):
        for values in ([], [(1, "test-db", "1")], [(1, "test-db", 1.0)],
                       [(1, "", 1)], [(2, "test-db", 1)], [(1, "test-db", 1)] * 2):
            with closing(sqlite3.connect(self.path, autocommit=True)) as db:
                db.execute("DELETE FROM metadata")
                db.executemany("INSERT INTO metadata VALUES(?, ?, ?)", values)
            before = self.snapshot()
            with self.subTest(values=values), self.assertRaises(Problem) as caught:
                with self.tx(write=True):
                    self.fail("malformed identity accepted")
            self.assertEqual(caught.exception.reason, "invalid_state_identity")
            self.assertEqual(before, self.snapshot())

    def test_corrupt_or_empty_db_is_not_initialized(self):
        for content in (b"not a database SECRET_VALUE", b""):
            self.path.write_bytes(content)
            with self.subTest(content=content), self.assertRaises(Problem) as caught:
                with self.tx(write=True):
                    self.fail("invalid DB accepted")
            self.assertEqual(caught.exception.reason, "state_database_failed")
            self.assertNotIn("SECRET_VALUE", str(caught.exception))
            self.assertEqual(self.path.read_bytes(), content)

    def test_wal_write_is_rejected_without_mode_conversion(self):
        with closing(sqlite3.connect(self.path, autocommit=True)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
        with self.assertRaises(Problem) as caught:
            with self.tx(write=True):
                self.fail("WAL write accepted")
        self.assertEqual(caught.exception.reason, "unsupported_state_settings")
        with self.tx() as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone(), ("wal",))

    def test_foreign_key_failure_rolls_back_previous_statement(self):
        with self.assertRaises(Problem) as caught:
            with self.tx(write=True) as db:
                db.execute("INSERT INTO parents VALUES(1)")
                db.execute("INSERT INTO children VALUES(1, 999)")
        self.assertEqual(caught.exception.reason, "state_constraint_failed")
        self.assertEqual(self.rows(), [])

    def test_body_error_and_interrupt_roll_back_and_close(self):
        for failure in (RuntimeError("body failed"), KeyboardInterrupt(), SystemExit(4)):
            with self.subTest(failure=type(failure).__name__), self.assertRaises(type(failure)):
                with self.tx(write=True) as db:
                    db.execute("INSERT INTO parents VALUES(1)")
                    raise failure
            self.assertEqual(self.rows(), [])
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")

    def test_second_writer_is_busy_then_can_retry_without_losing_first(self):
        with self.tx(write=True) as first:
            first.execute("INSERT INTO parents VALUES(1)")
            with self.assertRaises(Problem) as caught:
                with self.tx(write=True):
                    self.fail("second writer entered")
            self.assertEqual(caught.exception.reason, "state_busy")
        with self.tx(write=True) as db:
            db.execute("INSERT INTO parents VALUES(2)")
        self.assertEqual(self.rows(), [(1,), (2,)])

    def test_reader_holds_snapshot_and_failed_commit_is_not_success(self):
        with self.tx() as reader:
            self.assertEqual(reader.execute("SELECT id FROM parents").fetchall(), [])
            with self.assertRaises(Problem) as caught:
                with self.tx(write=True) as writer:
                    writer.execute("INSERT INTO parents VALUES(1)")
            self.assertEqual(caught.exception.reason, "state_commit_failed")
            self.assertEqual(reader.execute("SELECT id FROM parents").fetchall(), [])
        self.assertEqual(self.rows(), [])
        with self.tx(write=True) as db:
            db.execute("INSERT INTO parents VALUES(2)")
        self.assertEqual(self.rows(), [(2,)])

    def test_unexpected_transaction_end_is_not_reported_as_success(self):
        with self.assertRaises(Problem) as caught:
            with self.tx(write=True) as db:
                db.execute("ROLLBACK")
        self.assertEqual(caught.exception.reason, "state_transaction_ended")
        self.assertEqual(self.rows(), [])

    def test_missing_sqlite_or_old_python_is_reported_without_installing(self):
        before = self.snapshot()
        with patch.dict(sys.modules, {"sqlite3": None}), self.assertRaises(Problem) as caught:
            with self.tx():
                self.fail("missing sqlite accepted")
        self.assertEqual(caught.exception.reason, "sqlite_unavailable")
        with patch("oneploom.storage.sys.version_info", (3, 11, 9)), self.assertRaises(Problem) as caught:
            with self.tx():
                self.fail("unsupported Python accepted")
        self.assertEqual(caught.exception.reason, "sqlite_unavailable")
        self.assertEqual(before, self.snapshot())

    def test_unavailable_sqlite_settings_stop_before_body(self):
        connect = sqlite3.connect
        for setting in ("PRAGMA foreign_keys = ON", "PRAGMA synchronous = EXTRA"):
            class UnavailableSetting(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if sql == setting:
                        return super().execute("SELECT 1")
                    return super().execute(sql, parameters)

            before = self.snapshot()
            with self.subTest(setting=setting), patch("sqlite3.connect", side_effect=lambda *a, **kw: connect(*a, factory=UnavailableSetting, **kw)):
                with self.assertRaises(Problem) as caught:
                    with self.tx(write=True):
                        self.fail("required setting unavailable")
            self.assertEqual(caught.exception.reason, "unsupported_state_settings")
            self.assertEqual(before, self.snapshot())

    def test_commit_and_rollback_errors_are_distinguished_and_connection_closes(self):
        connect = sqlite3.connect
        for statement, reason in (("COMMIT", "state_commit_failed"), ("ROLLBACK", "state_rollback_failed")):
            class FailingStatement(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if sql == statement:
                        raise sqlite3.OperationalError("SECRET_VALUE")
                    return super().execute(sql, parameters)

            with self.subTest(statement=statement), patch("sqlite3.connect", side_effect=lambda *a, **kw: connect(*a, factory=FailingStatement, **kw)):
                with self.assertRaises(Problem) as caught:
                    with self.tx(write=True) as db:
                        db.execute("INSERT INTO parents VALUES(1)")
                        if statement == "ROLLBACK":
                            raise RuntimeError("cancel this unit")
            self.assertEqual(caught.exception.reason, reason)
            self.assertNotIn("SECRET_VALUE", str(caught.exception))
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")
            self.assertEqual(self.rows(), [])

    def test_close_error_does_not_mean_a_committed_write_can_be_repeated(self):
        connect = sqlite3.connect

        class CloseFailure(sqlite3.Connection):
            def close(self):
                super().close()
                raise sqlite3.OperationalError("SECRET_VALUE")

        with patch("sqlite3.connect", side_effect=lambda *a, **kw: connect(*a, factory=CloseFailure, **kw)):
            with self.assertRaises(Problem) as caught:
                with self.tx(write=True) as db:
                    db.execute("INSERT INTO parents VALUES(1)")
        self.assertEqual(caught.exception.reason, "state_close_failed")
        self.assertNotIn("SECRET_VALUE", str(caught.exception))
        self.assertEqual(self.rows(), [(1,)])

    def test_another_process_cannot_write_until_current_transaction_finishes(self):
        script = """
import sys
from pathlib import Path
from oneploom.project import Problem
from oneploom.storage import transaction
try:
    with transaction(Path(sys.argv[1]), db_id='test-db', schema_version=1, write=True):
        raise SystemExit('unexpected write access')
except Problem as error:
    print(error.reason)
"""
        with self.tx(write=True) as db:
            db.execute("INSERT INTO parents VALUES(1)")
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script, str(self.path)],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                    text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "state_busy")
        self.assertEqual(self.rows(), [(1,)])

    def test_abrupt_exit_recovers_hot_journal_without_losing_committed_rows(self):
        with self.tx(write=True) as db:
            db.execute("INSERT INTO parents VALUES(1)")
            db.execute("CREATE TABLE payloads(body BLOB)")
        script = """
import os
import sys
from pathlib import Path
from oneploom.storage import transaction
with transaction(Path(sys.argv[1]), db_id='test-db', schema_version=1, write=True) as db:
    db.execute('INSERT INTO parents VALUES(2)')
    db.execute('INSERT INTO payloads VALUES(zeroblob(?))', (4 * 1024 * 1024,))
    os._exit(17)
"""
        # 큰 fixture로 실제 journal을 남긴 뒤 finally/close 없이 시험 자식만 종료한다.
        # 10초는 시험 프로세스의 정지 감지이며 제품 명령의 시간 제한이 아니다.
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script, str(self.path)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 17, result.stderr)
        journal = Path(str(self.path) + "-journal")
        self.assertGreater(journal.stat().st_size, 512)
        with journal.open("rb") as stream:
            self.assertNotEqual(stream.read(8), b"\x00" * 8)
        with self.assertRaises(Problem) as caught:
            with self.tx():
                self.fail("read-only connection recovered a hot journal")
        self.assertEqual(caught.exception.reason, "state_database_failed")
        # 쓰기 재개는 SQLite 자체의 journal 복구를 사용한다. 보조 파일을 직접 지우지 않는다.
        with self.tx(write=True) as db:
            self.assertEqual(db.execute("SELECT id FROM parents").fetchall(), [(1,)])
            self.assertEqual(db.execute("SELECT count(*) FROM payloads").fetchone(), (0,))
            db.execute("INSERT INTO parents VALUES(3)")
        self.assertEqual(self.rows(), [(1,), (3,)])


if __name__ == "__main__":
    unittest.main()
