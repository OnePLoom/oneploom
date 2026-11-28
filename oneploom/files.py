"""Windows의 기존 파일을 보호하며 읽는다. 실제 파일 반영과 복구는 제공하지 않는다."""

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import stat

from .project import Problem, check_local_path, windows_path


class FileInformation(ctypes.Structure):
    """Windows BY_HANDLE_FILE_INFORMATION의 배치. 식별은 열린 파일의 fstat로 조회한다."""

    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("access_time", wintypes.FILETIME),
        ("write_time", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


def file_api():
    """64비트 핸들과 문자열이 잘리지 않도록 Windows 함수의 형식을 지정한다."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    api.GetFileInformationByHandle.restype = wintypes.BOOL
    api.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                           wintypes.DWORD, wintypes.DWORD]
    api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    api.GetFileType.argtypes = [wintypes.HANDLE]
    api.GetFileType.restype = wintypes.DWORD
    return api


def close_handle(api, handle):
    if not api.CloseHandle(handle):
        raise Problem("file_close_failed", "파일 보호 핸들을 반환하지 못했습니다. 실제 파일 상태를 확인해 주세요.")


def open_existing(api, path, *, directory):
    """재분석 지점을 따라가지 않고 기존 대상만 연다. 부모에는 삭제/쓰기 공유를 허용하지 않는다."""
    access = 0 if directory else 0x80000000  # GENERIC_READ
    share = 1 if directory else 0  # FILE_SHARE_READ / 배타적 파일 접근
    # BACKUP_SEMANTICS로 실제 디렉토리도 열어 종류를 구분한다. 별도 권한 활성화는 하지 않는다.
    flags = 0x00200000 | 0x02000000  # OPEN_REPARSE_POINT / BACKUP_SEMANTICS
    handle = api.CreateFileW(str(path), access, share, None, 3, flags, None)  # OPEN_EXISTING
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):
            raise Problem("file_not_found", "보호할 기존 파일 또는 부모 폴더가 없습니다. 새로 생성하지 않습니다.", 3)
        if error in (32, 33):
            raise Problem("file_busy", "다른 접근과 충돌해 파일을 보호할 수 없습니다. 사용 중인 작업을 확인해 주세요.")
        if error == 5:
            raise Problem("file_access_denied", "파일 접근이 거부됐습니다. 읽기 전용 속성과 접근 권한을 확인해 주세요.")
        raise Problem("file_open_failed", f"기존 파일을 열지 못했습니다. Windows 오류: {error}.")
    return handle


def check_handle(api, handle, path, *, directory):
    """열린 대상의 종류, 링크와 실제 경로를 확인한다. 승인과 프로젝트 소유를 대신하지 않는다."""
    info = FileInformation()
    if not api.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise Problem("file_information_failed", "열린 파일의 속성을 확인하지 못했습니다.")
    if (info.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or bool(info.attributes & stat.FILE_ATTRIBUTE_DIRECTORY) != directory
            or (not directory and info.links != 1)
            or api.GetFileType(handle) != 1):  # FILE_TYPE_DISK
        raise Problem("unsupported_file", "재분석 지점, 하드 링크 또는 지원하지 않는 파일 종류입니다.", 5)
    length = api.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not length:
        raise Problem("file_information_failed", "열린 파일의 실제 경로를 확인하지 못했습니다.")
    buffer = ctypes.create_unicode_buffer(length)
    received = api.GetFinalPathNameByHandleW(handle, buffer, length, 0)
    if not received or received >= length:
        raise Problem("file_information_failed", "실제 경로가 완전히 반환되지 않았습니다.")
    actual = Path(buffer.value.removeprefix("\\\\?\\"))
    try:
        check_local_path(actual)
    except ValueError as error:
        raise Problem("unsupported_file_location", "실제 파일 위치가 지원하는 Windows 로컬 경로가 아닙니다.", 5) from error
    if actual != path:
        raise Problem("file_path_changed", "열린 파일의 실제 경로가 요청한 경로와 다릅니다. 별칭이나 교체 여부를 확인해 주세요.")


@contextmanager
def protected_file(path: Path):
    """기존 파일을 읽는 동안 일반 파일 접근과 경로 교체를 제한한다.

    핸들 공유 제한은 모든 메타데이터 변경을 막지 않는다. 새 하드 링크가 추가될 수
    있으므로 이 읽기 결과만으로 쓰기 소유나 이후 반영의 안전성을 확정하지 않는다.
    본문에서 AI/사용자 응답을 기다리지 않는다. DB 생성, 사본 저장과 복구는 하지 않는다.
    """
    if os.name != "nt":
        raise Problem("unsupported_platform", "현재 파일 보호 구현의 지원 대상은 Windows입니다.", 5)
    import msvcrt

    try:
        windows_path(str(path), absolute=True)
        if ".." in path.parts or path == Path(path.anchor):
            raise ValueError("정규 절대 파일 경로가 필요합니다.")
        check_local_path(path)
    except ValueError as error:
        raise Problem("unsupported_file_location", "보호 대상에는 별칭이 아닌 Windows 로컬 파일의 절대 경로가 필요합니다.", 5) from error

    api = file_api()
    with ExitStack() as parents:
        # 루트부터 각 부모를 잡는다. 최종 파일만 잡으면 상위 폴더 교체가 남을 수 있다.
        for parent in reversed(path.parents):
            handle = open_existing(api, parent, directory=True)
            parents.callback(close_handle, api, handle)
            check_handle(api, handle, parent, directory=True)

        handle = open_existing(api, path, directory=False)
        descriptor = None
        stream = None
        try:
            check_handle(api, handle, path, directory=False)
            flags = os.O_BINARY | os.O_NOINHERIT | os.O_RDONLY
            descriptor = msvcrt.open_osfhandle(handle, flags)
            handle = None  # 이제 descriptor가 핸들의 수명을 소유한다.
            stream = os.fdopen(descriptor, "rb", buffering=0)
            descriptor = None  # 이제 stream.close()가 descriptor와 핸들을 반환한다.
            yield stream
        finally:
            try:
                if stream is not None:
                    stream.close()
                elif descriptor is not None:
                    os.close(descriptor)
                elif handle is not None:
                    close_handle(api, handle)
            except OSError as error:
                raise Problem("file_close_failed", "파일 보호의 종료를 확인하지 못했습니다. 같은 파일의 실제 상태를 확인해 주세요.") from error
