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

    # Git 버전 캐시 (한번만 감지)
    _git_version_cache = None

    @staticmethod
    def get_git_version():
        """Git 버전 감지 및 캐싱

        Returns:
            tuple: (major, minor, patch) 예: (2, 49, 0) 또는 (1, 8, 3)
        """
        if GitHelper._git_version_cache is not None:
            return GitHelper._git_version_cache

        try:
            git_bin = os.environ.get('CCCOPY_GIT_BIN_PATH', 'git')
            result = subprocess.run(
                [git_bin, '--version'],
                capture_output=True,
                text=True,
                check=True
            )
            # "git version 2.49.0" 형식 파싱
            version_str = result.stdout.strip()
            # "git version X.Y.Z" 에서 X.Y.Z 추출
            parts = version_str.split()
            if len(parts) >= 3:
                version_nums = parts[2].split('.')
                major = int(version_nums[0]) if len(version_nums) > 0 else 0
                minor = int(version_nums[1]) if len(version_nums) > 1 else 0
                patch = int(version_nums[2]) if len(version_nums) > 2 else 0
                GitHelper._git_version_cache = (major, minor, patch)
                return GitHelper._git_version_cache
        except Exception as e:
            display_message(f"Git 버전 감지 실패 (1.8로 가정): {e}", "WARNING")

        # 실패시 1.8로 가정
        GitHelper._git_version_cache = (1, 8, 0)
        return GitHelper._git_version_cache

    @staticmethod
    def is_git_version_ge(major, minor=0):
        """Git 버전이 지정된 버전 이상인지 확인

        Args:
            major: 메이저 버전
            minor: 마이너 버전 (기본값: 0)

        Returns:
            bool: 현재 Git 버전이 지정 버전 이상이면 True
        """
        current = GitHelper.get_git_version()
        if current[0] > major:
            return True
        elif current[0] == major:
            return current[1] >= minor
        return False

    @staticmethod
    def configure_safe_directory(directory, production_perm=None):
        """Git 2.35+ safe.directory 설정 (dubious ownership 오류 방지)

        Git 2.35 이상에서만 실행됩니다.
        Git 1.8 등 구버전에서는 아무 작업도 하지 않습니다.

        Args:
            directory: 안전한 디렉토리로 등록할 경로
            production_perm: Production 권한 관리자 (필요시)
        """
        # Git 2.35 미만 버전은 safe.directory 개념 없음
        if not GitHelper.is_git_version_ge(2, 35):
            return

        try:
            # 절대 경로로 변환
            abs_dir = os.path.abspath(directory)

            # 이미 safe.directory에 등록되어 있는지 확인
            git_bin = os.environ.get('CCCOPY_GIT_BIN_PATH', 'git')
            result = subprocess.run(
                [git_bin, 'config', '--global', '--get-all', 'safe.directory'],
                capture_output=True,
                text=True
            )

            # 이미 등록되어 있으면 스킵
            if result.returncode == 0:
                safe_dirs = result.stdout.strip().split('\n')
                if abs_dir in safe_dirs or '*' in safe_dirs:
                    display_message(f"이미 safe.directory 등록됨: {abs_dir}", "DEBUG")
                    return

            # safe.directory에 추가
            GitHelper.run_git_command(
                ['config', '--global', '--add', 'safe.directory', abs_dir],
                production_perm=production_perm
            )
            display_message(f"safe.directory 등록: {abs_dir}", "INFO")

        except Exception as e:
            # safe.directory 설정 실패는 치명적이지 않으므로 경고만
            display_message(f"safe.directory 설정 실패 (무시): {e}", "DEBUG")

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

    @staticmethod
    def sync_sources_to_git(directory, source_file_list, production_perm=None):
        """SOURCES 패턴에 매칭되는 파일들을 Git에 동기화 (추가만 수행)

        - Untracked 파일: Git에 추가
        - SOURCES에서 제외된 파일: Git tracking 유지 (히스토리 보존)

        Production은 팀 공유 공간이므로, 한 프로젝트가 SOURCES에서 제외해도
        다른 프로젝트나 파일 히스토리 보존을 위해 Git tracking은 유지합니다.
        명시적 삭제가 필요하면 사용자가 직접 Production에서 git rm 실행.

        Args:
            directory: Git 저장소 경로
            source_file_list: SOURCES 패턴에 매칭되는 파일들의 상대 경로 리스트
            production_perm: Production 권한 관리자

        Returns:
            dict: {'added': [...], 'removed': [...]} 변경 내용 (removed는 항상 빈 리스트)
        """
        import fnmatch

        changes = {'added': [], 'removed': []}

        try:
            # 1. Git에서 현재 tracked 중인 파일 목록 가져오기
            tracked_output = GitHelper.run_git_command(
                ['ls-files'],
                cwd=directory,
                capture_output=True,
                production_perm=production_perm
            )

            tracked_files = set()
            if tracked_output:
                tracked_files = set(line.strip() for line in tracked_output.strip().split('\n') if line.strip())

            # SOURCES 파일 집합
            sources_files = set(source_file_list)

            # 2. Untracked 파일 중 SOURCES에 속한 파일 찾기
            # git ls-files --others --exclude-standard: .gitignore 제외하고 untracked 파일 모두 표시
            untracked_output = GitHelper.run_git_command(
                ['ls-files', '--others', '--exclude-standard'],
                cwd=directory,
                capture_output=True,
                production_perm=production_perm
            )

            untracked_in_sources = []
            if untracked_output:
                for line in untracked_output.strip().split('\n'):
                    rel_path = line.strip()
                    if rel_path and rel_path in sources_files:
                        untracked_in_sources.append(rel_path)

            # 3. SOURCES에 속하지만 Git에 untracked인 파일 추가
            if untracked_in_sources:
                display_message(f"SOURCES에 속한 untracked 파일 {len(untracked_in_sources)}개를 Git에 추가합니다...", "INFO")
                for rel_path in untracked_in_sources[:5]:  # 최대 5개만 표시
                    display_message(f"  + {rel_path}", "INFO")
                if len(untracked_in_sources) > 5:
                    display_message(f"  ... 외 {len(untracked_in_sources) - 5}개", "INFO")

                GitHelper.add_files(directory, untracked_in_sources, production_perm=production_perm)
                changes['added'] = untracked_in_sources

            # 4. SOURCES에서 제외된 파일은 Git에 유지 (히스토리 보존)
            # Production은 팀 공유 공간이므로, 한 프로젝트가 SOURCES에서 제외해도
            # 다른 프로젝트나 히스토리 보존을 위해 Git tracking은 유지
            # 명시적 삭제가 필요하면 사용자가 직접 git rm 실행

            # 변경사항 요약
            if changes['added']:
                display_message(f"SOURCES 동기화 완료: +{len(changes['added'])} 파일 추가", "INFO")
            else:
                display_message("SOURCES 동기화: 변경사항 없음", "DEBUG")

            return changes

        except Exception as e:
            display_message(f"SOURCES 동기화 중 오류: {e}", "ERROR")
            return changes
