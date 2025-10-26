"""
Git 명령어 헬퍼 모듈
Git 저장소 관리 및 명령 실행 기능 제공
"""

import os
import shlex
import subprocess

from ..core.lock_manager import CCCopyError
from ..utils.ui_handler import display_message


class GitHelper:
    """Git 명령어 헬퍼"""

    @staticmethod
    def format_git_status_line(line):
        """git status --short 출력을 사람이 읽기 쉬운 형태로 변환

        Git 상태 코드:
        ?? = untracked (새 파일)
        M  = modified in index (수정됨, staged)
         M = modified in working tree (수정됨, unstaged)
        MM = modified in both (staged + unstaged)
        A  = added (추가됨)
        D  = deleted (삭제됨)
        R  = renamed (이름 변경됨)
        C  = copied (복사됨)
        """
        if not line.strip():
            return line

        # git status --short 형식: XY filename
        # X = index 상태, Y = working tree 상태
        # 최소 3글자 (XY + 공백 + 파일명)
        if len(line) < 3:
            return line

        # 첫 2글자가 상태 코드, 나머지가 파일명
        status_code = line[:2]
        file_path = line[3:] if len(line) > 3 else ""

        # 상태 코드를 한글로 변환
        status_map = {
            '??': '[새파일]',
            'M ': '[수정]  ',
            ' M': '[수정]  ',
            'MM': '[수정]  ',
            'A ': '[추가]  ',
            'D ': '[삭제]  ',
            'R ': '[이름변경]',
            'C ': '[복사]  ',
            'AM': '[추가+수정]',
            'AD': '[추가+삭제]',
        }

        status_text = status_map.get(status_code, f'[{status_code}]')
        return f"{status_text} {file_path}"

    @staticmethod
    def _get_git_operation_desc(args):
        """Git 명령에 대한 작업 설명 생성"""
        if not args:
            return "Git 명령 실행"

        cmd = args[0]
        if cmd == 'init':
            return "Git 저장소 초기화"
        elif cmd == 'config':
            if len(args) >= 3:
                return f"Git 설정 ({args[1]})"
            return "Git 설정"
        elif cmd == 'add':
            if len(args) > 1 and args[-1] == '.':
                return "Git 전체 파일 추가"
            return "Git 파일 추가"
        elif cmd == 'commit':
            return "Git 커밋"
        elif cmd == 'rm':
            if '--cached' in args:
                return "Git cache 갱신 (rm)"
            return "Git 파일 삭제"
        else:
            return f"Git {cmd} 실행"

    @staticmethod
    def run_git_command(args, cwd=None, capture_output=False, production_perm=None):
        """Git 명령 실행 (sg 지원)

        Args:
            args: Git 명령 인자 리스트
            cwd: 작업 디렉토리
            capture_output: 출력 캡처 여부
            production_perm: Production 권한 관리자 (AtomicProductionPermission)
        """
        git_bin = os.environ.get('CCCOPY_GIT_BIN_PATH', 'git')

        # TUI 모드에서는 항상 출력 캡처하여 화면 깨짐 방지
        from ..utils.ui_handler import _ui_handler
        force_capture = _ui_handler and hasattr(_ui_handler, 'messagebox')

        # Production 권한이 필요한 쓰기 작업인 경우 sg 사용
        write_commands = ['init', 'config', 'add', 'commit', 'rm']
        is_write_command = any(cmd in args for cmd in write_commands)

        if production_perm and is_write_command:
            # sg를 통한 실행
            # 인자 이스케이프: '.'나 단순 경로는 그대로, 복잡한 경로만 quote
            escaped_args = []
            for arg in args:
                arg_str = str(arg)
                # '.'나 '-'로 시작하는 옵션, 단순 경로는 그대로
                if arg_str in ['.', '..'] or arg_str.startswith('-'):
                    escaped_args.append(arg_str)
                else:
                    escaped_args.append(shlex.quote(arg_str))
            git_args_escaped = ' '.join(escaped_args)
            cwd_escaped = shlex.quote(cwd) if cwd else '.'

            # Git 바이너리 경로 이스케이프 (sg 환경에서 다른 git이 실행되는 것 방지)
            git_bin_escaped = shlex.quote(git_bin) if '/' in git_bin else git_bin
            cmd = f"cd {cwd_escaped} && {git_bin_escaped} {git_args_escaped}"

            # Git 명령에 대한 작업 설명 생성
            git_operation_desc = GitHelper._get_git_operation_desc(args)

            # Git add 명령이면 사용자에게 대기 안내
            if args[0] == 'add':
                display_message("Git add 작업이 진행 중입니다. 잠시 기다려주세요...", "INFO")

            try:
                result = production_perm.execute_sg_command(cmd, timeout=3600, operation_desc=git_operation_desc)

                # Git add 명령 완료시 완료 메시지
                if args[0] == 'add':
                    display_message("Git add 작업이 완료되었습니다.", "INFO")

                # TUI 모드에서 Git 출력을 로그로 표시
                if force_capture and result:
                    stdout_lines = result.strip().split('\n')
                    for line in stdout_lines:
                        line = line.strip()
                        if line and ('changed' in line or 'insertion' in line or 'deletion' in line):
                            display_message(f"Git: {line}", "INFO")

                return result.strip() if result else ""
            except CCCopyError:
                raise
        else:
            # 일반 실행 (읽기 작업 또는 Work 디렉토리)
            cmd = [git_bin] + args

            try:
                if capture_output or force_capture:
                    # TUI 모드에서 Git 진단 메시지가 터미널에 직접 출력되는 것을 방지
                    env = os.environ.copy()
                    env['GIT_TERMINAL_PROMPT'] = '0'  # 터미널 프롬프트 비활성화
                    env['GIT_DISCOVERY_ACROSS_FILESYSTEM'] = '1'  # 파일시스템 경계 경고 비활성화

                    result = subprocess.run(
                        cmd, cwd=cwd,
                        capture_output=True,
                        text=True,
                        check=True,
                        env=env
                    )

                    # TUI 모드에서 Git 출력을 로그로 표시 (중요한 정보만)
                    if force_capture and not capture_output and result.stdout:
                        stdout_lines = result.stdout.strip().split('\n')
                        for line in stdout_lines:
                            line = line.strip()
                            if line and ('changed' in line or 'insertion' in line or 'deletion' in line):
                                display_message(f"Git: {line}", "INFO")

                    return result.stdout.strip() if result.stdout else ""
                else:
                    subprocess.run(cmd, cwd=cwd, check=True)
                    return True
            except subprocess.CalledProcessError as e:
                if (capture_output or force_capture) and e.stdout:
                    error_msg = e.stdout.strip()
                elif e.stderr:
                    error_msg = e.stderr.strip()
                else:
                    error_msg = f"Git command failed: {' '.join(cmd)}"
                raise CCCopyError(error_msg)

    @staticmethod
    def is_git_repo(directory):
        """Git 저장소인지 확인"""
        return os.path.exists(os.path.join(directory, '.git'))

    @staticmethod
    def init_repo(directory, production_perm=None):
        """Git 저장소 초기화"""
        GitHelper.run_git_command(['init'], cwd=directory, production_perm=production_perm)

    @staticmethod
    def add_all(directory, production_perm=None):
        """모든 파일 추가 (삭제된 파일 포함)"""
        GitHelper.run_git_command(['add', '--all', '.'], cwd=directory, production_perm=production_perm)

    @staticmethod
    def add_files(directory, file_list, production_perm=None):
        """특정 파일들만 Git에 추가 (SOURCES 필터링용)

        Args:
            directory: Git 저장소 경로
            file_list: 추가할 파일들의 상대 경로 리스트
            production_perm: Production 권한 관리자
        """
        if not file_list:
            return

        # 각 파일을 개별적으로 git add
        for rel_path in file_list:
            try:
                GitHelper.run_git_command(['add', rel_path], cwd=directory, production_perm=production_perm)
            except Exception as e:
                # 개별 파일 추가 실패시 경고만 출력하고 계속 진행
                from ..utils.ui_handler import display_message
                display_message(f"[WARNING] git add 실패: {rel_path} - {str(e)}", "WARN")

    @staticmethod
    def setup_user_config(directory, production_perm=None, use_dummy=False):
        """Git 사용자 설정 (로컬 설정)

        Args:
            directory: Git 저장소 경로
            production_perm: Production 권한 관리자
            use_dummy: True이면 dummy user config 사용 (Production용)
        """
        try:
            import getpass

            if use_dummy:
                # Production용 dummy config
                user_name = "cccopy_admin"
                user_email = "admin@cccopy.com"
            else:
                # 실제 사용자 정보
                user_name = getpass.getuser()
                user_email = f"{user_name}@cccopy.com"

            GitHelper.run_git_command(['config', 'user.name', user_name], cwd=directory, production_perm=production_perm)
            GitHelper.run_git_command(['config', 'user.email', user_email], cwd=directory, production_perm=production_perm)
            display_message(f"Git 사용자 설정 완료: {user_name} <{user_email}>", "INFO")
        except Exception as e:
            display_message(f"Git 사용자 설정 실패: {e}", "ERROR")

    @staticmethod
    def has_uncommitted_changes(directory):
        """커밋되지 않은 변경사항이 있는지 확인"""
        try:
            # git status --porcelain으로 변경사항 확인
            result = GitHelper.run_git_command(['status', '--porcelain'], cwd=directory, capture_output=True)
            return bool(result.strip())
        except Exception as e:
            display_message(f"Git uncommitted changes check failed: {e}", "DEBUG")
            return False

    @staticmethod
    def commit_all(directory, message, production_perm=None):
        """모든 변경사항 커밋

        Args:
            directory: Git 저장소 경로
            message: 커밋 메시지
            production_perm: Production 권한 관리자 (있으면 --author 사용)
        """
        import getpass

        # Production 작업시 --author로 실제 사용자 명시
        if production_perm:
            user_name = getpass.getuser()
            user_email = f"{user_name}@cccopy.com"
            author = f"{user_name} <{user_email}>"
            GitHelper.run_git_command(['commit', '--author', author, '-m', message], cwd=directory, production_perm=production_perm)
        else:
            # Work 작업시 기본 git config 사용
            GitHelper.run_git_command(['commit', '-m', message], cwd=directory, production_perm=production_perm)

    @staticmethod
    def add_and_commit_files(directory, file_paths, message, production_perm=None):
        """특정 파일들만 add하고 커밋

        Args:
            directory: Git 저장소 경로
            file_paths: 커밋할 파일 경로 리스트
            message: 커밋 메시지
            production_perm: Production 권한 관리자 (있으면 --author 사용)
        """
        import getpass

        if not file_paths:
            return False

        # 파일들을 개별적으로 add
        for file_path in file_paths:
            GitHelper.run_git_command(['add', file_path], cwd=directory, production_perm=production_perm)

        # Production 작업시 --author로 실제 사용자 명시
        if production_perm:
            user_name = getpass.getuser()
            user_email = f"{user_name}@cccopy.com"
            author = f"{user_name} <{user_email}>"
            GitHelper.run_git_command(['commit', '--author', author, '-m', message], cwd=directory, production_perm=production_perm)
        else:
            # Work 작업시 기본 git config 사용
            GitHelper.run_git_command(['commit', '-m', message], cwd=directory, production_perm=production_perm)

        return True

    @staticmethod
    def get_current_head_commit(directory):
        """현재 HEAD 커밋 해시 가져오기"""
        try:
            return GitHelper.run_git_command(['rev-parse', 'HEAD'], cwd=directory, capture_output=True)
        except Exception as e:
            display_message(f"Get current head commit failed: {e}", "DEBUG")
            return None

    @staticmethod
    def get_file_hash_from_commit(directory, commit_hash, rel_path):
        """특정 커밋에서 파일의 Git blob hash 가져오기"""
        try:
            result = GitHelper.run_git_command(['ls-tree', commit_hash, rel_path], cwd=directory, capture_output=True)
            if result:
                parts = result.strip().split()
                if len(parts) >= 3:
                    return parts[2]  # blob hash
            return None
        except Exception as e:
            display_message(f"Get current head commit failed: {e}", "DEBUG")
            return None

    @staticmethod
    def get_current_file_hash(directory, rel_path):
        """현재 파일의 Git blob hash 가져오기"""
        try:
            file_path = os.path.join(directory, rel_path)
            if not os.path.exists(file_path):
                return None
            result = GitHelper.run_git_command(['hash-object', file_path], cwd=directory, capture_output=True)
            return result.strip() if result else None
        except Exception as e:
            display_message(f"Get current head commit failed: {e}", "DEBUG")
            return None

    @staticmethod
    def get_git_log(directory, limit=None):
        """Git 로그 가져오기 (표 형식용)"""
        try:
            # git log --pretty=format:'%h|%an|%ad|%s' --date=iso (Git 1.8 호환)
            cmd = ['log', '--pretty=format:%h|%an|%ad|%s', '--date=iso']
            if limit:
                cmd.extend(['-n', str(limit)])

            result = GitHelper.run_git_command(cmd, cwd=directory, capture_output=True)

            if not result:
                return []

            commits = []
            for i, line in enumerate(result.split('\n'), 1):
                if line.strip():
                    parts = line.split('|', 3)
                    if len(parts) == 4:
                        # ISO 날짜에서 타임존 제거 (2025-10-05 23:35:43 +0900 -> 2025-10-05 23:35:43)
                        date_str = parts[2].rsplit(' ', 1)[0] if ' ' in parts[2] else parts[2]
                        commits.append({
                            'index': i,
                            'hash': parts[0],
                            'author': parts[1],
                            'date': date_str,
                            'message': parts[3]
                        })
            return commits
        except Exception as e:
            display_message(f"Get git log failed: {e}", "DEBUG")
            return []

    @staticmethod
    def get_commit_files(directory, commit_hash):
        """특정 커밋에서 변경된 파일 목록 가져오기"""
        try:
            # git show --name-status <commit>
            cmd = ['show', '--name-status', '--pretty=format:', commit_hash]
            result = GitHelper.run_git_command(cmd, cwd=directory, capture_output=True)

            if not result:
                return []

            files = []
            for line in result.split('\n'):
                line = line.strip()
                if line and not line.startswith('commit') and not line.startswith('Author') and not line.startswith('Date'):
                    parts = line.split('\t', 1)
                    if len(parts) == 2:
                        status = parts[0]
                        filename = parts[1]
                        status_text = {'A': 'Added', 'M': 'Modified', 'D': 'Deleted', 'R': 'Renamed', 'C': 'Copied'}.get(status[0], status)
                        files.append({
                            'status': status_text,
                            'filename': filename
                        })
            return files
        except Exception as e:
            display_message(f"Get git log failed: {e}", "DEBUG")
            return []
