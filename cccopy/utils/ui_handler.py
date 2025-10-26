"""UI 핸들러 모듈 - CLI/TUI 출력 통합 관리"""
import datetime
import sys

# UI 모드별 출력 핸들러
_ui_handler = None
_tui_initializing = False  # TUI 초기화 중인지 여부


def set_ui_handler(handler):
    """UI 핸들러 설정"""
    global _ui_handler
    _ui_handler = handler


def set_tui_initializing(flag):
    """TUI 초기화 상태 설정"""
    global _tui_initializing
    _tui_initializing = flag


def display_message(message, level="INFO"):
    """UI 모드에 따른 메시지 출력"""
    # 로그 레벨을 일정한 글자수로 포맷팅
    level_formatted = {
        "INFO": "[INFO ]",
        "WARNING": "[WARN ]",
        "ERROR": "[ERROR]",
        "DEBUG": "[DEBUG]",
        "HIGH": "[HIGH ]"  # sg 명령 실행 결과용 하이 프라이어리티 레벨
    }.get(level, f"[{level}]")

    if _ui_handler:
        _ui_handler.display_message(message, level_formatted)
    elif _tui_initializing:
        # TUI 초기화 중이면 로그를 버퍼에 저장
        try:
            from ..ui import tui
            tui.add_init_log(message, level_formatted)
        except ImportError:
            # TUI 모듈이 없으면 기본 출력
            timestamp = datetime.datetime.now().strftime("%y%m%d %H:%M:%S")
            # HIGH 레벨은 CYAN(밝은 청록색) ANSI 색상 적용
            if level == "HIGH":
                print(f"\033[1;36m{timestamp} {level_formatted} {message}\033[0m")
            else:
                print(f"{timestamp} {level_formatted} {message}")

        # TUI 초기화 중이더라도 ERROR/WARNING은 즉시 stderr 출력
        # (Curses 시작 전 치명적 오류를 사용자가 볼 수 있도록)
        if level in ("ERROR", "WARNING"):
            timestamp = datetime.datetime.now().strftime("%y%m%d %H:%M:%S")
            print(f"{timestamp} {level_formatted} {message}", file=sys.stderr)
    else:
        # 기본 CLI 모드 - 타임스탬프 추가
        timestamp = datetime.datetime.now().strftime("%y%m%d %H:%M:%S")
        # HIGH 레벨은 CYAN(밝은 청록색) ANSI 색상 적용
        if level == "HIGH":
            print(f"\033[1;36m{timestamp} {level_formatted} {message}\033[0m")
        else:
            print(f"{timestamp} {level_formatted} {message}")


def messagebox(message, title="", message_type="info", buttons="ok", default=""):
    """
    범용 메시지박스 함수
    - message: 표시할 메시지
    - title: 대화상자 제목 (TUI용)
    - message_type: "info", "warn", "error"
    - buttons: "ok", "yesno", "yesnocancel", "input"
    - default: 기본 선택값
    """
    if _ui_handler and hasattr(_ui_handler, 'messagebox'):
        return _ui_handler.messagebox(message, title, message_type, buttons, default)
    else:
        # 기본 CLI 모드
        return _cli_messagebox(message, title, message_type, buttons, default)


def _cli_messagebox(message, title="", message_type="info", buttons="ok", default=""):
    """CLI 모드 메시지박스 구현"""
    from .helpers import safe_input

    # 메시지 타입별 프리픽스
    type_prefix = {
        "info": "[INFO]",
        "warn": "[WARNING]",
        "error": "[ERROR]"
    }.get(message_type, "[INFO]")

    # 제목이 있으면 표시
    if title:
        display_message(f"\n=== {title} ===", message_type.upper())

    # 메시지 출력
    display_message(f"{type_prefix} {message}", message_type.upper())

    # 버튼 타입별 처리
    if buttons == "ok":
        input("계속하려면 Enter를 누르세요...")
        return "ok"
    elif buttons == "yesno":
        prompt = "계속하시겠습니까? (y/N): " if not default else f"계속하시겠습니까? (y/N) [{default}]: "
        response = safe_input(prompt, default).lower()
        return "yes" if response in ('y', 'yes') else "no"
    elif buttons == "yesnocancel":
        prompt = f"선택하세요 (y/n/c): [{default}]: " if default else "선택하세요 (y/n/c): "
        response = safe_input(prompt, default).lower()
        if response in ('y', 'yes'):
            return "yes"
        elif response in ('n', 'no'):
            return "no"
        else:
            return "cancel"
    elif buttons == "input":
        print(f"\n{type_prefix} {message}")
        print("(빈 입력으로 취소, Ctrl+C로 종료)")
        result = safe_input("입력: ", default)
        if result == "":
            return None
        return result
    else:
        return "ok"
