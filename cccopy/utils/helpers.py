"""유틸리티 헬퍼 함수 모듈"""
import sys
import subprocess
import os
import configparser


def expand_path(path: str) -> str:
    """경로의 ~ 와 환경변수를 확장

    Args:
        path: 확장할 경로 (예: ~/work/abc, ${MY_WORK}/abc)

    Returns:
        str: 확장된 절대 경로

    Examples:
        >>> expand_path("~/work")
        "/home/username/work"
        >>> expand_path("${HOME}/work")
        "/home/username/work"
        >>> expand_path("$HOME/work")
        "/home/username/work"
    """
    if not path:
        return path

    # ~ 확장 (홈 디렉토리)
    path = os.path.expanduser(path)

    # 환경변수 확장 (${VAR} 또는 $VAR)
    path = os.path.expandvars(path)

    return path


def safe_input(prompt="", default=""):
    """EOF 안전한 input 함수"""
    from .ui_handler import display_message

    try:
        return input(prompt).strip()
    except EOFError:
        display_message(f"입력이 중단되었습니다. 기본값 '{default}' 사용", "WARNING")
        return default
    except KeyboardInterrupt:
        display_message("사용자가 중단했습니다.", "ERROR")
        sys.exit(1)


def check_command_exists(command):
    """명령어 존재 확인"""
    try:
        subprocess.run([command, '--version'],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False


def find_vscode_command():
    """VS Code 명령 찾기 - 다양한 환경 지원

    탐색 우선순위:
    1. 설정 파일 (config.ini - [VSCODE] PATH=...)
    2. 환경변수 (VSCODE_PATH)
    3. csh/tcsh의 where 명령 (alias 인식)
    4. which 명령
    5. 표준 경로 직접 탐색

    Returns:
        str: VS Code 실행 파일 경로, 없으면 None
    """
    # 1. 설정 파일에서 경로 확인
    try:
        config = configparser.ConfigParser()

        # 전역 설정 파일 시도
        config_paths = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'config.ini'),
            os.path.expanduser('~/.cccopy/config.ini')
        ]

        for config_path in config_paths:
            if os.path.exists(config_path):
                config.read(config_path)
                if config.has_option('VSCODE', 'PATH'):
                    vscode_path = config.get('VSCODE', 'PATH')
                    if vscode_path and os.path.isfile(vscode_path):
                        return vscode_path
    except Exception as e:
        from .ui_handler import display_message
        display_message(f"Config file read failed: {e}", "DEBUG")

    # 2. 환경변수 확인
    vscode_path = os.environ.get('CCCOPY_VSCODE_PATH')
    if vscode_path and os.path.isfile(vscode_path):
        return vscode_path

    # 3. where 및 which 명령으로 탐색
    for cmd in ['code', 'vscode']:
        # 3-1. csh의 where 명령 시도 (interactive mode, alias 인식)
        try:
            result = subprocess.run(
                ['csh', '-i', '-c', f'where {cmd}'],
                capture_output=True, text=True, timeout=3,
                stderr=subprocess.DEVNULL
            )
            if result.returncode == 0 and result.stdout.strip():
                # 첫 번째 경로 사용
                first_path = result.stdout.strip().split('\n')[0]
                if os.path.isfile(first_path):
                    return first_path
        except:
            pass

        # 3-2. tcsh의 where 명령 시도
        try:
            result = subprocess.run(
                ['tcsh', '-i', '-c', f'where {cmd}'],
                capture_output=True, text=True, timeout=3,
                stderr=subprocess.DEVNULL
            )
            if result.returncode == 0 and result.stdout.strip():
                first_path = result.stdout.strip().split('\n')[0]
                if os.path.isfile(first_path):
                    return first_path
        except:
            pass

        # 3-3. which 명령 시도 (fallback)
        try:
            result = subprocess.run(
                ['which', cmd],
                capture_output=True, text=True, timeout=3,
                stderr=subprocess.DEVNULL
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                if os.path.isfile(path):
                    return path
        except:
            pass

    # 4. 일반적인 경로 직접 탐색
    common_paths = [
        '/usr/bin/code',
        '/usr/local/bin/code',
        '/opt/vscode/bin/code',
        '/opt/VSCode-linux-x64/bin/code',
        os.path.expanduser('~/bin/code'),
        os.path.expanduser('~/.local/bin/code'),
    ]
    for path in common_paths:
        if os.path.isfile(path):
            return path

    return None


def get_parent_shell():
    """Parent process에서 shell 타입 감지 (bash, csh, tcsh 등)

    Returns:
        str: shell 실행 파일명 (bash, csh, tcsh, zsh 등), 없으면 'bash'
    """
    try:
        # 현재 프로세스 PID
        pid = os.getpid()

        # Parent process chain을 최대 10 레벨까지 탐색
        for _ in range(10):
            # ppid 가져오기
            try:
                result = subprocess.run(
                    ['ps', '-o', 'ppid=', '-p', str(pid)],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode != 0 or not result.stdout.strip():
                    break

                ppid = result.stdout.strip()
                if not ppid or ppid == '0' or ppid == '1':
                    break

            except Exception:
                break

            # parent process 이름 가져오기
            try:
                result = subprocess.run(
                    ['ps', '-o', 'comm=', '-p', ppid],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode != 0 or not result.stdout.strip():
                    pid = int(ppid)
                    continue

                pname = result.stdout.strip().lower()

                # 알려진 shell인지 확인
                known_shells = ['bash', 'sh', 'csh', 'tcsh', 'zsh', 'ksh', 'fish']
                for shell in known_shells:
                    if shell == pname or pname.endswith(f'/{shell}'):
                        return shell

            except Exception:
                pass

            # 다음 parent로 이동
            pid = int(ppid)

    except Exception:
        pass

    # 기본값: bash
    return 'bash'


def get_parent_terminal():
    """Parent process에서 터미널 프로그램 감지 (ps 명령어 기반)

    Returns:
        str: 터미널 실행 파일 경로, 없으면 None
    """
    # display_message를 안전하게 import (순환 import 방지)
    try:
        from .ui_handler import display_message
    except:
        def display_message(msg, level="INFO"):
            print(f"[{level}] {msg}")

    try:
        # 알려진 터미널 프로그램 목록 (쉘 스크립트와 동일)
        known_terminals = [
            'gnome-terminal', 'konsole', 'xterm', 'tilix', 'alacritty',
            'terminator', 'urxvt', 'kitty', 'mate-terminal', 'lxterminal',
            'io.elementary.terminal', 'xfce4-terminal', 'st', 'hyper',
            'guake', 'yakuake', 'ptyxis', 'rxvt', 'qterminal',
            'terminology', 'terminus', 'iterm2', 'terminal', 'wezterm',
            'foot', 'mlterm', 'sakura', 'tilda'
        ]

        # 현재 프로세스 PID
        pid = os.getpid()
        display_message(f"[DEBUG] 현재 PID: {pid}", "DEBUG")

        # Parent process chain을 최대 20 레벨까지 탐색
        for level in range(20):
            # ppid 가져오기
            try:
                result = subprocess.run(
                    ['ps', '-o', 'ppid=', '-p', str(pid)],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode != 0 or not result.stdout.strip():
                    display_message(f"[DEBUG] ppid 가져오기 실패 (레벨 {level})", "DEBUG")
                    break

                ppid = result.stdout.strip()
                if not ppid or ppid == '0' or ppid == '1':
                    display_message(f"[DEBUG] 루트 프로세스 도달: ppid={ppid}", "DEBUG")
                    break

                display_message(f"[DEBUG] 레벨 {level}: pid={pid} -> ppid={ppid}", "DEBUG")

            except Exception as e:
                display_message(f"[DEBUG] ppid 가져오기 예외: {e}", "DEBUG")
                break

            # parent process 이름 가져오기
            try:
                result = subprocess.run(
                    ['ps', '-o', 'comm=', '-p', ppid],
                    capture_output=True, text=True, timeout=2
                )
                if result.returncode != 0 or not result.stdout.strip():
                    display_message(f"[DEBUG] comm 가져오기 실패: ppid={ppid}", "DEBUG")
                    pid = int(ppid)
                    continue

                pname = result.stdout.strip()
                display_message(f"[DEBUG] Process name: {pname}", "DEBUG")

                # 알려진 터미널인지 확인
                for term in known_terminals:
                    if term in pname.lower() or pname.lower() in term:
                        display_message(f"[DEBUG] 터미널 발견: {pname} (매칭: {term})", "INFO")

                        # 프로세스명 정규화 (내부 프로세스 → 실제 실행 파일)
                        # ptyxis-agent → ptyxis
                        # gnome-terminal-server → gnome-terminal
                        normalized_name = pname
                        if 'ptyxis' in pname.lower():
                            normalized_name = 'ptyxis'
                        elif 'gnome-terminal' in pname.lower():
                            normalized_name = 'gnome-terminal'
                        elif 'konsole' in pname.lower():
                            normalized_name = 'konsole'

                        display_message(f"[DEBUG] 정규화된 이름: {normalized_name}", "DEBUG")

                        # 터미널 실행 파일 경로 찾기
                        # which 명령으로 경로 확인
                        try:
                            which_result = subprocess.run(
                                ['which', normalized_name],
                                capture_output=True, text=True, timeout=2
                            )
                            if which_result.returncode == 0 and which_result.stdout.strip():
                                path = which_result.stdout.strip()
                                display_message(f"[DEBUG] which로 경로 찾음: {path}", "INFO")
                                return path
                        except Exception as e:
                            display_message(f"[DEBUG] which 실패: {e}", "DEBUG")

                        # which 실패시 common path 탐색
                        common_paths = [
                            f'/usr/bin/{normalized_name}',
                            f'/usr/local/bin/{normalized_name}',
                            f'/bin/{normalized_name}',
                            f'/opt/{normalized_name}/bin/{normalized_name}',
                        ]
                        for path in common_paths:
                            if os.path.isfile(path):
                                display_message(f"[DEBUG] common path에서 찾음: {path}", "INFO")
                                return path

                        # 정확한 경로를 못 찾았지만 터미널 이름은 확인됨
                        # normalized_name 반환 (PATH에 있을 것으로 기대)
                        display_message(f"[DEBUG] 프로그램명으로 반환: {normalized_name}", "INFO")
                        return normalized_name

            except Exception as e:
                display_message(f"[DEBUG] comm 처리 예외: {e}", "DEBUG")
                pass

            # 다음 parent로 이동
            pid = int(ppid)

        display_message(f"[DEBUG] 모든 레벨 탐색 완료 - 터미널 미발견", "DEBUG")

    except Exception as e:
        display_message(f"[DEBUG] get_parent_terminal 예외: {e}", "DEBUG")

    # ps 명령 실패시 환경변수에서 확인
    term_env = os.environ.get('TERM_PROGRAM')
    display_message(f"[DEBUG] TERM_PROGRAM 환경변수: {term_env}", "DEBUG")
    if term_env:
        try:
            result = subprocess.run(
                ['which', term_env],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                display_message(f"[DEBUG] TERM_PROGRAM으로 찾음: {path}", "INFO")
                return path
        except Exception as e:
            display_message(f"[DEBUG] TERM_PROGRAM which 실패: {e}", "DEBUG")

    # fallback: 일반적인 터미널 명령어 탐색
    display_message(f"[DEBUG] Fallback: 일반 터미널 탐색 시작", "DEBUG")
    common_terminals = [
        'ptyxis', 'gnome-terminal', 'konsole', 'xterm', 'mate-terminal',
        'xfce4-terminal', 'lxterminal', 'qterminal', 'terminator', 'tilix',
        'alacritty', 'kitty', 'foot', 'wezterm'
    ]

    for term in common_terminals:
        try:
            result = subprocess.run(
                ['which', term],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                display_message(f"[DEBUG] Fallback으로 찾음: {term} -> {path}", "INFO")
                return path
        except Exception as e:
            display_message(f"[DEBUG] Fallback which {term} 실패: {e}", "DEBUG")
            continue

    display_message(f"[DEBUG] 모든 방법 실페 - None 반환", "DEBUG")
    return None


def launch_terminal(directory):
    """지정된 디렉토리에서 터미널 열기

    Args:
        directory: 터미널을 열 디렉토리 경로

    Returns:
        bool: 성공 여부
    """
    from .ui_handler import display_message

    if not os.path.isdir(directory):
        display_message(f"디렉토리를 찾을 수 없습니다: {directory}", "ERROR")
        return False

    # Parent terminal 감지
    terminal_path = get_parent_terminal()

    if not terminal_path:
        display_message("터미널 프로그램을 찾을 수 없습니다.", "ERROR")
        display_message("gnome-terminal, konsole, xterm 등을 설치하세요.", "ERROR")
        return False

    terminal_name = os.path.basename(terminal_path)
    display_message(f"{terminal_name}을 실행합니다: {directory}", "INFO")

    try:
        # 터미널별 실행 옵션
        if 'ptyxis' in terminal_name:
            # Fedora 42+ 기본 터미널
            # ptyxis -- bash -c "cd /tmp; exec bash" 형식
            # parent shell 감지
            parent_shell = get_parent_shell()
            display_message(f"[DEBUG] 감지된 shell: {parent_shell}", "DEBUG")

            subprocess.Popen(
                [terminal_path, '--', parent_shell, '-c', f'cd "{directory}"; exec {parent_shell}'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'gnome-terminal' in terminal_name:
            # gnome-terminal --working-directory=/path 형식
            subprocess.Popen(
                [terminal_path, f'--working-directory={directory}'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'konsole' in terminal_name:
            subprocess.Popen(
                [terminal_path, '--workdir', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'xfce4-terminal' in terminal_name:
            subprocess.Popen(
                [terminal_path, '--working-directory', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'mate-terminal' in terminal_name:
            subprocess.Popen(
                [terminal_path, '--working-directory', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'terminator' in terminal_name:
            subprocess.Popen(
                [terminal_path, '--working-directory', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'tilix' in terminal_name:
            subprocess.Popen(
                [terminal_path, '--working-directory', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'alacritty' in terminal_name:
            subprocess.Popen(
                [terminal_path, '--working-directory', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'kitty' in terminal_name:
            subprocess.Popen(
                [terminal_path, '--directory', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'wezterm' in terminal_name:
            subprocess.Popen(
                [terminal_path, 'start', '--cwd', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'foot' in terminal_name:
            subprocess.Popen(
                [terminal_path, '-D', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'lxterminal' in terminal_name or 'qterminal' in terminal_name or 'sakura' in terminal_name:
            subprocess.Popen(
                [terminal_path, '--working-directory', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'elementary' in terminal_name:
            # io.elementary.terminal
            subprocess.Popen(
                [terminal_path, '-w', directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        elif 'xterm' in terminal_name or 'rxvt' in terminal_name or 'st' == terminal_name:
            # xterm, urxvt, st: -e 옵션으로 cd 명령 실행
            subprocess.Popen(
                [terminal_path, '-e', 'bash', '-c', f'cd "{directory}" && exec bash'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
        else:
            # 기본: -e 방식으로 시도
            subprocess.Popen(
                [terminal_path, '-e', 'bash', '-c', f'cd "{directory}" && exec bash'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )

        return True

    except Exception as e:
        display_message(f"터미널 실행 중 오류: {e}", "ERROR")
        return False


def launch_text_editor(file_path):
    """텍스트 에디터를 실행하여 파일 편집 (동기 실행)
    우선순위: gedit -> gnome-text-editor

    Args:
        file_path: 편집할 파일 경로

    Returns:
        bool: 성공 여부
    """
    from .ui_handler import display_message

    try:
        editor = None
        editor_name = None

        # 1순위: gedit 확인 (실제 실행 가능 여부 테스트)
        try:
            result = subprocess.run(['gedit', '--version'],
                                   capture_output=True,
                                   text=True,
                                   timeout=2)
            if result.returncode == 0:
                editor = 'gedit'
                editor_name = 'gedit'
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # 2순위: gnome-text-editor 확인
        if not editor:
            try:
                result = subprocess.run(['gnome-text-editor', '--version'],
                                       capture_output=True,
                                       text=True,
                                       timeout=2)
                if result.returncode == 0:
                    editor = 'gnome-text-editor'
                    editor_name = 'gnome-text-editor'
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # 에디터를 찾지 못한 경우
        if not editor:
            display_message("텍스트 에디터를 찾을 수 없습니다.", "ERROR")
            display_message("gedit 또는 gnome-text-editor를 설치하세요.", "ERROR")
            return False

        display_message(f"{editor_name}를 실행합니다: {file_path}", "INFO")
        display_message(f"편집을 완료하고 {editor_name}를 종료하면 계속 진행됩니다...", "INFO")

        # 에디터를 동기 방식으로 실행 (사용자가 닫을 때까지 대기)
        # stdout/stderr를 DEVNULL로 리다이렉트하여 TUI가 깨지는 것을 방지
        # gedit, gnome-text-editor 모두 --standalone 옵션 필요
        import time

        # --standalone 옵션 지원 여부 확인
        has_standalone = False
        try:
            test_result = subprocess.run(
                [editor, '--help'],
                capture_output=True,
                text=True,
                timeout=2
            )
            has_standalone = '--standalone' in test_result.stdout or '--standalone' in test_result.stderr
        except:
            has_standalone = False

        # 에디터 실행
        if has_standalone:
            # --standalone: 독립 프로세스로 실행하여 종료까지 대기
            result = subprocess.run(
                [editor, '--standalone', '--new-window', file_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        else:
            # --standalone 미지원: --new-window만 사용 (대기 안 될 수 있음)
            display_message(f"[WARNING] {editor_name}이 --standalone을 지원하지 않습니다.", "WARN")
            display_message("편집 완료 후 수동으로 설정을 다시 로드하세요.", "WARN")
            result = subprocess.run(
                [editor, '--new-window', file_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        # 파일 시스템 동기화를 위한 짧은 대기
        time.sleep(0.5)

        if result.returncode == 0:
            return True
        else:
            display_message(f"{editor_name} 실행 중 오류 발생 (코드: {result.returncode})", "ERROR")
            return False

    except FileNotFoundError as e:
        display_message(f"에디터를 찾을 수 없습니다: {e}", "ERROR")
        return False
    except Exception as e:
        display_message(f"에디터 실행 중 오류: {e}", "ERROR")
        return False
