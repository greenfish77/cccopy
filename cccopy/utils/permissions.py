"""
권한 관리 모듈
Production 작업을 위한 원자적 권한 관리 클래스
"""

import grp
import shlex
import subprocess
import time

from ..core.lock_manager import CCCopyError
from .ui_handler import display_message


class AtomicProductionPermission:
    """Production 작업을 위한 sg 기반 원자적 권한 관리"""

    def __init__(self, group_name=None):
        self.group_name = group_name
        self._group_exists = False

        if group_name:
            try:
                group_info = grp.getgrnam(group_name)
                self._group_exists = True
                display_message(f"그룹 확인 완료: {group_name} (gid={group_info.gr_gid})", "DEBUG")
            except KeyError:
                display_message(f"그룹 '{group_name}' 정보 획득 실패", "ERROR")

    def execute_sg_command(self, command, timeout=30, check=True, operation_desc=None):
        """sg 명령을 통한 권한 상승 실행 (shell command)

        Args:
            command: 실행할 shell 명령
            timeout: 타임아웃 (초)
            check: 실패시 예외 발생 여부
            operation_desc: 작업 설명 (HIGH 레벨 로그용, 예: "Lock 디렉토리 생성")
        """
        if not self.group_name or not self._group_exists:
            display_message("권한 상승 불필요 (그룹 미설정), 일반 권한으로 실행", "DEBUG")
            # 그룹이 없으면 일반 shell로 실행
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout
                )
                if check and result.returncode != 0:
                    if operation_desc:
                        display_message(f"{operation_desc} 실패 (exit={result.returncode})", "HIGH")
                    display_message(f"[ERROR] 명령 실패 (exit={result.returncode})", "ERROR")
                    display_message(f"[ERROR] command: {command}", "ERROR")
                    if result.stderr:
                        display_message(f"[ERROR] stderr: {result.stderr}", "ERROR")
                    raise CCCopyError(f"명령 실행 실패: {result.stderr}")
                if result.stderr:
                    display_message(f"[WARN] stderr: {result.stderr}", "WARNING")
                if operation_desc:
                    display_message(f"{operation_desc} 성공", "HIGH")
                return result.stdout
            except subprocess.TimeoutExpired:
                if operation_desc:
                    display_message(f"{operation_desc} 실패 (타임아웃)", "HIGH")
                raise CCCopyError(f"명령 타임아웃: {command}")
            except Exception as e:
                if operation_desc:
                    display_message(f"{operation_desc} 실패 ({e})", "HIGH")
                raise CCCopyError(f"명령 실행 오류: {e}")

        # sg를 통한 권한 상승 실행
        sg_cmd = f"sg {self.group_name} -c {shlex.quote(command)}"
        # 명령이 길면 요약, 짧으면 전체 출력
        cmd_display = command if len(command) <= 150 else command[:150] + "..."
        display_message(f"[SG] 권한 상승 실행: {cmd_display}", "DEBUG")

        start_time = time.time()
        try:
            result = subprocess.run(
                sg_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            duration = time.time() - start_time
            display_message(f"[SG] 실행 완료: {duration:.3f}초", "DEBUG")

            if check and result.returncode != 0:
                if operation_desc:
                    display_message(f"{operation_desc} 실패 (exit={result.returncode})", "HIGH")
                display_message(f"[ERROR] sg 명령 실패 (exit={result.returncode})", "ERROR")
                display_message(f"[ERROR] command: {command}", "ERROR")
                if result.stderr:
                    display_message(f"[ERROR] stderr: {result.stderr}", "ERROR")
                raise CCCopyError(f"sg 명령 실패: {result.stderr}")

            if result.stderr:
                display_message(f"[WARN] stderr: {result.stderr}", "WARNING")

            # 성공시 HIGH 레벨 로그
            if operation_desc:
                display_message(f"{operation_desc} 성공", "HIGH")

            return result.stdout

        except subprocess.TimeoutExpired:
            if operation_desc:
                display_message(f"{operation_desc} 실패 (타임아웃 {timeout}초)", "HIGH")
            raise CCCopyError(f"sg 명령 타임아웃 ({timeout}초): {command}")
        except CCCopyError:
            raise
        except Exception as e:
            if operation_desc:
                display_message(f"{operation_desc} 실패 ({e})", "HIGH")
            raise CCCopyError(f"sg 명령 실행 오류: {e}")
