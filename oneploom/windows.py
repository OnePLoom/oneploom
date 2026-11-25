"""현재 Windows 사용자의 시스템 경로 조회. 파일이나 환경을 변경하지 않는다."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import uuid

from .project import Problem


def catalog_path():
    if os.name != "nt":
        raise Problem("unsupported_platform", "현재 제품 구현의 지원 대상은 Windows입니다.", 5)
    folder_id = (ctypes.c_byte * 16).from_buffer_copy(
        uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
    )
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    query = shell.SHGetKnownFolderPath
    query.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    query.restype = ctypes.c_long
    free = ole.CoTaskMemFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None
    pointer = ctypes.c_void_p()
    try:
        # flags=0: 현재 폴더 조회만 하며 KF_FLAG_CREATE를 사용하지 않는다.
        result = query(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer))
        if result < 0 or not pointer.value:
            raise Problem("invalid_project_catalog", "Windows 사용자 앱 데이터 위치를 조회하지 못했습니다.")
        return Path(ctypes.wstring_at(pointer.value)) / "OnepLoom" / "projects.json"
    finally:
        if pointer.value:
            free(pointer)
