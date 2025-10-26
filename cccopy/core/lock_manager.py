"""NFS 안전 락 관리 모듈"""
import os
import time
import socket
import getpass
import random
import shutil
import shlex


class CCCopyError(Exception):
    """CCCopy 커스텀 예외"""
    pass


class LockManager:
    """NFS 안전 락 매니저 (디렉토리 기반)"""

    def __init__(self, lock_file_path, timeout=60, max_stale_time=None, permission_manager=None):
        self.lock_dir_path = lock_file_path + ".lockdir"
        self.lock_info_file = os.path.join(self.lock_dir_path, "owner.info")
        self.timeout = timeout
        self.max_stale_time = max_stale_time if max_stale_time else 300
        self.unique_id = self._generate_unique_id()
        self.acquired = False
        self.permission_manager = permission_manager

    def _generate_unique_id(self):
        """고유 식별자 생성"""
        user = getpass.getuser()
        hostname = socket.gethostname()
        pid = os.getpid()
        timestamp = int(time.time() * 1000000)  # 마이크로초
        random_part = random.randint(100000, 999999)
        return f"{user}@{hostname}:{pid}:{timestamp}:{random_part}"

    def _is_stale_lock(self):
        """스테일 락 확인"""
        try:
            stat = os.stat(self.lock_dir_path)
            age = time.time() - stat.st_mtime
            return age > self.max_stale_time
        except OSError:
            return True

    def _acquire_lock(self):
        """락 획득 시도 (sg 기반)"""
        # display_message와 messagebox는 외부에서 주입받아야 함
        from ..utils.ui_handler import display_message

        try:
            if self.permission_manager:
                display_message("권한 관리자를 통한 락 생성 (sg)", "DEBUG")
                # sg를 통한 락 디렉토리 생성
                lock_dir_escaped = shlex.quote(self.lock_dir_path)
                lock_info_escaped = shlex.quote(self.lock_info_file)
                unique_id_escaped = shlex.quote(self.unique_id)

                cmd = f"mkdir -p {lock_dir_escaped} && printf '%s\\n%s\\n' '{unique_id_escaped}' '{time.time()}' > {lock_info_escaped}"
                self.permission_manager.execute_sg_command(cmd, timeout=10, operation_desc="Lock 디렉토리 생성")
            else:
                display_message("직접 락 생성", "DEBUG")
                os.makedirs(self.lock_dir_path)
                with open(self.lock_info_file, 'w') as f:
                    f.write(f"{self.unique_id}\n{time.time()}\n")

            self.acquired = True
            return True
        except (OSError, CCCopyError) as e:
            display_message(f"락 획득 실패: {e}", "DEBUG")
            return False

    def _release_lock(self):
        """락 해제 (sg 기반)"""
        from ..utils.ui_handler import display_message

        if self.acquired:
            try:
                if self.permission_manager:
                    display_message("권한 관리자를 통한 락 해제 (sg)", "DEBUG")
                    lock_dir_escaped = shlex.quote(self.lock_dir_path)
                    cmd = f"rm -rf {lock_dir_escaped}"
                    self.permission_manager.execute_sg_command(cmd, timeout=10, check=False, operation_desc="Lock 디렉토리 삭제")
                else:
                    if os.path.exists(self.lock_info_file):
                        os.remove(self.lock_info_file)
                    if os.path.exists(self.lock_dir_path):
                        os.rmdir(self.lock_dir_path)
                self.acquired = False
            except (OSError, CCCopyError):
                pass

    def __enter__(self):
        """락 획득"""
        from ..utils.ui_handler import display_message, messagebox

        start_time = time.time()
        while time.time() - start_time < self.timeout:
            if self._acquire_lock():
                return self

            # 스테일 락 정리
            if os.path.exists(self.lock_dir_path) and self._is_stale_lock():
                display_message(f"스테일 락 정리 중: {self.lock_dir_path}", "INFO")
                try:
                    shutil.rmtree(self.lock_dir_path)
                except OSError:
                    pass

            time.sleep(0.1)

        # 타임아웃 시 락 소유자 정보 및 해결 방법 출력
        user_only = "알 수 없음"
        if os.path.exists(self.lock_info_file):
            try:
                with open(self.lock_info_file, 'r') as f:
                    owner_info = f.readline().strip()
                # owner_info 형태: "user@hostname:pid:timestamp:random"에서 user만 추출
                if '@' in owner_info:
                    user_only = owner_info.split('@')[0]
                else:
                    user_only = owner_info.split(':')[0] if ':' in owner_info else owner_info
            except Exception as e:
                display_message(f"Read lock owner info failed: {e}", "DEBUG")

        # 에러 메시지와 해결 방법을 messagebox로 표시
        error_message = f"""락 획득에 실패했습니다.

현재 락 소유자: {user_only}
락 파일: {self.lock_dir_path}

다른 사용자가 작업 중이거나 이전 작업이 비정상 종료되었을 수 있습니다.

강제 해결 방법:
rm -rf {self.lock_dir_path}

주의: 강제 해결은 다른 사용자의 작업을 중단시킬 수 있습니다."""

        # TUI나 CLI에 따라 적절한 메시지 표시
        try:
            # messagebox 함수 사용 시도
            messagebox(error_message, "락 획득 타임아웃", "error")
        except (NameError, TypeError):
            # messagebox가 없거나 호출 실패시 기본 메시지 출력
            display_message("락 획득 타임아웃", "ERROR")
            display_message(f"현재 락 소유자: {user_only}", "ERROR")
            display_message(f"강제 해결: rm -rf {self.lock_dir_path}", "ERROR")

        raise CCCopyError(f"락 획득 타임아웃: {self.lock_dir_path}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """락 해제"""
        from ..utils.ui_handler import display_message

        self._release_lock()
        display_message("락 해제 완료", "INFO")
