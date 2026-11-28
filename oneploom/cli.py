"""개발 작업본 진입: python -m oneploom.cli. 아직 설치/호스트 실행은 제공하지 않는다."""

import argparse
import json
import os
from pathlib import Path
import sys

from .project import Problem, choose_project, find_module, read_catalog, resolve_project
from .connection import resolve_connection
from .windows import catalog_path


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse의 오류 인자 원문을 출력하지 않아 전달받은 비밀값을 노출하지 않는다.
        raise Problem("invalid_input", "명령이나 옵션을 확인해 주세요. --help에서 사용법을 볼 수 있습니다.", 2)


def parse_args(argv):
    parser = Parser(prog="oneploom", description="프로젝트의 OnepLoom과 WIKI 위치를 확인합니다. 현재는 실행 전 확인 기능의 개발 작업본입니다.", allow_abbrev=False)
    parser.add_argument("--version", action="version", version="OnepLoom 개발 작업본 (릴리스 전)")
    commands = parser.add_subparsers(dest="command")
    for name in ("start", "resume"):
        command = commands.add_parser(name, help="프로젝트 대상 확인" if name == "start" else "재개 대상의 프로젝트 확인", allow_abbrev=False)
        if name == "resume":
            command.add_argument("session", nargs="?", help="재개할 대화 식별자")
        target = command.add_mutually_exclusive_group()
        target.add_argument("--project", action="append", metavar="PATH", help="프로젝트 경로")
        target.add_argument("--select", action="store_true", help="등록 목록에서 선택")
        command.add_argument("--preview", action="store_true", help="실행 없이 확인")
        command.add_argument("--json", action="store_true", help="미리보기 결과를 JSON으로 반환")
    projects = commands.add_parser("projects", help="등록된 프로젝트 조회", allow_abbrev=False)
    actions = projects.add_subparsers(dest="action")
    listing = actions.add_parser("list", help="등록 목록 조회", allow_abbrev=False)
    listing.add_argument("--json", action="store_true", help="JSON으로 반환")
    tokens = list(argv) if argv else ["start"]
    forwarded = []
    if "--" in tokens:
        index = tokens.index("--")
        forwarded = tokens[index + 1:]
        tokens = tokens[:index]
        if not tokens or tokens[0] not in ("start", "resume"):
            parser.error("separator")
    args = parser.parse_args(tokens)
    args.forwarded = forwarded
    if args.command == "projects" and args.action is None:
        projects.print_help()
        return None
    if args.command in ("start", "resume"):
        if args.project is not None:
            if len(args.project) != 1 or not args.project[0].strip():
                parser.error("project")
            args.project = args.project[0]
        if args.json and not args.preview:
            parser.error("json requires preview")
        if args.command == "resume" and args.session is not None and not args.session.strip():
            parser.error("session")
    return args


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = None
    root = None
    connection = None
    try:
        args = parse_args(argv)
        if args is None:
            return 0
        if os.name != "nt":
            raise Problem("unsupported_platform", "현재 제품 구현의 지원 대상은 Windows입니다.", 5)
        if args.command == "projects":
            values = read_catalog(catalog_path())
            result = {"operation": "projects.list", "stage": "catalog", "status": "ready",
                      "message": "등록된 프로젝트 목록을 조회했습니다. 각 프로젝트의 실행 준비 여부는 별도 확인이 필요합니다.", "projects": values}
        else:
            cwd = Path.cwd()
            if args.project is not None:
                root = resolve_project(args.project, cwd)
            elif not args.select:
                root = resolve_project(None, cwd)
            if root is None:
                if args.json:
                    raise Problem("project_selection_required", "프로젝트 선택이 필요합니다. start --project PATH로 대상을 지정해 주세요.", 3)
                root = resolve_project(choose_project(read_catalog(catalog_path()), sys.stdin, sys.stdout), cwd)
            module = find_module(root)
            connection = resolve_connection(root, module)
            # 경로를 읽을 수 있어도 Codex의 권한 확인과 실행 준비가 끝난 것은 아니다.
            raise Problem("runtime_not_implemented", "프로젝트와 WIKI 위치를 확인했습니다. 연결 저장과 Codex의 권한 확인 및 실행은 아직 구현되지 않았습니다.", 5)
        code = 0
    except Problem as error:
        code = error.code
        if args is not None:
            operation = "projects.list" if args.command == "projects" else args.command
        elif argv[:2] == ["projects", "list"]:
            operation = "projects.list"
        else:
            operation = argv[0] if argv and argv[0] in ("start", "resume") else None
        result = {"operation": operation, "stage": "catalog" if operation == "projects.list" else "prepare",
                  "status": {1: "error", 2: "error", 3: "needs_input", 4: "cancelled", 5: "unsupported"}[code],
                  "reason": error.reason, "message": str(error)}
        if root is not None:
            result["project_root"] = str(root)
        if connection is not None:
            result["wiki_root"] = str(connection.wiki_root)
    except KeyboardInterrupt:
        code = 4
        result = {"operation": args.command if args is not None else None, "stage": "prepare",
                  "status": "cancelled", "reason": "user_cancelled", "message": "프로젝트 확인을 취소했습니다."}
    # '--' 뒤의 --json은 OnepLoom 옵션이 아니다. 파싱 실패도 해석 가능한 요청만 JSON 처리한다.
    own = list(argv)[:list(argv).index("--")] if "--" in argv else list(argv)
    json_output = args.json if args is not None else ("--json" in own and result["operation"] is not None)
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["message"], file=sys.stdout if code in (0, 3, 4, 5) else sys.stderr)
        if "projects" in result:
            for value in result["projects"]:
                print(value)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
