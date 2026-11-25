"""프로젝트와 설정을 읽기 전용으로 확인한다. 설치나 코드 실행은 하지 않는다."""

import ctypes
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess


class Problem(Exception):
    """호출자가 설명과 종료 상태로 바꿀 수 있는 확인 실패."""

    def __init__(self, reason, message, code=1):
        super().__init__(message)
        self.reason = reason
        self.code = code


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-JSON number")


def read_json(path, reason="invalid_config"):
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        # 설정 본문이나 오류에 포함된 입력값은 진단에 복사하지 않는다.
        detail = "파일 접근 실패" if isinstance(error, OSError) else "UTF-8 JSON 형식 오류"
        raise Problem(reason, f"{path.name}: {detail}. 원본을 보존하고 확인해 주세요.") from error


def windows_path(value, *, absolute=False, relative=False):
    """모호한 드라이브 상대 경로와 미지원 네트워크/장치 경로를 거부한다."""
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("비어 있거나 잘못된 경로입니다.")
    path = PureWindowsPath(value)
    if value.startswith(("\\\\", "//")) or "://" in value:
        raise ValueError("네트워크, 장치 경로 또는 URI는 지원하지 않습니다.")
    if bool(path.drive) != bool(path.root):
        raise ValueError("드라이브 상대 경로와 루트 상대 경로는 허용하지 않습니다.")
    if absolute and not path.is_absolute():
        raise ValueError("Windows 절대 경로가 필요합니다.")
    if relative:
        depth = 0
        if path.drive or path.root:
            raise ValueError("기준 폴더 안의 상대 경로가 필요합니다.")
        for part in path.parts:
            depth += -1 if part == ".." else 1
            if depth < 0:
                raise ValueError("기준 폴더를 벗어나는 상대 경로입니다.")
        if depth == 0:
            raise ValueError("기준 폴더 자체를 대상으로 사용할 수 없습니다.")
    for part in path.parts[1:] if path.anchor else path.parts:
        if part in (".", ".."):
            continue
        name = part.split(".", 1)[0].rstrip(" ").upper()
        device = name in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or (
            len(name) == 4 and name[:3] in {"COM", "LPT"} and name[3] in "123456789¹²³"
        )
        if any(c in part for c in '<>:"|?*') or part.endswith((" ", ".")) or device:
            raise ValueError("Windows에서 사용할 수 없거나 모호한 경로입니다.")
    return path


def git_root(directory):
    """Git이 확인한 가장 가까운 작업 사본. 저장소 밖일 때만 None."""
    git = shutil.which("git")
    if git is None:
        raise Problem("git_not_found", "프로젝트 확인에 필요한 Windows Git을 설치해 주세요.", 3)
    # Git 훅 등에서 상속된 다른 저장소 위치를 이번 조회에 적용하지 않는다.
    try:
        names = subprocess.run([git, "rev-parse", "--local-env-vars"], cwd=directory,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        env = os.environ.copy()
        for name in names.stdout.splitlines():
            env.pop(name, None)
        result = subprocess.run(
            [git, "-C", str(directory), "rev-parse", "--show-toplevel", "--is-inside-work-tree", "--is-inside-git-dir"],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            if len(lines) != 3:
                raise Problem("project_lookup_failed", "Git의 작업 폴더 조회 결과를 확인할 수 없습니다.")
            root = Path(lines[0]).resolve(strict=True)
            if not directory.is_relative_to(root):
                raise Problem("project_mismatch", "Git이 보고한 작업 폴더와 요청한 위치가 다릅니다. 저장소의 작업 폴더 설정을 확인해 주세요.")
            if lines[1:] != ["true", "false"]:
                raise Problem("invalid_project", "Git 작업 폴더 내부에서 실행해 주세요.")
            return root
        # 손상된 .git, 권한 거부, bare 저장소를 '프로젝트 밖'으로 바꿔 목록을 띄우지 않는다.
        for parent in (directory, *directory.parents):
            try:
                (parent / ".git").lstat()
            except FileNotFoundError:
                continue
            else:
                raise Problem("project_lookup_failed", "현재 Git 경계를 확인하지 못했습니다. 저장소 상태와 접근 권한을 확인해 주세요.")
        bare = subprocess.run([git, "-C", str(directory), "rev-parse", "--is-bare-repository"],
                              env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if bare.returncode == 0:
            raise Problem("invalid_project", "작업 사본이 없는 Git 저장소는 실행 대상이 아닙니다.")
        return None
    except (OSError, subprocess.CalledProcessError) as error:
        raise Problem("project_lookup_failed", "Git 또는 프로젝트 경로에 접근하지 못했습니다.") from error


def resolve_project(value, cwd):
    explicit = value is not None
    value = value if explicit else str(cwd)
    try:
        windows_path(value)
    except ValueError as error:
        raise Problem("invalid_input", str(error), 2) from error
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    try:
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise Problem("invalid_project", "프로젝트 경로는 디렉토리여야 합니다.")
        try:
            windows_path(str(path), absolute=True)
        except ValueError as error:
            raise Problem("unsupported_project_location", "실제 대상은 지원하는 Windows 로컬 경로여야 합니다.", 5) from error
        drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
        drive_type.argtypes = [ctypes.c_wchar_p]
        drive_type.restype = ctypes.c_uint
        if drive_type(path.anchor) not in (2, 3, 5, 6):
            raise Problem("unsupported_project_location", "네트워크 또는 종류를 확인할 수 없는 드라이브는 지원하지 않습니다.", 5)
        root = git_root(path)
        if root is None:
            if not explicit:
                return None
            raise Problem("project_not_found", "지정한 경로에 Git 작업 사본이 없습니다. 구체적인 프로젝트 경로를 지정해 주세요.", 3)
        if root == Path(root.anchor) or root.samefile(Path.home()):
            raise Problem("invalid_project", "홈 또는 드라이브 최상위 폴더는 프로젝트 대상으로 사용하지 않습니다.")
        return root
    except OSError as error:
        raise Problem("project_lookup_failed", "프로젝트 경로가 없거나 접근할 수 없습니다. 다른 대상으로 대신 진행하지 않습니다.") from error


def read_catalog(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise Problem("invalid_project_catalog", "프로젝트 목록에 접근할 수 없습니다.") from error
    values = read_json(path, "invalid_project_catalog")
    try:
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError("목록은 경로 문자열 배열이어야 합니다.")
        if len(set(values)) != len(values):
            raise ValueError("목록에 같은 경로가 중복되어 있습니다.")
        for value in values:
            windows_path(value, absolute=True)
        return values
    except ValueError as error:
        raise Problem("invalid_project_catalog", f"프로젝트 목록 형식 오류: {error}") from error


def choose_project(paths, stream, output):
    """표시한 경로 배열을 유지한다. 다른 호출이 목록을 바꿔도 번호를 재해석하지 않는다."""
    if not paths:
        raise Problem("project_selection_required", "등록된 프로젝트가 없습니다. start --project PATH로 대상을 지정해 주세요.", 3)
    if not stream.isatty():
        raise Problem("project_selection_required", "프로젝트 선택이 필요합니다. start --project PATH로 대상을 지정해 주세요.", 3)
    for number, path in enumerate(paths, 1):
        print(f"{number}. {PureWindowsPath(path).name} — {path}", file=output)
    while True:
        print("프로젝트 번호 (Enter 또는 q: 취소): ", end="", file=output, flush=True)
        try:
            raw = stream.readline()
        except KeyboardInterrupt as error:
            raise Problem("user_cancelled", "프로젝트 선택을 취소했습니다.", 4) from error
        if raw == "":
            raise Problem("project_selection_required", "입력이 종료되어 프로젝트를 선택하지 못했습니다.", 3)
        choice = raw.strip()
        if choice in ("", "q"):
            raise Problem("user_cancelled", "프로젝트 선택을 취소했습니다.", 4)
        if choice.isascii() and choice.isdigit() and len(choice) <= len(str(len(paths))):
            number = int(choice)
            if 1 <= number <= len(paths):
                return paths[number - 1]
        print("목록의 번호를 입력하거나 취소해 주세요.", file=output)


def _inside(path, root):
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved == root:
            raise Problem("project_mismatch", "설정 또는 설치 파일의 실제 위치가 허용된 프로젝트 영역 밖입니다.")
        return resolved
    except OSError as error:
        raise Problem("invalid_config", "설정 또는 설치 파일이 없거나 접근할 수 없습니다.") from error


def find_module(root):
    """발견용 필드를 검사한다. 실행 허가나 설치 기준의 무결성을 인증하지 않는다."""
    local_path = root / ".harness" / "local.json"
    local = None
    try:
        local_path.lstat()
    except FileNotFoundError:
        try:
            local_path.parent.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise Problem("invalid_config", "로컬 상태 폴더에 접근할 수 없습니다.") from error
        else:
            if not _inside(local_path.parent, root).is_dir():
                raise Problem("invalid_config", "로컬 상태 위치가 디렉토리가 아닙니다.")
    except OSError as error:
        raise Problem("invalid_config", "로컬 연결 파일에 접근할 수 없습니다.") from error
    else:
        local = read_json(_inside(local_path, root))
        if not isinstance(local, dict) or set(local) - {"project_id", "project_root", "module_path", "wiki_path", "codex_path", "launch"}:
            raise Problem("invalid_config", "로컬 연결 설정의 객체 형식 또는 필드를 확인해 주세요.")
        try:
            windows_path(local.get("project_root"), absolute=True)
            if not Path(local["project_root"]).samefile(root):
                raise Problem("project_mismatch", "로컬 연결의 프로젝트 경로가 현재 작업 사본과 다릅니다.")
        except (ValueError, OSError) as error:
            raise Problem("project_mismatch", "로컬 연결의 프로젝트 경로를 현재 작업 사본과 대조할 수 없습니다.") from error
    module_path = local.get("module_path", "oneploom") if local is not None else "oneploom"
    try:
        relative = windows_path(module_path, relative=True)
        if relative.parts[0].casefold() == ".harness":
            raise ValueError("개인 상태 폴더는 공유 설치 영역이 아닙니다.")
    except ValueError as error:
        raise Problem("invalid_config", f"module_path 오류: {error}") from error
    candidate = root / module_path
    try:
        candidate.lstat()
    except FileNotFoundError as error:
        if local is not None and "module_path" in local:
            raise Problem("invalid_config", "등록된 설치 위치가 없습니다. 기본 위치로 대체하지 않습니다.") from error
        raise Problem("module_not_found", "이 프로젝트에 OnepLoom 설치본이 없습니다. 설치 또는 팀의 연결 준비 안내를 확인해 주세요.", 3) from error
    except OSError as error:
        raise Problem("invalid_config", "설치 위치에 접근하지 못했습니다.") from error
    module = _inside(candidate, root)
    if module.relative_to(root).parts[0].casefold() == ".harness":
        raise Problem("invalid_config", "개인 상태 폴더는 공유 설치 영역으로 사용할 수 없습니다.")
    if not module.is_dir() or git_root(module) != root:
        raise Problem("project_mismatch", "설치 영역이 해당 프로젝트의 디렉토리가 아닙니다.")
    config = read_json(_inside(module / "project.json", module))
    if not isinstance(config, dict) or set(config) != {"project_id", "wiki"}:
        raise Problem("invalid_config", "공유 설정에는 project_id와 wiki가 필요합니다. 알 수 없는 필드를 버리지 않습니다.")
    identifier = config["project_id"]
    if not isinstance(identifier, str) or not identifier.strip() or any(ord(c) < 32 for c in identifier):
        raise Problem("invalid_config", "project_id는 비어 있지 않은 문자열이어야 합니다.")
    if local is not None and local.get("project_id") != identifier:
        raise Problem("project_mismatch", "공유 설정과 로컬 연결의 프로젝트 식별자가 다릅니다.")
    wiki = config["wiki"]
    try:
        if not isinstance(wiki, dict) or set(wiki) != {"path", "map"}:
            raise ValueError("wiki에는 path와 map이 필요합니다.")
        if wiki["path"] is not None:
            windows_path(wiki["path"], relative=True)
        windows_path(wiki["map"], relative=True)
    except ValueError as error:
        raise Problem("invalid_config", f"공유 WIKI 설정 형식 오류: {error}") from error
    entry = _inside(module / "cli.py", module)
    if not entry.is_file():
        raise Problem("invalid_config", "프로젝트 진입 파일 cli.py가 일반 파일이 아닙니다.")
    return module
