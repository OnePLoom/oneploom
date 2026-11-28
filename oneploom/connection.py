"""발견한 설정을 소비해 WIKI 연결을 읽기 전용으로 확인한다."""

from dataclasses import dataclass
from pathlib import Path
import re

from .project import Module, Problem, check_local_path, windows_path


@dataclass
class Connection:
    """현재 확인한 위치. Codex의 접근 권한이나 실행 준비 완료를 뜻하지 않는다."""

    wiki_root: Path
    map_path: Path
    codex_path: Path | None
    launch: dict | None


def execution_settings(local):
    """저장된 실행 선택의 형식만 검사한다. 옵션의 적용 가능성은 호스트가 확인한다."""
    codex = None
    if "codex_path" in local:
        try:
            windows_path(local["codex_path"], absolute=True)
        except ValueError as error:
            raise Problem("invalid_config", "codex_path에는 선택한 실행 파일의 Windows 절대 경로가 필요합니다.") from error
        codex = Path(local["codex_path"])

    launch = local.get("launch")
    if "launch" in local:
        if not isinstance(launch, dict) or set(launch) != {"args", "context"}:
            raise Problem("invalid_config", "launch에는 args와 context가 모두 필요합니다. 기본값을 상속하려면 launch를 생략해 주세요.")
        args = launch["args"]
        if (not isinstance(args, list) or not args
                or any(not isinstance(arg, str) or not arg.strip() or any(ord(c) < 32 for c in arg) for arg in args)):
            raise Problem("invalid_config", "launch.args에는 비어 있지 않은 실행 옵션 문자열 배열이 필요합니다.")
        if not isinstance(launch["context"], str) or re.fullmatch(r"[0-9a-f]{64}", launch["context"]) is None:
            raise Problem("invalid_config", "launch.context의 비교 값 형식을 확인해 주세요.")
    return codex, launch


def resolve_connection(root: Path, module: Module) -> Connection:
    """프로젝트 기준 WIKI와 WIKI 기준 위치표를 확인하며 파일을 생성하거나 고치지 않는다."""
    local = module.local if module.local is not None else {}
    codex, launch = execution_settings(local)
    internal = module.wiki["path"] is not None
    if internal:
        if "wiki_path" in local:
            raise Problem("connection_conflict", "내부 WIKI 설정과 외부 WIKI 연결이 함께 있습니다. 연결 변경에서 사용할 위치를 확인해 주세요.", 3)
        candidate = root / module.wiki["path"]
    else:
        if "wiki_path" not in local:
            raise Problem("wiki_not_connected", "외부 WIKI 위치가 아직 연결되지 않았습니다. 이 작업 사본의 WIKI 경로를 먼저 등록해 주세요.", 3)
        try:
            windows_path(local["wiki_path"], absolute=True)
        except ValueError as error:
            raise Problem("invalid_config", "wiki_path에는 WIKI의 Windows 절대 경로가 필요합니다.") from error
        candidate = Path(local["wiki_path"])

    try:
        wiki = candidate.resolve(strict=True)
        # 연결된 폴더 자체가 다른 위치를 가리킬 수 있으므로 실제 대상도 검사한다.
        check_local_path(wiki)
        if internal and (wiki == root or not wiki.is_relative_to(root)):
            raise Problem("wiki_outside_project", "내부 WIKI의 실제 위치가 프로젝트 영역 밖입니다.")
        if not wiki.is_dir():
            raise Problem("invalid_wiki", "등록한 WIKI 위치가 디렉토리가 아닙니다.")
    except FileNotFoundError as error:
        raise Problem("wiki_not_found", "등록한 WIKI 폴더가 없습니다. 기존 연결을 보존하고 위치를 확인해 주세요.", 3) from error
    except ValueError as error:
        raise Problem("unsupported_wiki_location", "WIKI의 실제 위치는 지원하는 Windows 로컬 경로여야 합니다.", 5) from error
    except OSError as error:
        raise Problem("wiki_access_failed", "WIKI 위치에 접근하지 못했습니다. 기존 연결과 접근 권한을 확인해 주세요.") from error

    try:
        mapping = (wiki / module.wiki["map"]).resolve(strict=True)
        if mapping == wiki or not mapping.is_relative_to(wiki):
            raise Problem("map_outside_wiki", "위치표의 실제 위치가 등록한 WIKI 영역 밖입니다.")
        if not mapping.is_file():
            raise Problem("invalid_wiki_map", "위치표 위치가 일반 파일이 아닙니다.")
        # 이번 단계는 위치와 읽기 가능 여부만 확인한다. 위치표 내용의 계약은 별도 소비자가 검사한다.
        with mapping.open("rb") as stream:
            stream.read(1)
    except FileNotFoundError as error:
        raise Problem("wiki_map_not_found", "등록한 위치표 파일이 없습니다. 다른 파일을 대신 선택하지 않습니다.", 3) from error
    except OSError as error:
        raise Problem("wiki_map_access_failed", "등록한 위치표를 읽을 수 없습니다. 파일과 접근 권한을 확인해 주세요.") from error

    return Connection(wiki, mapping, codex, launch)
