"""로컬과 CI에서 같은 Windows 전체 검사와 성공 기준을 사용한다."""

from pathlib import Path
import platform
import subprocess
import sys
import unittest


def run_tests(root: Path) -> int:
    """지정한 저장소의 전체 검사를 실행하고 누락도 실패로 판정한다."""
    suite = unittest.TestLoader().discover(str(root / "tests"), top_level_dir=str(root))
    if suite.countTestCases() == 0:
        print("No tests were discovered.", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print("Skipped tests do not count as a complete verification.", file=sys.stderr)
    return 0 if result.wasSuccessful() and not result.skipped else 1


def main() -> int:
    """실행 환경을 확인한 뒤 이 스크립트가 속한 저장소를 검사한다."""
    if sys.platform != "win32":
        print("Run the complete test suite on Windows.", file=sys.stderr)
        return 2
    print(platform.platform(), flush=True)
    print(sys.version, flush=True)
    try:
        subprocess.run(["git", "--version"], check=True)
        subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "$PSVersionTable.PSVersion.ToString()",
        ], check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Cannot verify the test environment: {error}", file=sys.stderr)
        return 2
    return run_tests(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
