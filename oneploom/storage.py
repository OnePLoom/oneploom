"""기존 상태 DB에서 짧은 조회/저장 묶음을 실행한다. 생성과 파일 반영은 맡지 않는다."""

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys

from .project import Problem, check_local_path, windows_path


def state_path(path: Path) -> Path:
    """실제 로컬 파일을 찾는다. 프로젝트 소유와 작업 승인 확인은 호출자의 책임이다."""
    try:
        windows_path(str(path), absolute=True)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise Problem("unsupported_state_file", "상태 DB는 링크가 아닌 단일 일반 파일이어야 합니다.", 5)
        resolved = path.resolve(strict=True)
        check_local_path(resolved)
        return resolved
    except FileNotFoundError as error:
        raise Problem("state_not_found", "기존 상태 DB가 없습니다. 설치 기록과 연결 위치를 확인해 주세요. 빈 DB는 만들지 않습니다.", 3) from error
    except ValueError as error:
        raise Problem("unsupported_state_location", "상태 DB에는 Windows 로컬 파일의 절대 경로가 필요합니다.", 5) from error
    except OSError as error:
        raise Problem("state_access_failed", "상태 DB에 접근하지 못했습니다. 기존 파일과 권한을 확인해 주세요.") from error


def check_identity(connection, db_id: str, schema_version: int) -> None:
    """같은 트랜잭션에서 DB 식별과 호출자가 지원하는 정확한 형식을 대조한다."""
    rows = connection.execute("SELECT singleton, db_id, schema_version FROM metadata").fetchmany(2)
    if (len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] != 1
            or not isinstance(rows[0][1], str) or not rows[0][1].strip()
            or type(rows[0][2]) is not int or rows[0][2] <= 0):
        raise Problem("invalid_state_identity", "상태 DB의 식별 정보가 올바르지 않습니다. 기존 파일을 보존하고 확인해 주세요.")
    if rows[0][1] != db_id:
        raise Problem("state_mismatch", "요청한 상태 DB와 실제 DB의 식별자가 다릅니다. 다른 작업의 기록을 사용하지 않습니다.")
    if rows[0][2] != schema_version:
        raise Problem("unsupported_state_version", "현재 작업이 지원하는 DB 형식과 다릅니다. 자동 변환하지 않습니다.", 5)


@contextmanager
def transaction(path: Path, *, db_id: str, schema_version: int, write: bool = False):
    """검증한 기존 DB를 열고 BEGIN부터 COMMIT/ROLLBACK, 연결 종료까지 관리한다.

    내부 저장 코드만 이 연결을 사용한다. 본문에는 바인딩한 SQL을 두고 파일 변경,
    사용자 대기나 AI 호출, 별도의 트랜잭션 제어/ATTACH를 넣지 않는다.
    이 함수의 성공은 DB 저장의 성공이며 실제 파일 변경의 성공은 아니다.
    """
    if os.name != "nt":
        raise Problem("unsupported_platform", "현재 상태 저장 구현의 지원 대상은 Windows입니다.", 5)
    if sys.version_info < (3, 12):
        raise Problem("sqlite_unavailable", "명시적 트랜잭션 제어를 위해 sqlite3를 포함한 Python 3.12 이상이 필요합니다.", 3)
    try:
        import sqlite3
    except ImportError as error:
        raise Problem("sqlite_unavailable", "현재 Python에 sqlite3가 없습니다. sqlite3를 포함한 Python을 준비해 주세요.", 3) from error
    if (not isinstance(db_id, str) or not db_id.strip()
            or type(schema_version) is not int or not 0 < schema_version < 2**63
            or type(write) is not bool):
        raise Problem("invalid_state_request", "DB 식별자, 지원 형식 번호와 조회/쓰기 구분을 확인해 주세요.", 2)
    path = state_path(path)
    # as_uri가 경로의 #, %와 공백 등을 인코딩한다. URI 옵션을 입력 문자열에 붙이지 않는다.
    uri = path.as_uri() + ("?mode=rw" if write else "?mode=ro")
    connection = None
    committing = False
    try:
        # 잠금은 즉시 보고한다. 숨은 반복이나 실행 중인 다른 프로세스의 종료는 하지 않는다.
        connection = sqlite3.connect(uri, uri=True, autocommit=True, timeout=0)
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise Problem("unsupported_state_settings", "SQLite 외래키 검사를 활성화할 수 없습니다.", 5)
        if write:
            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise Problem("unsupported_state_settings", "기존 DB의 journal 방식이 지원하는 DELETE와 다릅니다. 자동 변경하지 않습니다.", 5)
            connection.execute("PRAGMA synchronous = EXTRA")
            if connection.execute("PRAGMA synchronous").fetchone() != (3,):
                raise Problem("unsupported_state_settings", "SQLite 저장 동기화 설정을 확인할 수 없습니다.", 5)
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            check_identity(connection, db_id, schema_version)
            yield connection
            if not connection.in_transaction:
                raise Problem("state_transaction_ended", "저장 코드가 트랜잭션을 먼저 종료했습니다. 작업 식별자로 실제 기록을 다시 확인해야 합니다.")
            committing = True
            connection.execute("COMMIT")
        except BaseException:
            # KeyboardInterrupt/SystemExit도 앞선 SQL만 남기지 않고 호출자에게 전달한다.
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error as error:
                    raise Problem("state_rollback_failed", "DB 취소를 확인하지 못했습니다. 재적용 전에 같은 작업 식별자로 기록을 확인해 주세요.") from error
            raise
    except sqlite3.Error as error:
        if committing:
            raise Problem("state_commit_failed", "DB 저장 완료를 확인하지 못했습니다. 같은 작업 식별자로 기록을 조회한 뒤 재개해 주세요.") from error
        code = getattr(error, "sqlite_errorcode", 0) & 0xff
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise Problem("state_busy", "다른 작업이 상태 DB를 사용 중입니다. 그 작업이 끝난 뒤 다시 시도해 주세요.") from error
        if code == sqlite3.SQLITE_CONSTRAINT:
            raise Problem("state_constraint_failed", "저장 기록의 참조 또는 제약조건이 맞지 않아 이번 묶음을 저장하지 못했습니다.") from error
        raise Problem("state_database_failed", "상태 DB 작업에 실패했습니다. 원본을 보존하고 DB 형식, 접근과 복구 상태를 확인해 주세요.") from error
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as error:
                raise Problem("state_close_failed", "DB 연결 종료를 확인하지 못했습니다. 새 쓰기 전에 같은 작업 기록을 확인해 주세요.") from error
