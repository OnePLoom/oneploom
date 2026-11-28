# OnepLoom

작업에 필요한 지침과 검증을 선택하고, 프로젝트의 로컬 WIKI를 개발 흐름에 연결하는 조건부 하네스다. 기존 프로젝트에는 코드와 Git 이력을 근거로 WIKI를 생성하거나 보강하는 기능을 제공할 계획이다.

## 현재 상태

명령 입력, 프로젝트 선택, 설치 설정과 등록된 WIKI 위치의 읽기 전용 확인을 구현한 개발 작업본이다. 설치기, 연결 저장, 배포 패키지와 Codex 실행 연결은 아직 없다. 프로젝트와 WIKI 경로를 찾은 결과를 설치 또는 전체 실행 준비의 완료로 표시하지 않는다.

연결 저장의 기반으로 기존 SQLite DB의 조회/쓰기 트랜잭션을 구현했다. 이 내부 저장 코드는 아직 CLI에 연결하지 않았으며, DB 생성과 변경 계획/승인 기록, 실제 설정 파일 반영은 후속이다.

첫 지원 대상은 Windows다. 현재 설치된 Codex의 공식 npm 설치와 공식 Windows 설치를 지원할 계획이며 사용자 모델과 Codex 전역 설정을 임의로 변경하지 않는다. 실제 지원 버전은 구현과 검증 후 안내한다.

## 개발 작업본 실행

Windows에서 Python과 Git을 준비한 뒤 이 코드 저장소 루트에서 실행한다. 별도 Python 패키지 설치는 필요하지 않다. 사용자 명령 `oneploom`의 설치는 아직 제공하지 않는다.

```powershell
py -3 -B -m oneploom.cli --help
py -3 -B -m oneploom.cli projects list --json
```

`projects list`는 등록된 경로만 읽으며 목록 파일이 없으면 빈 목록을 반환한다. 프로젝트나 WIKI를 만들지 않는다. 현재 조회 코드는 Windows 11, Python 3.13.9와 Git 2.52.0.windows.1에서 검증했으며, 다른 버전의 지원 범위는 배포 검증에서 정한다.

`start`와 `resume`은 대상 선택 뒤 내부 WIKI 또는 로컬에 등록한 외부 WIKI, 그 안의 위치표 파일을 확인한다. 없는 경로, 영역을 벗어나는 연결과 읽기 실패를 구분하고 기존 파일은 수정하지 않는다. 경로 확인에 성공해도 `runtime_not_implemented`와 종료 코드 5를 반환한다. 위치표 내용의 검증, Codex 권한 확인과 실제 실행은 아직 제공하지 않는다. 이번 소스의 `oneploom/`을 사용자 프로젝트에 복사하는 설치 방법은 제공하지 않는다.

구현 검사는 [CONTRIBUTING.md의 검사와 문서](CONTRIBUTING.md#검사와-문서)를 따른다.

## 개발 문서

- 작업 규칙과 검토: [CONTRIBUTING.md](CONTRIBUTING.md)
- AI 작업 진입 규칙: [AGENTS.md](AGENTS.md)
- 제품 요구와 설계: [OnepLoom 개발 WIKI](https://github.com/OnePLoom/oneploom-wiki)의 README.md와 REQUIREMENTS.md

코드와 WIKI는 OnePLoom Organization 아래의 독립 저장소다. 제품 소스는 [oneploom](https://github.com/OnePLoom/oneploom), 개발 지식은 위 WIKI에서 관리한다. 로컬에서는 같은 작업 공간의 코드 폴더와 wiki 폴더로 나누어 사용할 수 있다.

## 구현 순서

1. 명령 해석, 도움말, 프로젝트 발견과 설정 읽기.
2. WIKI 연결 계획/저장, 현재 Codex로 시작과 선택적 재개.
3. 공식 배포 자료를 이용한 설치, 사용자별 짧은 명령 준비와 복구.
4. 조건부 검증, 상세 WIKI 복원, 작업 재개와 코드/WIKI PR 연계의 완성 및 실제 Windows 검증.

첫 단계만 끝낸 결과를 전체 제품 완료로 배포하지 않는다. 실행 안내는 실제 구현과 검증된 명령이 생기면 준비물 → 설치 → 기기별 연결 → 평소 사용 → 실패 대응 순서로 제공한다.

## 배포 상태

GitHub 저장소 주소와 MIT 라이선스를 준비했으며, 릴리스는 아직이다. 저장소 준비를 실행 가능한 제품의 배포로 보지 않는다. 외부 코드나 배포 파일을 편입하기 전에 해당 출처와 이용 조건을 확인한다.

## 라이선스

OnepLoom의 코드와 문서는 [MIT License](LICENSE)를 따른다. 저작권 고지와 허가문을 유지하는 조건으로 사용, 수정과 재배포를 허용한다.
