# CCCopy - Git 기반 팀 협업 도구

## 프로젝트 개요

CCCopy는 Python 3.7과 Git 1.8 환경에서 동작하는 팀 협업을 위한 Git 래퍼 도구입니다. Work와 Production 두 개의 Git 저장소를 관리하며, NFS 환경에서의 안전한 동시 접근을 보장합니다.
Python은 추가 PIP Package 없이 순수 python 환경을 지원합니다.

## 시스템 요구사항

- Python 3.7+
- Git 1.8+
- Linux/Unix 환경 (NFS 지원)
- 표준 라이브러리만 사용 (외부 의존성 없음)

## 주요 특징

### 이중 Git 저장소 관리
- **Work 저장소**: 개인 작업 공간 (개별 사용자)
- **Production 저장소**: 공유 프로덕션 환경 (글로벌 단일 저장소)

### 안전한 동시 접근 제어
- NFS 안전 락 매니저 (디렉토리 기반)
- 모든 Production 작업에 LockManager 적용
- 스테일 락 자동 정리 (5분 타임아웃)

### 지능형 파일 상태 관리
- **Git hash 기반 파일 비교**
- **Git 기반 상태 추적**
- **6가지 파일 상태 분류**:
  - `same`: Production과 Work가 동일
  - `modified`: 사용자가 Work에서 수정한 파일 (새로 생성된 파일 포함)
  - `updated`: Production에서 업데이트된 파일
  - `conflicted`: 양쪽 모두 수정되어 충돌 상황
  - `pending`: 상태 확인 중 (thread 처리 중)

### 보안 및 원자적 권한 관리
- **원자적 권한 상승**: Production 작업시에만 밀리초 단위로 최소 권한 상승
- **상세한 보안 감사 로깅**: 모든 권한 상승/복귀 시점과 소요시간 추적
- **Work 작업 보호**: Work 디렉토리/Git 작업은 원래 권한으로만 실행
- **실패 안전 설계**: 권한 복귀 실패시 치명적 오류로 즉시 중단

### Git Author 정확한 추적 시스템
- **Production: --author 옵션 사용**: 모든 커밋에 실제 작업자를 author로 명시
- **Dummy Committer 설정**: Production Git config는 `cccopy_admin <admin@cccopy.com>` (고정)
- **정확한 이력 추적**: 누가 언제 어떤 파일을 수정했는지 명확히 기록
- **Work: 개인 사용자 설정**: Work Git은 개인 계정 정보로 config 설정
- **Git 1.8 호환**: `git commit --author` 옵션 완전 지원

### .gitignore 중앙집중 관리
- **Production 중심 관리**: .gitignore는 Production에서만 수정 가능
- **자동 동기화**: DOWNLOAD시 Production → Work .gitignore 자동 복사
- **사용자 수정 방지**: Work .gitignore 수정시 백업 후 복원 및 경고
- **Git cache 자동 갱신**: .gitignore 변경시 `git rm -r --cached . && git add .` 자동 실행
- **업로드 차단**: Work .gitignore는 Production으로 업로드 불가

### 다중 프로젝트 관리 시스템
- **템플릿 기반 프로젝트 생성**: project/ 디렉토리의 .ini 파일을 템플릿으로 활용
- **프로젝트별 독립적 설정**: 각 프로젝트마다 개별 작업 디렉토리와 설정 관리
- **TAG 기반 프로젝트 식별**: 프로젝트 생성시 선택적 TAG 입력으로 구분
- **프로젝트 복제 기능**: 기존 프로젝트를 복사하여 새 프로젝트 빠른 생성

### TUI 인터페이스
- **Curses 기반 대화형 인터페이스**: 직관적인 키보드 조작
- **이중 뷰 모드**: TreeView(계층 구조) ⟷ ListView(평면 목록) 전환 (`+/-` 키)
- **뷰 모드 영속성**: 종료시 선택한 뷰 모드 자동 저장 및 복원
- **실시간 파일 상태 표시**: 색상으로 파일 상태 구분
- **한글 완전 지원**: 한글 폭 정확 처리로 깨지지 않는 UI
- **파일 변경 자동 감지**: Work 디렉토리 파일 수정시 자동 새로고침

## 디렉토리 구조

```
/home/work/code/cccopy/
├── main.py                 # 진입점 (TUI/CLI 통합)
├── cccopy/                 # 메인 패키지
│   ├── __init__.py        # Public API export
│   ├── core/              # 핵심 기능
│   │   ├── __init__.py
│   │   ├── lock_manager.py   # NFS 안전 락 관리자
│   │   └── git_helper.py     # Git 명령어 래퍼
│   ├── models/            # 데이터 모델
│   │   ├── __init__.py
│   │   └── file_state.py     # FileState enum
│   ├── utils/             # 유틸리티
│   │   ├── __init__.py
│   │   ├── ui_handler.py     # UI 핸들러, messagebox
│   │   ├── helpers.py        # 헬퍼 함수들
│   │   ├── permissions.py    # 원자적 권한 관리
│   │   ├── preference.py     # 전역 환경설정
│   │   ├── config.py         # 프로젝트 관리
│   │   └── file_utils.py     # 파일 처리 유틸리티
│   ├── ui/                # 사용자 인터페이스
│   │   ├── __init__.py
│   │   ├── cli.py            # CLI 모드
│   │   └── tui.py            # TUI 모드 (Curses)
│   └── apps/              # 애플리케이션 플러그인
│       ├── __init__.py
│       └── fortune/          # Fortune 앱 (샘플)
│           ├── __init__.py
│           └── main.py
├── project/               # 프로젝트 템플릿
│   ├── test_project.ini
│   └── dev_project.ini
└── CLAUDE.md              # 프로젝트 문서

~/.cccopy/                 # 사용자 설정 디렉토리
├── config.ini             # 전역 설정
├── preference/            # 전역 환경설정
│   └── cccopy.ini
├── log/                   # 로그 파일
└── NNNN/                  # 프로젝트별 설정
    └── config.ini
```

## 실행 방법

```bash
cd /home/work/code/cccopy
python3 main.py
```

**실행 특징:**
- **TUI 모드가 기본값**: 자동으로 Curses 기반 TUI 인터페이스 시작
- **자동 환경 감지**: Curses 미지원 환경에서는 CLI 모드로 자동 전환
- **단일 진입점**: 하나의 명령어로 최적의 인터페이스 제공

## TUI 명령어

### 메인 화면
- `Q` / `ESC`: 종료 (뷰 모드 자동 저장)
- `M`: Work ⟷ Production 모드 전환
- `D`: Download (Production → Work)
- `U`: Upload (Work → Production)
- `S`: Save (Work 커밋)
- `H`: History 조회
  - 히스토리 내 `F`: 파일명 필터
- `P`: Project 관리 (생성/전환/삭제/복제)
- `T`: Terminal 열기 (현재 디렉토리)
- `R` / `F5`: 새로고침
- `+` / `-`: TreeView ⟷ ListView 전환
- `↑/↓`: 파일/항목 선택
- `Space`: 폴더 펼치기/접기 (TreeView 모드)
- `Enter`: 파일 상세 정보 / 선택 실행
- `Tab`: 포커스 전환
- `F2`: 도움말

## 주요 기능 상세

### 1. Download (Production → Work)

**첫 다운로드**:
- Production Git 저장소 초기화
- Work Git 저장소 초기화
- Production → Work .gitignore 복사
- 파일 복사 및 초기 커밋

**이후 다운로드**:
- .gitignore 자동 동기화
- 선별적 자동 커밋 (새 파일만)
- 충돌 발생시 VS Code diff 지원

### 2. Upload (Work → Production)

**업로드 프로세스**:
1. .gitignore 패턴 적용
2. Modified 파일만 업로드
3. .gitignore 업로드 차단
4. Production 직접 수정 내용 자동 커밋
5. Work → Production 파일 복사
6. 사용자 커밋 메시지로 최종 커밋

### 3. Save (Work 커밋)

- Work 저장소의 변경사항 커밋
- `git add --all .` 사용 (Git 1.8 호환)
- 사용자 커밋 메시지 입력

### 4. History (이력 조회)

**TUI 히스토리 뷰어**:
- 커밋 목록 탐색 (↑↓)
- 상세 정보 표시
- 파일명 필터 기능 (`F` 키)
- 브랜치별 조회 (Work/Production)

### 5. Project (프로젝트 관리)

**신규 프로젝트 생성**:
- 템플릿 선택
- 작업 디렉토리 입력
- TAG 입력 (한글 지원)
- 설정 커스터마이징 (선택)

**프로젝트 복제**:
- 설정 파일 복사
- 작업 파일 복사 (.git 제외)
- 새 TAG 자동 생성
- CLI/TUI 모두 지원

## 설정 파일

### 프로젝트 템플릿 (project/*.ini)

```ini
[CONFIG]
PROJECT_NAME=example_project
PRODUCTION_DIR=/path/to/production        # 절대 경로
WORKING_BASE_DIR=/path/to/work            # 절대 경로
CONFIG_VERSION=1

[SOURCES]
00=src/**              # 모든 하위 파일 포함
01=docs/**             # 문서 디렉토리

[EXCLUDES]
00=**/node_modules/    # Node.js 모듈
01=**/.git/            # Git 디렉토리
02=**/__pycache__/     # Python 캐시

[UPLOAD]
GROUP=work             # 업로드시 사용할 그룹 권한

[LOG]
PATH=/tmp/log/
```

**경로 확장 지원:**
- **`~` (홈 디렉토리)**: `~/work/abc` → `/home/username/work/abc`
- **환경변수**: `${MY_WORK}/abc` 또는 `$MY_WORK/abc` → `/data/myproject/abc`
- **절대경로**: `/absolute/path` → `/absolute/path` (그대로 유지)

**예시:**
```ini
# 홈 디렉토리 사용
PRODUCTION_DIR=~/projects/production
WORKING_BASE_DIR=~/work/project1

# 환경변수 사용
PRODUCTION_DIR=${PROJECT_ROOT}/production
WORKING_BASE_DIR=${WORK_BASE}/myproject

# 혼합 사용
PRODUCTION_DIR=~/projects/${PROJECT_NAME}/prod
WORKING_BASE_DIR=${HOME}/work/${PROJECT_NAME}
```

### 전역 환경설정 (~/.cccopy/preference/cccopy.ini)

```ini
[VSCODE]
# VS Code 실행 파일 경로 (선택 사항)
# PATH=/usr/bin/code

[VIEW]
# 마지막 사용한 뷰 모드 (자동 저장/복원)
# MODE=tree  # 또는 list
```

## 보안 및 권한 관리

### 원자적 권한 관리

```python
# Production 작업시에만 밀리초 단위로 권한 상승
with AtomicProductionPermission(group_name):
    # Production 작업 (파일 읽기/쓰기, Git 명령)
    perform_production_operations()
# 자동으로 원래 권한 복귀
```

**보안 원칙:**
- Production 작업만 권한 상승
- Work 작업은 원래 권한
- 원자적 실행 (각 작업마다 독립적)
- 상세 감사 로깅
- 실패시 즉시 중단

### Git Author 추적

```python
# Production: --author로 실제 작업자 기록
git commit --author="username <username@cccopy.com>" -m "message"

# Work: git config 사용
git commit -m "message"
```

## 성능 최적화

### Partial Refresh 시스템
- **즉시 UI 응답**: 초기 실행시 0.1초 이내 화면 표시
- **파일시스템 기반 스캔**: 현재 디렉토리만 non-recursive 스캔
- **Background Git 로딩**: Git 명령을 background thread에서 실행
- **파일 상태 캐싱**: 5분 타임아웃으로 성능 최적화

### 실시간 파일 변경 감지
- **자동 감지 및 새로고침**: Work 디렉토리 파일 변경 자동 감지
- **Git 기반 정확한 감지**: `git status --porcelain` 사용
- **선별적 캐시 갱신**: 변경된 파일만 무효화
- **Thread 기반 비동기 처리**: UI blocking 없음

## 문제 해결

### 락 타임아웃 발생
```bash
# 스테일 락 수동 제거
rm -rf /production/.cccopy/lock/*.lockdir
```

### 권한 오류
```bash
# 그룹 멤버십 확인
groups
id
```

### VS Code를 찾을 수 없는 문제

**방법 1: 설정 파일에 경로 지정 (권장)**
```ini
# ~/.cccopy/preference/cccopy.ini
[VSCODE]
PATH=/opt/vscode/bin/code
```

**방법 2: 환경변수 설정**
```bash
export VSCODE_PATH=/opt/vscode/bin/code    # bash
setenv VSCODE_PATH /opt/vscode/bin/code    # csh/tcsh
```

## 버전 이력

- **v1.1** (2025-11-07): TreeView/ListView 이중 모드 및 뷰 모드 영속성 지원
- **v1.0** (2025-10-25): 초기 버전

## 라이센스

프로젝트별 라이센스 정책을 따릅니다.

## 작성자

Generated with Claude Code
