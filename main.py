#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Main Entry Point for CCCopy
CCCopy 메인 진입점
"""

import os
import sys
import configparser
import getpass
import grp
import pwd

# cccopy 모듈에서 필요한 것들 import
from cccopy import (
    ProjectManager,
    PreferenceManager,
    FileState,
    CCCopyError,
    GitHelper,
    safe_input,
    display_message,
    set_ui_handler,
    set_tui_initializing,
    _cli_messagebox
)

# cccopy.ui 모듈에서 CLI 모드 import
from cccopy.ui import run_cli_mode

CCCOPY_VERSION_MAJOR = 1
CCCOPY_VERSION_MINOR = 1
CCCOPY_VERSION       = f'{CCCOPY_VERSION_MAJOR}.{CCCOPY_VERSION_MINOR}'

# ============================================================================
# 전역 상수 (Global Constants)
# ============================================================================

# 로그 관련 상수
# LOG_DIR은 tui.py에 정의되어 있음 (고정 경로이므로 중복 불필요)
MAX_LOG_LINES = 1024          # 로그 파일당 최대 라인 수 (초과시 새 파일 생성)
MAX_LOG_FILES =  256          # 최대 보관 로그 파일 개수 (오래된 것 자동 삭제)

# 캐시 타임아웃 상수 (초 단위)
PARTIAL_REFRESH_CACHE_TIMEOUT = 300  # 5분
# - 파일 상태 캐시: TUI에서 파일 상태 확인 결과 캐싱
# - Git tracked files 캐시: git ls-files 결과 캐싱
# - Production 자동 커밋 체크 캐시: Production 변경 확인 결과 캐싱

# Thread Pool 설정
MAX_STATE_CHECK_WORKERS = 2   # 파일 상태 체크용 Thread Pool의 worker 수
# - 동시에 2개의 파일 상태를 백그라운드에서 체크
# - 너무 많으면 CPU 과부하, 너무 적으면 느림

# Watch 시스템 설정
WATCH_FILE_CHANGE_INTERVAL = 5  # 파일 변화 감지 체크 주기 (초 단위)
# - Work 디렉토리의 현재 디렉토리만 감지
# - 너무 짧으면 CPU/I/O 과부하, 너무 길면 반응 느림
# - 권장값: 3~10초


# ============================================================================
# 환경 변수 (Environment Variables)
# ============================================================================
#
# CCCopy는 다음 환경 변수를 지원합니다:
#
# 1. CCCOPY_VSCODE_PATH
#    - 설명: VS Code 실행 파일 경로 지정
#    - 용도: 충돌 파일 비교 시 VS Code 사용
#    - 우선순위: 설정 파일 > 환경변수 > which/where 탐색 > 표준 경로
#    - 예시:
#      export CCCOPY_VSCODE_PATH=/opt/vscode/bin/code    # bash
#      setenv CCCOPY_VSCODE_PATH /opt/vscode/bin/code    # csh/tcsh
#
# 2. CCCOPY_GIT_BIN_PATH
#    - 설명: Git 실행 파일 경로 지정
#    - 용도: 비표준 경로의 Git 사용 (기본값: 'git')
#    - 예시:
#      export CCCOPY_GIT_BIN_PATH=/opt/git/bin/git       # bash
#      setenv CCCOPY_GIT_BIN_PATH /opt/git/bin/git       # csh/tcsh
#
# 3. CCCOPY_PROJECT_TEMPLATE_DIR
#    - 설명: 프로젝트 템플릿 디렉토리 경로 지정
#    - 용도: 기본 project/ 디렉토리 대신 사용자 정의 경로 사용
#    - 설정하지 않았을 때: <cccopy_root>/project/
#    - 예시:
#      export CCCOPY_PROJECT_TEMPLATE_DIR=/home/user/my_templates  # bash
#      setenv CCCOPY_PROJECT_TEMPLATE_DIR /home/user/my_templates  # csh/tcsh
#      export CCCOPY_PROJECT_TEMPLATE_DIR=~/custom_templates       # bash (~ 지원)
#
# ============================================================================


def cleanup_old_log_files():
    """오래된 로그 파일 삭제 (MAX_LOG_FILES 개수 유지)"""
    from cccopy.ui.tui import LOG_DIR  # tui.py에서 LOG_DIR import
    try:
        # 로그 디렉토리가 없으면 생성
        os.makedirs(LOG_DIR, exist_ok=True)

        # 로그 파일 목록 가져오기 (*.log)
        log_files = []
        for filename in os.listdir(LOG_DIR):
            if filename.endswith('.log'):
                filepath = os.path.join(LOG_DIR, filename)
                # 파일 수정 시간 기준으로 정렬하기 위해 (mtime, filepath) 튜플 저장
                log_files.append((os.path.getmtime(filepath), filepath))

        # 수정 시간 기준 오름차순 정렬 (오래된 것이 앞에)
        log_files.sort()

        # MAX_LOG_FILES 초과시 오래된 파일부터 삭제
        if len(log_files) > MAX_LOG_FILES:
            files_to_delete = log_files[:len(log_files) - MAX_LOG_FILES]
            for mtime, filepath in files_to_delete:
                try:
                    os.remove(filepath)
                except Exception as e:
                    # 삭제 실패는 무시
                    pass
    except Exception as e:
        # 로그 정리 실패는 무시 (프로그램 실행에 영향 없음)
        pass


def show_startup_fortune(preference, ui_handler=None):
    """앱 시작 시 운세 표시 (조건부)

    Args:
        preference: PreferenceManager 인스턴스
        ui_handler: UI 핸들러 (TUI 모드일 때 사용)
    """
    try:
        import datetime
        from cccopy.apps.fortune.main import calculate_fortune_index, d_f

        # APP.FORTUNE.STARTUP_SHOW 설정 확인
        show_fortune = preference.get('', 'APP.FORTUNE.STARTUP_SHOW')
        if show_fortune != 'ON':
            return  # OFF이면 운세 표시 안 함

        # 오늘 날짜 가져오기 (yyyymmdd 형식)
        today = datetime.datetime.now().strftime('%Y%m%d')

        # APP.FORTUNE.STARTUP_TODAY 확인 (마지막으로 운세를 표시한 날짜)
        last_shown = preference.get('', 'APP.FORTUNE.STARTUP_TODAY')

        if last_shown == today:
            # 오늘 이미 운세를 표시했으면 무시
            return

        # 생년월일시 가져오기
        birth = preference.get('', 'APP.FORTUNE.BIRTH')
        if not birth or len(birth) != 10:
            display_message("운세를 표시하려면 APP.FORTUNE.BIRTH를 설정하세요 (yyyymmddhh 형식)", "INFO")
            return

        # 운세 계산
        fortune_index = calculate_fortune_index(birth, today)
        fortune_data = d_f()

        if not fortune_data or fortune_index >= len(fortune_data):
            display_message("운세 데이터를 불러올 수 없습니다", "DEBUG")
            return

        result = fortune_data[fortune_index]

        # 운세 표시 (다이얼로그 또는 로그)
        try:
            if ui_handler:
                # TUI 모드: 다이얼로그로 표시
                ui_handler.messagebox(
                    result,
                    "오늘의 운세",
                    "info",
                    "ok"
                )
            else:
                # CLI 모드: 로그로 표시
                display_message("=" * 60, "HIGH")
                display_message("오늘의 운세", "HIGH")
                display_message("=" * 60, "HIGH")
                for line in result.split('\n'):
                    if line.strip():
                        display_message(line, "INFO")
                display_message("=" * 60, "HIGH")
        except Exception as dialog_error:
            # 다이얼로그 표시 실패는 로그로 기록하고 계속 진행
            display_message(f"운세 다이얼로그 표시 실패: {dialog_error}", "DEBUG")

        # 오늘 날짜를 APP.FORTUNE.STARTUP_TODAY에 저장 (표시 성공 여부와 무관)
        preference.set('', 'APP.FORTUNE.STARTUP_TODAY', today)
        preference.save()

    except Exception as e:
        # 운세 표시 실패는 무시 (프로그램 진행에 영향 없음)
        display_message(f"운세 표시 실패: {e}", "DEBUG")


def main():
    """메인 함수 - 모드에 따라 TUI 또는 CLI 실행"""
    # 오래된 로그 파일 정리
    cleanup_old_log_files()

    # 실행 모드 설정 (TUI일 때 초기화 로그 버퍼링)
    mode = "tui"  # 기본값은 TUI 모드
    if mode == "tui":
        set_tui_initializing(True)

    # 전역 환경설정 초기화
    preference = None
    try:
        preference = PreferenceManager()
        display_message("전역 환경설정 로드 완료", "INFO")
    except Exception as e:
        display_message(f"전역 환경설정 로드 실패: {e}", "ERROR")
        # 환경설정 실패는 프로그램 진행에 치명적이지 않으므로 계속 진행

    try:
        workspace = ProjectManager(cache_timeout=PARTIAL_REFRESH_CACHE_TIMEOUT)
    except Exception as e:
        display_message(f"초기화 실패: {e}", "ERROR")
        sys.exit(1)

    # CLI 모드에서 프로젝트 선택이 필요한 경우만 미리 처리
    if mode != "tui" and workspace.needs_project_selection():
        display_message("프로젝트 관리가 필요합니다 (신규 생성 또는 기존 선택)", "INFO")
        # ProjectSelectionManager를 사용하여 프로젝트 관리 메뉴 표시
        from cccopy.utils.config import ProjectSelectionManager
        project_manager = ProjectSelectionManager(workspace)
        project_manager.show_project_management_menu()

        # 프로젝트 선택이 완료되었는지 확인
        if workspace.needs_project_selection():
            display_message("프로젝트 설정이 취소되었습니다. 프로그램을 종료합니다.", "WARN")
            sys.exit(0)

    # 모드에 따라 분기 실행
    if mode == "tui":
        run_tui_mode(workspace, preference)
    else:
        # CLI 모드: 로그로 운세 표시
        if preference:
            show_startup_fortune(preference)
        run_cli_mode(workspace)


def run_tui_mode(workspace, preference=None):
    """TUI 모드 실행

    Args:
        workspace: ProjectManager 인스턴스
        preference: PreferenceManager 인스턴스 (옵션)
    """
    try:
        # cccopy.ui.tui 모듈을 동적으로 import
        from cccopy.ui import tui as cccopy_tui
        # TUI에 필요한 클래스들 설정
        cccopy_tui.set_cccopy_classes(ProjectManager, FileState, CCCopyError, GitHelper, safe_input)
        # TUI에 전역 상수 전달
        cccopy_tui.set_global_constants(
            CCCOPY_VERSION=CCCOPY_VERSION,
            MAX_LOG_LINES=MAX_LOG_LINES,
            MAX_LOG_FILES=MAX_LOG_FILES,
            PARTIAL_REFRESH_CACHE_TIMEOUT=PARTIAL_REFRESH_CACHE_TIMEOUT,
            MAX_STATE_CHECK_WORKERS=MAX_STATE_CHECK_WORKERS,
            WATCH_FILE_CHANGE_INTERVAL=WATCH_FILE_CHANGE_INTERVAL
        )
        # TUI용 UI 핸들러 클래스 생성
        class TUIHandler:
            def __init__(self):
                self.tui = None
            def set_tui(self, tui):
                self.tui = tui
            def display_message(self, message, level="INFO"):
                if self.tui:
                    self.tui.add_log(message, level)
                else:
                    # fallback - TUI 없이는 출력하지 않음 (화면 깨짐 방지)
                    pass
            def messagebox(self, message, title="", message_type="info", buttons="ok", default=""):
                if self.tui:
                    return self.tui.messagebox(message, title, message_type, buttons, default)
                else:
                    # fallback to CLI messagebox
                    return _cli_messagebox(message, title, message_type, buttons, default)
        # UI 핸들러 설정
        tui_handler = TUIHandler()
        set_ui_handler(tui_handler)

        # TUI 초기화 완료, 플래그 해제
        set_tui_initializing(False)

        # TUI 실행 (preference 전달)
        tui = cccopy_tui.CCCopyTUI(workspace, preference=preference)
        tui_handler.set_tui(tui)

        # 버퍼된 초기화 로그들을 TUI에 출력
        init_logs = cccopy_tui.get_and_clear_init_logs()
        for message, level in init_logs:
            tui.display_message(message, level)
        # 환경 변수로 강제 텍스트 모드 선택 가능
        if os.environ.get('CCCOPY_FORCE_TEXT_MODE') or '--text' in sys.argv:
            display_message("텍스트 모드로 실행합니다...", "INFO")
            tui.run_simple_tui()
            return
        # Curses 실행 시도
        try:
            import curses
            curses.wrapper(tui.main_loop)
        except Exception as e:
            display_message(f"Curses 실행 실패: {e}", "ERROR")
            display_message("텍스트 모드로 전환합니다...", "INFO")
            tui.run_simple_tui()

    except ImportError as e:
        display_message(f"TUI 모듈 로드 실패: {e}", "ERROR")
        display_message("CLI 모드로 fallback 실행...", "INFO")
        # CLI 모드 fallback
        run_cli_mode(workspace)


def check_and_reexec_with_group():
    """필요한 그룹 권한이 있는지 확인하고, 없으면 sg로 재실행"""
    # 환경 변수로 이미 재실행했는지 확인 (무한 루프 방지)
    if os.environ.get('CCCOPY_REEXEC_DONE'):
        return

    try:
        # config.ini에서 필요한 그룹 읽기
        config_path = os.path.join(os.path.dirname(__file__), 'config', 'config.ini')
        if not os.path.exists(config_path):
            return  # 설정 파일 없으면 그냥 진행

        config = configparser.ConfigParser(inline_comment_prefixes=(';', '#'))
        config.read(config_path, encoding='utf-8')

        if not config.has_option('UPLOAD', 'GROUP'):
            return  # 그룹 설정 없으면 그냥 진행

        required_group = config.get('UPLOAD', 'GROUP').strip()
        if not required_group:
            return

        # 필요한 그룹 gid 확인 (먼저 수행)
        try:
            group_info = grp.getgrnam(required_group)
            required_gid = group_info.gr_gid
        except KeyError:
            # 메시지 없이 조용히 반환 (그룹이 없으면 재실행 불필요)
            return

        # 현재 effective gid 확인
        current_egid = os.getegid()

        # 이미 올바른 그룹이면 그냥 진행 (재실행 불필요)
        if current_egid == required_gid:
            return

        # 사용자가 해당 그룹에 속해 있는지 확인
        current_user = getpass.getuser()
        if current_user not in group_info.gr_mem:
            # 추가로 primary group도 확인
            try:
                user_info = pwd.getpwnam(current_user)
                if user_info.pw_gid != required_gid:
                    print(f"[오류] 사용자 '{current_user}'는 '{required_group}' 그룹에 속해있지 않습니다.")
                    sys.exit(1)
            except KeyError:
                pass

        # sg 명령어로 재실행 (메시지 출력 없이 조용히 수행)
        env = os.environ.copy()
        env['CCCOPY_REEXEC_DONE'] = '1'

        # 현재 스크립트와 인자들
        args = [sys.executable] + sys.argv
        cmd = ['sg', required_group, '-c', ' '.join(f'"{arg}"' if ' ' in arg else arg for arg in args)]

        os.execvpe('sg', cmd, env)

    except Exception as e:
        print(f"[경고] 그룹 권한 확인 중 오류: {e}")
        # 오류가 나도 그냥 진행 (기존 동작 유지)
        return


if __name__ == "__main__":
    # 그룹 권한 체크 및 자동 재실행
    check_and_reexec_with_group()

    # 메인 실행
    main()
