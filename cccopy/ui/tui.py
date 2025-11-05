#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cccopy_tui - Curses-based TUI for CCCopy
CCCopy를 위한 Curses 기반 TUI

트리뷰 스타일 파일 탐색기 인터페이스
"""

import os
import curses
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import List, Dict, Optional

# cccopy 모듈에서 필요한 클래스들을 전역 변수로 설정
# 순환 import 방지를 위해 직접 import 제거
ProjectManager = None
FileState = None
CCCopyError = None
GitHelper = None
safe_input = None

# 전역 상수들 (main.py에서 설정됨)
CCCOPY_VERSION = "1.0"                # 기본값
PARTIAL_REFRESH_CACHE_TIMEOUT = 300  # 기본값
MAX_LOG_LINES = 1024                  # 기본값
MAX_LOG_FILES = 1024                  # 기본값
MAX_STATE_CHECK_WORKERS = 2           # 기본값
WATCH_FILE_CHANGE_INTERVAL = 5        # 기본값

# 로그 파일 디렉토리
LOG_DIR = os.path.expanduser("~/.cccopy/log/")

# 초기화 로그 버퍼 (curses 초기화 전 로그들을 저장)
_init_log_buffer = []

def add_init_log(message, level="INFO"):
    """초기화 로그를 버퍼에 추가"""
    global _init_log_buffer
    # level이 이미 포맷된 형태라면 그대로 저장, 아니면 메시지만 저장
    # 타임스탬프는 TUI에서 로그를 표시할 때 추가됨
    _init_log_buffer.append((message, level))

def get_and_clear_init_logs():
    """초기화 로그 버퍼를 반환하고 클리어"""
    global _init_log_buffer
    logs = _init_log_buffer.copy()
    _init_log_buffer.clear()
    return logs

def set_cccopy_classes(project_manager_cls, file_state_cls, cccopy_error_cls, git_helper_cls, safe_input_func):
    """cccopy 클래스들을 설정"""
    global ProjectManager, FileState, CCCopyError, GitHelper, safe_input
    ProjectManager = project_manager_cls
    FileState = file_state_cls
    CCCopyError = cccopy_error_cls
    GitHelper = git_helper_cls
    safe_input = safe_input_func

def set_global_constants(**kwargs):
    """main.py에서 전역 상수들을 설정"""
    global CCCOPY_VERSION, PARTIAL_REFRESH_CACHE_TIMEOUT, MAX_LOG_LINES, MAX_LOG_FILES, MAX_STATE_CHECK_WORKERS, WATCH_FILE_CHANGE_INTERVAL
    if 'CCCOPY_VERSION' in kwargs:
        CCCOPY_VERSION = kwargs['CCCOPY_VERSION']
    if 'PARTIAL_REFRESH_CACHE_TIMEOUT' in kwargs:
        PARTIAL_REFRESH_CACHE_TIMEOUT = kwargs['PARTIAL_REFRESH_CACHE_TIMEOUT']
    if 'MAX_LOG_LINES' in kwargs:
        MAX_LOG_LINES = kwargs['MAX_LOG_LINES']
    if 'MAX_LOG_FILES' in kwargs:
        MAX_LOG_FILES = kwargs['MAX_LOG_FILES']
    if 'MAX_STATE_CHECK_WORKERS' in kwargs:
        MAX_STATE_CHECK_WORKERS = kwargs['MAX_STATE_CHECK_WORKERS']
    if 'WATCH_FILE_CHANGE_INTERVAL' in kwargs:
        WATCH_FILE_CHANGE_INTERVAL = kwargs['WATCH_FILE_CHANGE_INTERVAL']


class ViewMode(Enum):
    """표시 모드"""
    WORK = "WORK"
    PRODUCTION = "PRODUCTION"


class ViewStyle(Enum):
    """뷰 스타일"""
    DETAIL = "detail"  # 기존 상세 모드 (디렉토리 탐색)
    TREE = "tree"      # 트리 뷰 모드 (모든 파일 표시)


class FileNode:
    """파일/디렉토리 노드"""
    def __init__(self, name: str, path: str, is_dir: bool = False,
                 parent: Optional['FileNode'] = None):
        self.name = name
        self.path = path  # 상대 경로
        self.is_dir = is_dir
        self.parent = parent
        self.children: List['FileNode'] = []
        self.expanded = False
        self.state = FileState.SAME
        self.size = 0

    def add_child(self, child: 'FileNode'):
        """자식 노드 추가"""
        child.parent = self
        self.children.append(child)

    def sort_children(self):
        """자식 노드 정렬 (디렉토리 먼저, 이름순)"""
        self.children.sort(key=lambda x: (not x.is_dir, x.name.lower()))

    def get_full_path(self, base_dir: str) -> str:
        """전체 파일 경로 반환"""
        return os.path.join(base_dir, self.path)


class FileTree:
    """파일 트리 관리"""
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = FileNode("", "", True)
        self.flat_nodes: List[FileNode] = []  # 표시용 평면 리스트

    def build_tree(self, mode: ViewMode):
        """파일 트리 구축"""
        self.root = FileNode("", "", True)
        self.flat_nodes = []

        # 기본 디렉토리 설정
        if mode == ViewMode.WORK:
            base_dir = self.workspace.working_dir
        else:
            base_dir = self.workspace.production_dir

        if not os.path.exists(base_dir):
            return

        # 파일 수집 (Git tracked 파일만 - 성능 최적화)
        try:
            files = self.workspace.collect_files_from_git(include_work_only=True)
        except Exception:
            files = []

        # 노드 딕셔너리 (경로 -> 노드)
        nodes: Dict[str, FileNode] = {"": self.root}

        for production_file, rel_path in files:
            if mode == ViewMode.WORK:
                file_path = os.path.join(self.workspace.working_dir, rel_path)
            else:
                file_path = production_file

            # 파일이 실제로 존재하는지 확인
            if not os.path.exists(file_path):
                continue

            # 디렉토리 경로 분해
            parts = rel_path.split('/')
            current_path = ""

            for i, part in enumerate(parts):
                if current_path:
                    parent_path = current_path
                    current_path = current_path + "/" + part
                else:
                    parent_path = ""
                    current_path = part

                # 노드가 없으면 생성
                if current_path not in nodes:
                    is_dir = i < len(parts) - 1
                    node = FileNode(part, current_path, is_dir)

                    # 파일 정보 설정
                    if not is_dir and os.path.exists(file_path):
                        try:
                            node.size = os.path.getsize(file_path)
                            # 파일 상태 계산
                            work_file = os.path.join(self.workspace.working_dir, rel_path)
                            node.state = self.workspace.get_file_state(production_file, work_file, rel_path)
                        except Exception:
                            node.size = 0
                            node.state = FileState.SAME

                    nodes[current_path] = node

                    # 부모에 추가
                    if parent_path in nodes:
                        nodes[parent_path].add_child(node)

        # 트리 정렬
        self._sort_tree(self.root)

        # 평면 리스트 생성
        self._build_flat_list()

    def _sort_tree(self, node: FileNode):
        """트리 재귀적 정렬"""
        node.sort_children()
        for child in node.children:
            self._sort_tree(child)

    def _build_flat_list(self):
        """표시용 평면 리스트 구축"""
        self.flat_nodes = []
        self._add_to_flat_list(self.root, 0)

    def _add_to_flat_list(self, node: FileNode, depth: int):
        """평면 리스트에 노드 추가"""
        if depth > 0:  # 루트 노드는 제외
            self.flat_nodes.append(node)

        if node.expanded or depth == 0:
            for child in node.children:
                self._add_to_flat_list(child, depth + 1)

    def toggle_expand(self, index: int):
        """폴더 펼치기/접기 토글"""
        if 0 <= index < len(self.flat_nodes):
            node = self.flat_nodes[index]
            if node.is_dir:
                node.expanded = not node.expanded
                self._build_flat_list()

    def get_depth(self, node: FileNode) -> int:
        """노드 깊이 계산"""
        depth = 0
        current = node.parent
        while current and current.parent:  # 루트 제외
            depth += 1
            current = current.parent
        return depth


class CCCopyTUI:
    """CCCopy TUI 메인 클래스"""

    def __init__(self, workspace, preference=None):
        self.workspace = workspace
        self.mode = ViewMode.WORK
        self.tree = FileTree(workspace)
        self.selected_index = 0
        self.scroll_offset = 0
        self.logs: List[str] = []

        # 전역 환경설정 관리자
        if preference:
            self.preference = preference
        else:
            from ..utils.preference import PreferenceManager
            self.preference = PreferenceManager()

        # 로그 파일 관리
        self.current_log_file = None
        self.current_log_file_path = None
        self._init_log_file()

        # 뷰 스타일 (detail 또는 tree)
        self.view_style = ViewStyle.DETAIL  # 기본값: detail

        # 디렉토리 탐색 상태
        self.current_directory = ""  # 상대 경로 (공백은 루트)
        self.directory_entries: List[Dict] = []  # 현재 디렉토리의 항목들

        # 트리 뷰 상태
        self.tree_expanded_dirs = set()  # 펼쳐진 디렉토리 경로 집합

        # 로그 뷰어 상태
        self.log_viewer_mode = False
        self.log_scroll_offset = 0
        self.log_selected_index = 0
        self.log_viewer_first_time = True
        self.log_show_all_debug = False  # DEBUG 로그 전체 표시 여부 (기본값: False = 최신 5개만)
        self.viewing_log_file = None  # 현재 보고 있는 로그 파일 경로 (None이면 현재 로그)

        # 히스토리 뷰어 상태
        self.history_viewer_mode = False
        self.history_list = []
        self.history_list_original = []  # 필터링 전 원본 목록
        self.history_selected_index = 0
        self.history_scroll_offset = 0
        self.history_detail_mode = False
        self.history_detail_files = []
        self.history_detail_selected_index = 0
        self.history_detail_scroll_offset = 0
        self.current_commit_hash = ""
        self.history_filter = {}  # 필터 조건 {'filename': '파일명', 'date_from': '', 'date_to': ''}

        # 도움말 뷰어 상태
        self.help_viewer_mode = False
        self.help_selected_index = 0
        self.help_scroll_offset = 0

        # 업로드 뷰어 상태
        self.upload_viewer_mode = False
        self.upload_files = []  # 업로드 가능한 파일 목록
        self.upload_selected_index = 0
        self.upload_scroll_offset = 0

        # App 뷰어 상태
        self.app_viewer_mode = False
        self.app_list = []  # 앱 목록
        self.app_selected_index = 0
        self.app_scroll_offset = 0

        # 색상 쌍 정의
        self.colors = {}

        # 다이얼로그 상태
        self.dialog_active = False
        self.dialog_result = None

        # Cache 시스템 (파일별 상태 캐싱)
        # 구조: {relative_path: (timestamp, FileState)}
        self.file_state_cache = {}
        self.cache_timeout = PARTIAL_REFRESH_CACHE_TIMEOUT  # 5분 (초 단위)

        # Thread 시스템
        import threading
        self.refresh_lock = threading.Lock()
        self.stop_refresh_event = threading.Event()
        self.pending_updates = {}  # {relative_path: FileState}

        # ThreadPoolExecutor (MAX_STATE_CHECK_WORKERS개 동시 실행으로 NFS I/O 경합 방지)
        self.thread_pool = ThreadPoolExecutor(max_workers=MAX_STATE_CHECK_WORKERS, thread_name_prefix="cccopy_state_check")
        self.futures = []  # 진행 중인 Future 객체 추적

        # Git tracked files 캐시 (전체 목록)
        self.tracked_files_cache = None
        self.tracked_files_cache_time = 0
        self.tracked_files_cache_timeout = PARTIAL_REFRESH_CACHE_TIMEOUT  # 5분 (cccopy.py Production 체크와 동일)
        self.tracked_files_loading = False  # git ls-files 로딩 중 플래그

        # Watch 시스템 (파일 변화 감지)
        self.watch_thread = None
        self.watch_stop_event = threading.Event()
        self.watch_directory_changed_event = threading.Event()
        self.last_git_status = None  # 이전 git status 결과 저장 (None=초기화 안됨)
        self.needs_auto_refresh = False

        # 튜토리얼 시스템
        # TUTORIAL.STARTUP_SHOW 설정 확인 (기본값: ON)
        startup_show = self.preference.get('', 'TUTORIAL.STARTUP_SHOW').upper()
        self.tutorial_enabled = (startup_show == 'ON')
        self.tutorial_step = 0  # 현재 튜토리얼 단계 (0부터 시작)
        self.tutorial_steps = [
            {
                'key': 'D',
                'title': '[D]ownload',
                'message': 'D를 눌러 Production 영역의 파일들을 Work로 복사하세요.\nProduction에 있는 최신 파일들을 내려받아 작업을 시작합니다.'
            },
            {
                'key': 'S',
                'title': '[S]ave',
                'message': 'S를 눌러 Work 영역의 변경사항을 Git에 커밋하세요.\n작업 중인 내용을 안전하게 저장합니다.\nWork는 나의 영역이라 마음 껏 자주 Save를 하고\n작업 기록을 남기셔도 됩니다.'
            },
            {
                'key': 'U',
                'title': '[U]pload',
                'message': 'U를 눌러 Work의 수정된 파일들을 Production에 업로드하세요.\n작업한 내용을 공유 영역에 반영하므로 신중하게 진행하세요.'
            },
            {
                'key': 'H',
                'title': '[H]istory',
                'message': 'H를 눌러 Git 히스토리를 조회해 보세요.\n과거 커밋 이력과 변경 내용을 확인할 수 있습니다.'
            },
            {
                'key': 'P',
                'title': '[P]roject',
                'message': 'P를 눌러 프로젝트를 관리해 보세요.\n새 프로젝트 생성, 전환, 삭제, 복제를 할 수 있습니다.\n과제 시작과 종료에 맞춰 프로젝트를 생성, [U]pload 그리고 삭제할 수 있습니다.'
            },
            {
                'key': 'T',
                'title': '[T]erminal',
                'message': 'T를 눌러 해당 경로를 새로운 Terminal로 실행합니다.\nTerminal에서 추가 작업을 진행하실 수 있습니다.'
            },
            {
                'key': None,  # 화살표 없이 중앙에 표시
                'title': 'CCCopy 작업 흐름 요약',
                'message': '* 업무 워크 플로우\n1. 과제 추가: 기능 수정/추가 요구 사항 발생\n2. Project 생성: Project Template 혹은 복제로 시작\n3. Work 수정: Save를 하면서 소스 수정\n   3-1. 자주 Save하고 History의 Rollback으로 원복 가능\n   3-2. 임시1, 임시2로 분리하고 싶으면\n        Project 복제로 Branch 작업\n   3-3. Branch 작업 후 선택받은 Project외\n        삭제 진행\n4. Upload: 현재 작업중인 Work를 Production으로 반영\n5. Project 삭제'
            }
        ]

    # ==================== 로그 파일 관리 메서드 ====================

    def _init_log_file(self):
        """로그 파일 초기화"""
        import datetime

        # 로그 디렉토리 생성
        os.makedirs(LOG_DIR, exist_ok=True)

        # 새 로그 파일 생성
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        self.current_log_file_path = os.path.join(LOG_DIR, f"{timestamp}.log")
        self.current_log_file = open(self.current_log_file_path, 'w', encoding='utf-8')

    def _write_log_to_file(self, log_entry):
        """로그를 파일에 기록"""
        if self.current_log_file:
            try:
                self.current_log_file.write(log_entry + '\n')
                self.current_log_file.flush()

                # MAX_LOG_LINES 초과시 새 파일 생성
                if len(self.logs) >= MAX_LOG_LINES:
                    self.current_log_file.close()
                    self._init_log_file()
                    self.logs.clear()
            except Exception as e:
                # 파일 쓰기 실패는 무시 (메모리 로그는 유지)
                pass

    def _close_log_file(self):
        """로그 파일 닫기"""
        if self.current_log_file:
            try:
                self.current_log_file.close()
            except:
                pass
            self.current_log_file = None

    # ==================== Cache 관련 메서드 ====================

    def get_cached_state(self, rel_path, current_mtime=None):
        """캐시에서 파일 상태 조회 (5분 타임아웃, mtime 비교)

        Args:
            rel_path: 상대 경로
            current_mtime: 현재 파일의 mtime (제공시 비교)
        """
        if rel_path not in self.file_state_cache:
            return None

        cache_entry = self.file_state_cache[rel_path]

        # 캐시 구조 체크 (하위 호환성)
        if len(cache_entry) == 2:
            # 구 버전 캐시 (timestamp, state)
            timestamp, state = cache_entry
            cached_mtime = None
        else:
            # 신 버전 캐시 (timestamp, state, mtime)
            timestamp, state, cached_mtime = cache_entry

        current_time = time.time()

        # 5분 타임아웃 체크
        if current_time - timestamp >= self.cache_timeout:
            del self.file_state_cache[rel_path]
            return None

        # mtime 비교 (제공된 경우)
        if current_mtime is not None and cached_mtime is not None:
            if current_mtime != cached_mtime:
                # mtime 불일치 → 파일 수정됨 → 캐시 무효
                self.add_log(f"mtime 변경 감지: {os.path.basename(rel_path)}", "DEBUG")
                del self.file_state_cache[rel_path]
                return None

        return state

    def update_cache(self, rel_path, state):
        """캐시 업데이트 - mtime 포함"""
        with self.refresh_lock:
            # 파일의 현재 mtime 가져오기
            if self.mode == ViewMode.WORK:
                full_path = os.path.join(self.workspace.working_dir, rel_path)
            else:
                full_path = os.path.join(self.workspace.production_dir, rel_path)

            try:
                mtime = os.path.getmtime(full_path)
            except:
                mtime = 0

            self.file_state_cache[rel_path] = (time.time(), state, mtime)

    def clear_cache(self):
        """전체 캐시 클리어 (Full Refresh용)"""
        with self.refresh_lock:
            self.file_state_cache.clear()
            self.add_log("전체 새로고침을 위해 캐시 삭제됨", "DEBUG")

    def clear_file_cache(self, rel_path):
        """특정 파일의 캐시만 클리어"""
        with self.refresh_lock:
            if rel_path in self.file_state_cache:
                del self.file_state_cache[rel_path]
                self.add_log(f"파일 캐시 삭제: {os.path.basename(rel_path)}", "DEBUG")

    # ==================== Thread 기반 Partial Refresh ====================

    def request_state_check(self, file_path, full_path):
        """ThreadPool로 파일 상태 확인 요청 (최대 2개 동시 실행)"""
        future = self.thread_pool.submit(self._check_file_state_async, file_path, full_path)
        self.futures.append(future)

    def _check_file_state_async(self, file_path, full_path):
        """비동기로 파일 상태 확인 (thread worker)"""
        try:
            # Thread 종료 요청 확인
            if self.stop_refresh_event.is_set():
                return

            # 실제 상태 계산
            if self.mode == ViewMode.WORK:
                production_file = os.path.join(self.workspace.production_dir, file_path)
                state = self.workspace.get_file_state(production_file, full_path, file_path)
            else:
                work_file = os.path.join(self.workspace.working_dir, file_path)
                state = self.workspace.get_file_state(full_path, work_file, file_path)

            # 캐시 업데이트
            self.update_cache(file_path, state)

            # UI 업데이트를 위한 pending 등록
            with self.refresh_lock:
                self.pending_updates[file_path] = state

        except Exception as e:
            # 오류 발생시 SAME으로 처리
            with self.refresh_lock:
                self.pending_updates[file_path] = FileState.SAME
            self.add_log(f"State check failed for {file_path}: {e}", "DEBUG")

    def stop_all_refresh_threads(self):
        """모든 refresh thread 종료 (ThreadPool 사용)"""
        self.stop_refresh_event.set()

        # 아직 시작되지 않은 Future들 취소
        cancelled_count = 0
        for future in self.futures:
            if future.cancel():  # 아직 시작 안 한 작업만 취소됨
                cancelled_count += 1

        if cancelled_count > 0:
            self.add_log(f"{cancelled_count}개의 대기 중인 작업을 취소했습니다", "DEBUG")

        # 이미 실행 중인 작업들은 stop_refresh_event로 조기 종료되도록 기다림
        # 하지만 길게 기다리지 않음 (최대 0.5초만 대기)
        running_count = 0
        for future in self.futures:
            if not future.done():
                running_count += 1
                try:
                    future.result(timeout=0.1)  # 0.1초만 대기
                except Exception:
                    pass  # 타임아웃이나 에러 무시

        if running_count > 0:
            self.add_log(f"{running_count}개의 실행 중인 작업을 대기했습니다 (아직 진행 중일 수 있음)", "DEBUG")

        self.futures.clear()
        self.stop_refresh_event.clear()

    def cleanup(self):
        """종료 시 모든 리소스 정리 (graceful shutdown)"""
        self.add_log("Cleaning up resources...", "INFO")

        # 1. 로그 파일 닫기
        self._close_log_file()

        # 2. Watch thread 종료
        self.stop_watch_thread()

        # 3. 모든 refresh thread 종료
        self.stop_all_refresh_threads()

        # 4. ThreadPoolExecutor 종료 (Python 3.7 호환)
        self.add_log("Shutting down thread pool...", "INFO")
        self.thread_pool.shutdown(wait=False)  # 대기하지 않고 즉시 종료 신호

        # 5. Git ls-files background thread 종료 (daemon이지만 명시적 종료)
        if self.tracked_files_loading:
            self.add_log("Waiting for background file loading to stop...", "INFO")
            # stop_refresh_event를 재활용하여 종료 신호
            self.stop_refresh_event.set()
            # 최대 1초 대기
            for _ in range(10):
                if not self.tracked_files_loading:
                    break
                time.sleep(0.1)
            self.stop_refresh_event.clear()

        # 6. Cache 클리어
        self.file_state_cache.clear()
        self.tracked_files_cache = None
        self.pending_updates.clear()

        self.add_log("Cleanup completed", "INFO")

    # ==================== Watch 시스템 메서드 ====================

    def start_watch_thread(self):
        """Watch thread 시작 - 현재 디렉토리 파일 변화 감지"""
        if self.watch_thread and self.watch_thread.is_alive():
            return  # 이미 실행 중

        self.watch_stop_event.clear()
        self.watch_directory_changed_event.clear()
        self.watch_thread = threading.Thread(
            target=self._watch_current_directory,
            daemon=True,
            name="cccopy_watch"
        )
        self.watch_thread.start()
        self.add_log("파일 변화 감지 시스템 시작", "DEBUG")

    def _watch_current_directory(self):
        """현재 디렉토리만 감지 (Thread 실행) - Git status 기반"""
        while not self.watch_stop_event.is_set():
            try:
                # Work 모드에서만 감지
                if self.mode == ViewMode.WORK:
                    # Git status로 현재 변경사항 확인
                    result = GitHelper.run_git_command(
                        ['status', '--porcelain'],
                        cwd=self.workspace.working_dir,
                        capture_output=True
                    )

                    # 현재 디렉토리 파일만 필터링 (subdirectory 제외)
                    prefix = self.current_directory + '/' if self.current_directory else ''
                    changed_files = [
                        line.strip().split()[-1]
                        for line in result.strip().split('\n')
                        if line.strip()
                    ]

                    current_dir_changed_files = []
                    for f in changed_files:
                        if self.current_directory:
                            # 현재 디렉토리 내 파일인지 확인
                            if f.startswith(prefix):
                                # subdirectory 제외: prefix 제거 후 '/' 없어야 함
                                relative = f[len(prefix):]
                                if '/' not in relative:
                                    current_dir_changed_files.append(f)
                        else:
                            # 루트 디렉토리: '/' 없는 파일만
                            if '/' not in f:
                                current_dir_changed_files.append(f)

                    # 현재 디렉토리의 git status를 문자열로 저장
                    current_status = '\n'.join(sorted(current_dir_changed_files))

                    # 이전 상태와 비교 (변경 감지)
                    # 주의: last_git_status가 None이 아니어야 함 (초기화 완료 체크)
                    if self.last_git_status is not None and current_status != self.last_git_status:
                        # 이전 상태에서 파일 목록 추출
                        prev_files = set(self.last_git_status.split('\n')) if self.last_git_status else set()
                        prev_files.discard('')  # 빈 문자열 제거
                        curr_files = set(current_dir_changed_files)

                        # 추가된 파일 (새로 수정됨)
                        added_files = curr_files - prev_files
                        # 제거된 파일 (원복됨)
                        removed_files = prev_files - curr_files

                        if added_files or removed_files:
                            log_parts = []
                            if added_files:
                                added_list = ', '.join([os.path.basename(f) for f in sorted(added_files)[:5]])
                                if len(added_files) > 5:
                                    added_list += f" 외 {len(added_files) - 5}개"
                                log_parts.append(f"수정: {added_list}")
                                # 추가된 파일의 캐시 클리어
                                for file_path in added_files:
                                    self.clear_file_cache(file_path)

                            if removed_files:
                                removed_list = ', '.join([os.path.basename(f) for f in sorted(removed_files)[:5]])
                                if len(removed_files) > 5:
                                    removed_list += f" 외 {len(removed_files) - 5}개"
                                log_parts.append(f"원복: {removed_list}")
                                # 제거된 파일의 캐시 클리어
                                for file_path in removed_files:
                                    self.clear_file_cache(file_path)

                            self.add_log(f"파일 변경 감지 ({', '.join(log_parts)}) - 자동 새로고침", "INFO")

                            with self.refresh_lock:
                                self.needs_auto_refresh = True
                                self.needs_redraw = True

                    # 현재 상태 저장
                    self.last_git_status = current_status

            except Exception as e:
                self.add_log(f"Watch error: {e}", "DEBUG")

            # WATCH_FILE_CHANGE_INTERVAL 초 대기 또는 디렉토리 변경 신호
            signaled = self.watch_directory_changed_event.wait(WATCH_FILE_CHANGE_INTERVAL)
            if signaled:
                self.last_git_status = None  # 디렉토리 변경시 상태 리셋 (초기화 안됨 상태)
                self.watch_directory_changed_event.clear()
                self.add_log("Watch 디렉토리 변경됨", "DEBUG")

    def notify_directory_changed(self):
        """디렉토리 변경 알림 (디렉토리 이동시 호출)"""
        if self.watch_directory_changed_event:
            self.watch_directory_changed_event.set()

    def stop_watch_thread(self):
        """Watch thread 종료"""
        if self.watch_thread and self.watch_thread.is_alive():
            self.add_log("파일 변화 감지 시스템 종료", "DEBUG")
            self.watch_stop_event.set()
            self.watch_directory_changed_event.set()
            self.watch_thread.join(timeout=1)

    # ==================== Pending Updates 처리 ====================

    def apply_pending_updates(self):
        """Pending 상태 업데이트를 UI에 반영"""
        if not self.pending_updates:
            return False

        updated = False
        with self.refresh_lock:
            for file_path, new_state in self.pending_updates.items():
                # directory_entries에서 해당 파일 찾아서 상태 업데이트
                for entry in self.directory_entries:
                    # Detail 모드의 'file'과 Tree 모드의 'tree_file' 모두 처리
                    if entry.get('type') in ('file', 'tree_file') and entry.get('path') == file_path:
                        entry['state'] = new_state
                        updated = True

            self.pending_updates.clear()

        return updated

    # ==================== Full / Partial Refresh ====================

    def build_directory_view_full(self):
        """Full Refresh - 모든 thread 종료, cache clear, 동기 처리"""
        # 1. 모든 refresh thread 종료
        self.stop_all_refresh_threads()

        # 2. Cache 클리어
        self.clear_cache()

        # 3. 사용자에게 시간이 걸릴 수 있다고 안내
        self.add_log("전체 새로고침 진행 중... 시간이 걸릴 수 있습니다.", "INFO")

        # 4. 기본 디렉토리 설정
        if self.mode == ViewMode.WORK:
            base_dir = self.workspace.working_dir
        else:
            base_dir = self.workspace.production_dir

        # 현재 디렉토리의 항목들 가져오기 (동기 모드)
        current_path = self.current_directory
        directories, files = self.get_current_directory_items(base_dir, current_path, async_mode=False)

        # 항목 목록 구성
        self.directory_entries = []

        # 상위 디렉토리 항목 추가 (루트가 아닌 경우)
        if self.current_directory:
            parent_path = self.get_parent_directory(self.current_directory)
            self.directory_entries.append({
                'type': 'parent',
                'name': '..',
                'path': parent_path
            })

        # 디렉토리들 추가 (정렬)
        for dir_name in sorted(directories):
            dir_path = os.path.join(self.current_directory, dir_name) if self.current_directory else dir_name
            self.directory_entries.append({
                'type': 'directory',
                'name': dir_name + "/",
                'path': dir_path
            })

        # 파일들 추가 (정렬) - 동기적으로 상태 확인
        for file_name in sorted(files):
            file_path = os.path.join(self.current_directory, file_name) if self.current_directory else file_name
            full_path = os.path.join(base_dir, file_path)

            # 파일 정보 가져오기
            try:
                size = os.path.getsize(full_path) if os.path.exists(full_path) else 0

                # Full refresh: 동기적으로 상태 계산 (thread 사용 안 함)
                if self.mode == ViewMode.WORK:
                    production_file = os.path.join(self.workspace.production_dir, file_path)
                    state = self.workspace.get_file_state(production_file, full_path, file_path)
                else:
                    work_file = os.path.join(self.workspace.working_dir, file_path)
                    state = self.workspace.get_file_state(full_path, work_file, file_path)

                # 캐시 업데이트
                self.update_cache(file_path, state)

            except Exception as e:
                size = 0
                state = FileState.SAME
                self.add_log(f"Failed to get state for {file_path}: {e}", "DEBUG")

            self.directory_entries.append({
                'type': 'file',
                'name': file_name,
                'path': file_path,
                'size': size,
                'state': state
            })

        # 선택 인덱스 조정
        if self.selected_index >= len(self.directory_entries):
            self.selected_index = max(0, len(self.directory_entries) - 1)

        self.add_log(f"전체 새로고침 완료: {len(self.directory_entries)}개 항목", "INFO")

    def get_current_project_tag(self):
        """현재 프로젝트의 이름과 TAG 정보를 가져옴"""
        try:
            import os
            import configparser

            # 현재 사용자의 홈 디렉토리에서 설정 확인
            home_dir = os.path.expanduser("~")

            # 프로젝트 하위 설정에서 현재 프로젝트 번호 가져오기
            project_global_config_path = os.path.join(home_dir, ".cccopy", "project", "config.ini")
            if not os.path.exists(project_global_config_path):
                return ""

            project_global_config = configparser.ConfigParser()
            project_global_config.read(project_global_config_path, encoding='utf-8')

            if not project_global_config.has_section('CONFIG'):
                return ""

            current_project = project_global_config.get('CONFIG', 'last_project', fallback='')
            if not current_project:
                return ""

            # 프로젝트별 설정 파일에서 프로젝트 이름과 TAG 가져오기
            project_config_path = os.path.join(home_dir, ".cccopy", "project", current_project, "config.ini")
            if not os.path.exists(project_config_path):
                return ""

            project_config = configparser.ConfigParser()
            project_config.read(project_config_path, encoding='utf-8')

            if not project_config.has_section('INFO'):
                return ""

            project_name = project_config.get('INFO', 'project_name', fallback='')
            tag = project_config.get('INFO', 'tag', fallback='')  # 'TAG' -> 'tag' (소문자)

            # 프로젝트 이름과 TAG를 조합해서 반환
            if project_name and tag:
                return f"{project_name}({tag})"
            elif project_name:
                return project_name
            elif tag:
                return tag
            else:
                return ""

        except Exception:
            return ""

    def get_display_width(self, text):
        """한글을 고려한 실제 표시 너비 계산"""
        width = 0
        for char in text:
            # 한글, 중문, 일문 등 동아시아 문자는 너비 2, 나머지는 1
            if ord(char) >= 0x1100 and ord(char) <= 0x11FF:  # 한글 자모
                width += 2
            elif ord(char) >= 0x3130 and ord(char) <= 0x318F:  # 한글 호환 자모
                width += 2
            elif ord(char) >= 0xAC00 and ord(char) <= 0xD7AF:  # 한글 음절
                width += 2
            elif ord(char) >= 0x4E00 and ord(char) <= 0x9FFF:  # CJK 한자
                width += 2
            elif ord(char) >= 0x3400 and ord(char) <= 0x4DBF:  # CJK 확장 A
                width += 2
            elif ord(char) >= 0xFF00 and ord(char) <= 0xFFEF:  # 전각 문자
                width += 2
            else:
                width += 1
        return width

    def truncate_text(self, text, max_width):
        """텍스트를 지정된 표시 너비에 맞게 자르기"""
        current_width = 0
        result = ""
        for char in text:
            # 직접 문자 폭 계산 (get_display_width 재귀 호출 방지)
            if ord(char) >= 0x1100 and ord(char) <= 0x11FF:  # 한글 자모
                char_width = 2
            elif ord(char) >= 0x3130 and ord(char) <= 0x318F:  # 한글 호환 자모
                char_width = 2
            elif ord(char) >= 0xAC00 and ord(char) <= 0xD7AF:  # 한글 음절
                char_width = 2
            elif ord(char) >= 0x4E00 and ord(char) <= 0x9FFF:  # CJK 한자
                char_width = 2
            elif ord(char) >= 0x3400 and ord(char) <= 0x4DBF:  # CJK 확장 A
                char_width = 2
            elif ord(char) >= 0xFF00 and ord(char) <= 0xFFEF:  # 전각 문자
                char_width = 2
            else:
                char_width = 1

            if current_width + char_width > max_width:
                break
            result += char
            current_width += char_width
        return result

    def format_text_with_korean_padding(self, text, total_width, align='center'):
        """한글 폭을 고려한 텍스트 패딩 및 정렬

        Args:
            text: 포맷할 텍스트
            total_width: 전체 표시 너비
            align: 정렬 방식 ('left', 'center', 'right')

        Returns:
            패딩이 적용된 텍스트
        """
        truncated = self.truncate_text(text, total_width)
        display_width = self.get_display_width(truncated)

        if align == 'left':
            return truncated + " " * (total_width - display_width)
        elif align == 'right':
            return " " * (total_width - display_width) + truncated
        else:  # center
            left_padding = (total_width - display_width) // 2
            right_padding = total_width - display_width - left_padding
            return " " * left_padding + truncated + " " * right_padding

    def create_dialog_line(self, content, dialog_width, align='center'):
        """다이얼로그 라인 생성 (한글 폭 고려)

        Args:
            content: 라인 내용
            dialog_width: 다이얼로그 전체 너비
            align: 정렬 방식

        Returns:
            "│ content │" 형태의 다이얼로그 라인
        """
        content_width = dialog_width - 4  # 양쪽 "│ " 제외
        formatted_content = self.format_text_with_korean_padding(content, content_width, align)
        return "│ " + formatted_content + " │"

    def messagebox(self, message, title="", message_type="info", buttons="ok", default=""):
        """TUI 모드 메시지박스 구현"""
        import curses

        # 대화상자 활성화 플래그 설정
        self.dialog_active = True

        try:

            # 메시지 크기에 따라 대화상자 크기 동적 조정
            lines = message.split('\n')
            content_height = len(lines)
            content_width = max(len(line) for line in lines) if lines else 50

            # 최소/최대 크기 설정
            min_height, max_height = 8, curses.LINES - 4
            min_width, max_width = 50, curses.COLS - 4

            # 계산된 크기 적용 (여백 포함)
            height = min(max(content_height + 6, min_height), max_height)  # 제목(3) + 버튼(3) 여백
            width = min(max(content_width + 4, min_width), max_width)      # 좌우 여백

            max_y, max_x = curses.LINES, curses.COLS
            start_y = max(0, (max_y - height) // 2)
            start_x = max(0, (max_x - width) // 2)

            # 그림자 효과를 위한 백그라운드 윈도우 (1칸 그림자)
            shadow_win = curses.newwin(height, width + 1, start_y + 1, start_x + 1)
            shadow_win.bkgd(' ', curses.A_REVERSE)
            shadow_win.noutrefresh()

            # 박스 윈도우 생성
            dialog_win = curses.newwin(height, width, start_y, start_x)
            dialog_win.bkgd(' ', curses.A_NORMAL)
            dialog_win.keypad(1)  # 특수 키 활성화 (화살표 키 등)
            # 단순 메시지 대화상자는 블로킹 모드로 동작 (타임아웃 없음)

            # 색상 설정 (안전한 접근)
            colors = getattr(self, 'colors', {})
            if message_type == "error":
                color = colors.get('conflicted', curses.A_NORMAL)
                border_color = colors.get('conflicted', curses.A_NORMAL)
            elif message_type == "warn":
                color = colors.get('modified', curses.A_NORMAL)
                border_color = colors.get('modified', curses.A_NORMAL)
            else:
                color = colors.get('default', curses.A_NORMAL)
                border_color = colors.get('default', curses.A_NORMAL)

            # 대화상자 그리기
            dialog_win.box()

            # 제목 표시
            if title:
                title_text = f" {title} "
                title_x = max(0, (width - len(title_text)) // 2)
                dialog_win.addstr(0, title_x, title_text, curses.A_BOLD | border_color)

            # 메시지 표시 (여러 줄 지원) - 동적 크기에 맞춰 모든 줄 표시
            lines = message.split('\n')
            max_message_lines = height - 6  # 제목(3) + 버튼(3) 공간 제외
            for i, line in enumerate(lines[:max_message_lines]):
                if len(line) > width - 4:
                    line = line[:width-7] + "..."
                if 2 + i < height - 4:  # 버튼을 위한 공간 확보
                    dialog_win.addstr(2 + i, 2, line, color)

            # 화면에 그리기
            dialog_win.noutrefresh()
            curses.doupdate()  # 대화상자 원자적 표시

            # 버튼 표시 및 처리
            result = self._handle_dialog_buttons(dialog_win, buttons, default, height, width)

            # 정리
            del shadow_win
            del dialog_win

            return result

        except Exception as e:
            self.add_log(f"Dialog error: {e}", "ERROR")
            # 폴백: CLI 방식 사용
            from ..utils.ui_handler import _cli_messagebox
            return _cli_messagebox(message, title, message_type, buttons, default)
        finally:
            # 대화상자 비활성화
            self.dialog_active = False
            # 화면 강제 새로고침 - 대화상자 흔적 제거
            self.force_refresh_screen()

    def _handle_dialog_buttons(self, dialog_win, buttons, default, height, width):
        """다이얼로그 버튼 처리"""
        import curses

        if buttons == "ok":
            dialog_win.addstr(height - 2, (width - 6) // 2, "[ OK ]", curses.A_REVERSE)
            dialog_win.noutrefresh()
            curses.doupdate()  # 대화상자 원자적 표시

            while True:
                key = dialog_win.getch()
                if key in [10, 13, 27, ord(' ')]:  # Enter, ESC, Space
                    return "ok"
                elif key == ord('q') or key == ord('Q'):
                    return "ok"

        elif buttons == "yesno":
            # Yes/No 버튼
            button_y = height - 3
            yes_x = width // 4 - 2
            no_x = 3 * width // 4 - 2

            selected = 0 if default.lower() in ('y', 'yes') else 1

            while True:
                # 키 도움말 표시
                help_text = "←→/Tab: Navigate, Y/N: Direct, Enter: Select, ESC: Cancel"
                if len(help_text) <= width - 4:
                    dialog_win.addstr(height - 4, 2, help_text, getattr(self, 'colors', {}).get('default', curses.A_DIM))

                # 버튼 그리기
                yes_style = curses.A_REVERSE | curses.A_BOLD if selected == 0 else curses.A_NORMAL
                no_style = curses.A_REVERSE | curses.A_BOLD if selected == 1 else curses.A_NORMAL

                dialog_win.addstr(button_y, yes_x, "[ Yes ]", yes_style)
                dialog_win.addstr(button_y, no_x, "[ No ]", no_style)

                # 현재 선택 표시
                if selected == 0:
                    dialog_win.addstr(button_y, yes_x - 2, "→", curses.A_BOLD)
                    dialog_win.addstr(button_y, no_x - 2, " ")
                else:
                    dialog_win.addstr(button_y, yes_x - 2, " ")
                    dialog_win.addstr(button_y, no_x - 2, "→", curses.A_BOLD)
                dialog_win.noutrefresh()
                curses.doupdate()  # 대화상자 원자적 표시

                key = dialog_win.getch()
                # 블로킹 모드이므로 타임아웃 처리 불필요

                self.add_log(f"Yes/No: Key pressed: {key} (curses.KEY_LEFT={curses.KEY_LEFT}, curses.KEY_RIGHT={curses.KEY_RIGHT})", "DEBUG")

                # 화살표 키는 다양한 값을 가질 수 있음 (환경에 따라)
                # 일반적인 값들: LEFT=260, RIGHT=261, 또는 다른 값들
                if key == curses.KEY_LEFT or key == 260 or key == ord('h'):
                    selected = 0
                    self.add_log("Yes/No: Left Arrow/H pressed - focused on Yes", "DEBUG")
                    # 포커스만 이동, 결정하지 않음
                elif key == curses.KEY_RIGHT or key == 261 or key == ord('l'):
                    selected = 1
                    self.add_log("Yes/No: Right Arrow/L pressed - focused on No", "DEBUG")
                    # 포커스만 이동, 결정하지 않음
                elif key == ord('\t') or key == 9:  # Tab key
                    selected = 1 - selected  # 0 <-> 1 토글
                    self.add_log(f"Yes/No: Tab pressed - focused on {'Yes' if selected == 0 else 'No'}", "DEBUG")
                    # 포커스만 이동, 결정하지 않음
                elif key == ord('y') or key == ord('Y'):
                    self.add_log("Yes/No: Y key pressed - returning yes", "DEBUG")
                    return "yes"
                elif key == ord('n') or key == ord('N'):
                    self.add_log("Yes/No: N key pressed - returning no", "DEBUG")
                    return "no"
                elif key in [10, 13]:  # Enter
                    result = "yes" if selected == 0 else "no"
                    self.add_log(f"Yes/No: Enter pressed - returning {result}", "DEBUG")
                    return result
                elif key == 27:  # ESC - 기본값으로 반환
                    result = default if default in ['yes', 'no'] else "no"
                    self.add_log(f"Yes/No: ESC pressed - returning default '{result}'", "DEBUG")
                    return result
                elif key == ord('q') or key == ord('Q'):
                    self.add_log("Yes/No: Q key pressed - returning no", "DEBUG")
                    return "no"
                else:
                    # 알려지지 않은 키는 무시하고 계속
                    if key != -1:  # 타임아웃이 아닌 경우만 로그
                        self.add_log(f"Yes/No: Unknown key {key} - ignoring", "DEBUG")

        elif buttons == "input":
            # 고급 텍스트 입력 처리
            return self._handle_text_input(dialog_win, height, width, default)

        return default

    def _handle_text_input(self, dialog_win, height, width, default=""):
        """고급 텍스트 입력 처리 - 실시간 표시, 커서 이동, 스크롤, 한글 지원"""
        import curses
        import unicodedata

        # 입력 상태 변수
        text = list(default)  # 문자 리스트로 관리 (삽입/삭제 용이)
        cursor_pos = len(text)  # 커서 위치 (텍스트 내 인덱스)
        scroll_offset = 0  # 화면 스크롤 오프셋

        # UTF-8 멀티바이트 버퍼
        multibyte_buffer = []

        # 커서 깜박임 상태
        cursor_visible = True
        cursor_blink_counter = 0

        def get_char_width(char):
            """문자의 화면 표시 폭 계산 (한글은 2, 영문은 1)"""
            if ord(char) <= 127:  # ASCII
                return 1
            # 한글 및 동아시아 문자 폭 계산
            eaw = unicodedata.east_asian_width(char)
            if eaw in ('F', 'W'):  # Full-width, Wide
                return 2
            else:
                return 1

        def get_text_display_width(text_list, start=0, end=None):
            """텍스트 리스트의 화면 표시 폭 계산"""
            if end is None:
                end = len(text_list)
            return sum(get_char_width(char) for char in text_list[start:end])


        # 입력 영역 계산
        input_y = height - 3
        input_x = 4  # "> " 다음 위치
        max_display_width = width - 8  # 양쪽 여백 고려

        # 키 도움말 표시
        help_text = "←→: Move cursor, Backspace/Del: Delete, Enter: OK, ESC: Cancel"
        if len(help_text) <= width - 4:
            dialog_win.addstr(height - 4, 2, help_text, getattr(self, 'colors', {}).get('default', curses.A_DIM))

        def update_display():
            """화면 업데이트 - 한글 지원"""
            # 입력 라벨과 프롬프트
            dialog_win.addstr(input_y - 1, 2, "Input:")
            dialog_win.addstr(input_y, 2, "> ")

            # 스크롤 조정 - 화면 폭 기준
            nonlocal scroll_offset

            # 표시 가능한 텍스트 범위 계산
            display_start = scroll_offset
            display_end = len(text)
            display_width = 0

            # 스크롤 오프셋부터 시작해서 화면에 들어갈 만큼만 계산
            for i in range(scroll_offset, len(text)):
                char_width = get_char_width(text[i])
                if display_width + char_width > max_display_width:
                    display_end = i
                    break
                display_width += char_width

            # 커서가 화면 밖으로 나가면 스크롤 조정
            if cursor_pos < scroll_offset:
                # 왼쪽으로 스크롤
                scroll_offset = cursor_pos
            elif cursor_pos > display_end:
                # 오른쪽으로 스크롤 - 커서가 화면 오른쪽 끝에 오도록
                scroll_offset = cursor_pos
                while scroll_offset > 0:
                    test_width = get_text_display_width(text, scroll_offset - 1, cursor_pos)
                    if test_width <= max_display_width:
                        scroll_offset -= 1
                    else:
                        break

            # 다시 표시 범위 계산
            display_start = scroll_offset
            display_end = len(text)
            display_width = 0

            for i in range(scroll_offset, len(text)):
                char_width = get_char_width(text[i])
                if display_width + char_width > max_display_width:
                    display_end = i
                    break
                display_width += char_width

            # 표시할 텍스트 생성
            display_text = ''.join(text[display_start:display_end])

            # 입력 영역 클리어 후 텍스트 표시
            dialog_win.addstr(input_y, input_x, " " * max_display_width)
            if display_text:
                try:
                    # UTF-8 인코딩 처리
                    safe_display_text = display_text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                    dialog_win.addstr(input_y, input_x, safe_display_text)
                except Exception as e:
                    # 한글 표시 실패시 ASCII로 대체
                    ascii_text = ''.join(c if ord(c) < 128 else '?' for c in display_text)
                    dialog_win.addstr(input_y, input_x, ascii_text)
                    self.add_log(f"Korean display error: {e}", "DEBUG")

            # 커서 위치 계산 및 표시
            if scroll_offset <= cursor_pos <= display_end:
                cursor_display_width = get_text_display_width(text, scroll_offset, cursor_pos)
                screen_cursor_x = input_x + cursor_display_width

                if screen_cursor_x < input_x + max_display_width:
                    # 커서 깜박임 효과
                    if cursor_visible:
                        # 커서가 텍스트 끝에 있는 경우
                        if cursor_pos >= len(text):
                            # 빈 공간에 블록 커서 표시
                            dialog_win.addstr(input_y, screen_cursor_x, "█", curses.A_BOLD)
                        else:
                            # 문자 위에 블록 커서 표시
                            cursor_char = text[cursor_pos]
                            char_width = get_char_width(cursor_char)

                            try:
                                if char_width == 2:  # 한글 등 2칸 문자
                                    # 2칸 문자 전체를 반전 표시
                                    dialog_win.addstr(input_y, screen_cursor_x, cursor_char, curses.A_REVERSE | curses.A_BOLD)
                                else:  # 1칸 문자
                                    dialog_win.addstr(input_y, screen_cursor_x, cursor_char, curses.A_REVERSE | curses.A_BOLD)
                            except:
                                # 표시 실패시 블록 커서
                                if char_width == 2:
                                    dialog_win.addstr(input_y, screen_cursor_x, "██", curses.A_REVERSE)
                                else:
                                    dialog_win.addstr(input_y, screen_cursor_x, "█", curses.A_REVERSE)

                    # 커서 위치로 이동 (터미널 커서도 맞춤)
                    dialog_win.move(input_y, screen_cursor_x)

            # 스크롤 표시기
            if scroll_offset > 0:
                dialog_win.addstr(input_y, input_x - 1, "<", curses.A_BOLD)
            else:
                dialog_win.addstr(input_y, input_x - 1, " ")

            if display_end < len(text):
                dialog_win.addstr(input_y, input_x + max_display_width, ">", curses.A_BOLD)
            else:
                dialog_win.addstr(input_y, input_x + max_display_width, " ")

            dialog_win.noutrefresh()
            curses.doupdate()  # 대화상자 원자적 표시

        # 초기 화면 표시
        update_display()

        while True:
            key = dialog_win.getch()
            if key == -1:  # timeout - 커서 깜박임 처리
                cursor_blink_counter += 1
                if cursor_blink_counter >= 5:  # 0.5초마다 깜박임 (100ms * 5)
                    cursor_visible = not cursor_visible
                    cursor_blink_counter = 0
                    update_display()
                continue

            # 키 입력이 있으면 커서를 표시 상태로 복원
            cursor_visible = True
            cursor_blink_counter = 0

            self.add_log(f"Input: Key {key} pressed, cursor={cursor_pos}, text_len={len(text)}", "DEBUG")

            if key in [10, 13]:  # Enter - 입력 완료
                result = ''.join(text)
                self.add_log(f"Input completed: '{result}'", "INFO")
                return result

            elif key == 27:  # ESC - 취소
                self.add_log("ESC로 입력 취소됨", "DEBUG")
                return None

            elif key == curses.KEY_LEFT or key == 260:  # 왼쪽 화살표
                if cursor_pos > 0:
                    cursor_pos -= 1
                    self.add_log(f"Cursor moved left to {cursor_pos}", "DEBUG")

            elif key == curses.KEY_RIGHT or key == 261:  # 오른쪽 화살표
                if cursor_pos < len(text):
                    cursor_pos += 1
                    self.add_log(f"Cursor moved right to {cursor_pos}", "DEBUG")

            elif key == curses.KEY_HOME or key == 262:  # Home - 줄 시작
                cursor_pos = 0
                self.add_log("Cursor moved to start", "DEBUG")

            elif key == curses.KEY_END or key == 360:  # End - 줄 끝
                cursor_pos = len(text)
                self.add_log("Cursor moved to end", "DEBUG")

            elif key in [curses.KEY_BACKSPACE, 127, 8]:  # Backspace
                if cursor_pos > 0:
                    deleted_char = text.pop(cursor_pos - 1)
                    cursor_pos -= 1
                    self.add_log(f"Backspace: deleted '{deleted_char}' at {cursor_pos}", "DEBUG")

            elif key == curses.KEY_DC or key == 330:  # Delete
                if cursor_pos < len(text):
                    deleted_char = text.pop(cursor_pos)
                    self.add_log(f"Delete: deleted '{deleted_char}' at {cursor_pos}", "DEBUG")

            elif key == ord('\t'):  # Tab - 4칸 공백 삽입
                for _ in range(4):
                    text.insert(cursor_pos, ' ')
                    cursor_pos += 1
                self.add_log("Tab: inserted 4 spaces", "DEBUG")

            elif 32 <= key <= 126:  # ASCII 인쇄 가능한 문자
                char = chr(key)
                text.insert(cursor_pos, char)
                cursor_pos += 1
                self.add_log(f"Inserted ASCII '{char}' at {cursor_pos-1}", "DEBUG")

            elif key >= 128:  # UTF-8 멀티바이트 시작
                # UTF-8 바이트를 버퍼에 추가
                multibyte_buffer.append(key)

                # UTF-8 시퀀스 완성 체크
                try:
                    # 바이트 배열을 UTF-8로 디코딩 시도
                    byte_data = bytes(multibyte_buffer)
                    unicode_char = byte_data.decode('utf-8')

                    # 성공하면 문자 삽입
                    text.insert(cursor_pos, unicode_char)
                    cursor_pos += 1
                    self.add_log(f"Inserted Korean '{unicode_char}' (width={get_char_width(unicode_char)}) at {cursor_pos-1}", "DEBUG")

                    # 버퍼 클리어
                    multibyte_buffer.clear()

                except UnicodeDecodeError:
                    # 아직 완성되지 않은 시퀀스 - 더 기다림
                    if len(multibyte_buffer) >= 4:  # UTF-8 최대 4바이트
                        self.add_log(f"Invalid UTF-8 sequence: {multibyte_buffer}", "DEBUG")
                        multibyte_buffer.clear()
                    continue  # 화면 업데이트 안 함
                except Exception as e:
                    self.add_log(f"UTF-8 decode error: {e}", "DEBUG")
                    multibyte_buffer.clear()
                    continue

            else:
                # 알려지지 않은 키
                self.add_log(f"Input: Unknown key {key} ignored", "DEBUG")
                continue

            # 화면 업데이트
            update_display()

    def display_message(self, message, level="INFO"):
        """TUI용 메시지 표시"""
        # level이 이미 포맷된 형태([INFO ], [WARN ] 등)라면 직접 로그에 추가
        if level.startswith("[") and level.endswith("]"):
            import datetime
            timestamp = datetime.datetime.now().strftime("%y%m%d %H:%M:%S")
            log_entry = f"{timestamp} {level} {message}"
            self.logs.append(log_entry)
            self._write_log_to_file(log_entry)
            if len(self.logs) > MAX_LOG_LINES:
                self.logs.pop(0)
            # 로그가 추가되었으므로 화면 갱신 필요
            self.needs_redraw = True
        else:
            self.add_log(message, level)

    def test_messagebox(self):
        """F1키 테스트용 messagebox"""
        self.add_log("Testing messagebox - Hello World", "INFO")
        try:
            # Hello World 테스트
            result = self.messagebox("Hello World!\n\nThis is a test messagebox.\nPress Enter to continue.",
                                   "Test Dialog", "info", "ok", "")
            self.add_log(f"OK Dialog result: {result}", "INFO")

            # Yes/No 테스트 (기본값: yes)
            result2 = self.messagebox("Do you want to test Yes/No dialog?\n\nTry Tab, Y/N keys, Enter, ESC",
                                    "Yes/No Test (default: Yes)", "info", "yesno", "yes")
            self.add_log(f"Yes/No result: {result2} (default was 'yes')", "INFO")

            if result2 == "yes":
                # Yes/No 테스트 (기본값: no)
                result3 = self.messagebox("Another Yes/No test.\n\nThis time default is No.\nTry ESC to see default behavior.",
                                        "Yes/No Test (default: No)", "warn", "yesno", "no")
                self.add_log(f"Yes/No result: {result3} (default was 'no')", "INFO")

                if result3 == "yes":
                    # Input 테스트
                    result4 = self.messagebox("Enter your name:",
                                            "Input Test", "info", "input", "User")
                    self.add_log(f"Input result: '{result4}'", "INFO")
            else:
                self.add_log("Test sequence stopped by user", "INFO")
        except Exception as e:
            self.add_log(f"Messagebox test failed: {e}", "ERROR")

    def init_colors(self):
        """색상 초기화"""
        try:
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()

                # 색상 쌍 정의
                curses.init_pair(1, curses.COLOR_WHITE, -1)    # 기본 텍스트
                curses.init_pair(2, curses.COLOR_YELLOW, -1)   # 수정됨 (Modified)
                curses.init_pair(3, curses.COLOR_GREEN, -1)    # 동일 (Same)
                curses.init_pair(4, curses.COLOR_BLUE, -1)     # 업데이트됨 (Updated)
                curses.init_pair(5, curses.COLOR_RED, -1)      # 충돌 (Conflicted)
                curses.init_pair(6, curses.COLOR_CYAN, -1)     # 새 파일 (New)
                curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_WHITE)  # 선택됨
                curses.init_pair(8, curses.COLOR_BLUE, -1)     # 폴더
                # 로그 색상 쌍 추가
                curses.init_pair(9, curses.COLOR_GREEN, -1)    # INFO - 녹색
                curses.init_pair(10, curses.COLOR_WHITE, -1)   # DEBUG - 회색 (어둡게 처리)
                curses.init_pair(11, curses.COLOR_RED, -1)     # ERROR - 빨간색
                curses.init_pair(12, curses.COLOR_WHITE, -1)   # LOG - 기본 흰색
                curses.init_pair(13, curses.COLOR_YELLOW, -1)  # WARNING - 노란색
                curses.init_pair(14, curses.COLOR_MAGENTA, -1)    # HIGH - 보라색 (MAGENTA)
                curses.init_pair(15, curses.COLOR_WHITE, -1)   # PENDING - 회색 (어둡게 처리, DEBUG와 동일)
                curses.init_pair(16, curses.COLOR_WHITE, curses.COLOR_GREEN)  # 튜토리얼 - 녹색 배경

                self.colors = {
                    'default': curses.color_pair(1),
                    'modified': curses.color_pair(2),
                    'same': curses.color_pair(3),
                    'updated': curses.color_pair(4),
                    'conflicted': curses.color_pair(5),
                    'new': curses.color_pair(6),
                    'selected': curses.color_pair(7),
                    'folder': curses.color_pair(8),
                    'pending': curses.color_pair(15) | curses.A_DIM,  # 회색 + 어둡게 (상태 확인 중, DEBUG와 동일)
                    # 로그 색상 추가
                    'log_info': curses.color_pair(9),           # 녹색
                    'log_debug': curses.color_pair(10) | curses.A_DIM,  # 흰색 + 어둡게
                    'log_error': curses.color_pair(11),         # 빨간색
                    'log_warning': curses.color_pair(13),       # 노란색
                    'log_high': curses.color_pair(14) | curses.A_BOLD,  # 보라색 + 굵게 (HIGH)
                    'log_default': curses.color_pair(12),       # 기본 흰색
                }
            else:
                # 색상 미지원시 기본값
                self.colors = {
                    'default': 0,
                    'modified': 0,
                    'same': 0,
                    'updated': 0,
                    'conflicted': 0,
                    'new': 0,
                    'selected': curses.A_REVERSE,  # 반전 효과
                    'folder': curses.A_BOLD,       # 굵은 글씨
                    'wait': curses.A_DIM,          # 어둡게 (상태 확인 중)
                    # 로그 색상 (색상 미지원시 기본값)
                    'log_info': curses.A_BOLD,      # 굵게 (녹색 대신)
                    'log_debug': curses.A_DIM,      # 어둡게 (회색 대신)
                    'log_error': curses.A_BOLD,     # 굵게 (빨간색 대신)
                    'log_warning': curses.A_BOLD,   # 굵게 (노란색 대신)
                    'log_high': curses.A_BOLD,      # 굵게 (청록색 대신)
                    'log_default': 0,
                }
        except Exception:
            # 완전 실패시 기본값
            self.colors = {
                'default': 0,
                'modified': 0,
                'same': 0,
                'updated': 0,
                'conflicted': 0,
                'new': 0,
                'selected': curses.A_REVERSE,
                'folder': curses.A_BOLD,
                'wait': curses.A_DIM,          # 어둡게 (상태 확인 중)
                # 로그 색상 (완전 실패시 기본값)
                'log_info': curses.A_BOLD,      # 굵게 (녹색 대신)
                'log_debug': curses.A_DIM,      # 어둡게 (회색 대신)
                'log_error': curses.A_BOLD,     # 굵게 (빨간색 대신)
                'log_warning': curses.A_BOLD,   # 굵게 (노란색 대신)
                'log_high': curses.A_BOLD,      # 굵게 (청록색 대신)
                'log_default': 0,
            }

    def safe_addstr(self, win, y, x, text, attr=curses.A_NORMAL):
        """UTF-8 안전한 문자열 출력 함수 - 색상 안전성 포함"""
        try:
            # 색상 속성 안전성 검증
            safe_attr = attr
            if isinstance(attr, str):
                # 문자열인 경우 기본값으로 대체
                safe_attr = curses.A_NORMAL
            elif not isinstance(attr, int):
                # 정수가 아닌 경우 기본값으로 대체
                safe_attr = curses.A_NORMAL

            # UTF-8 인코딩 안전성 확보
            safe_text = text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
            win.addstr(y, x, safe_text, safe_attr)
            return True
        except curses.error:
            # 실패시 ASCII 변환 시도
            try:
                ascii_text = ''.join(c if ord(c) < 128 else '?' for c in text)
                win.addstr(y, x, ascii_text, curses.A_NORMAL)  # 기본 색상으로 시도
                return True
            except curses.error:
                return False

    def add_log(self, message: str, level: str = "LOG"):
        """로그 추가"""
        import datetime

        # 현재 시간을 YYMMDD HH:MM:SS 형식으로 생성
        timestamp = datetime.datetime.now().strftime("%y%m%d %H:%M:%S")

        # level이 이미 포맷된 형태([INFO ], [WARN ] 등)인지 확인
        if level.startswith("[") and level.endswith("]"):
            level_formatted = level
        else:
            # 기존 방식과의 호환성을 위해 포맷팅
            level_formatted = {
                "INFO": "[INFO ]",
                "WARNING": "[WARN ]",
                "ERROR": "[ERROR]",
                "DEBUG": "[DEBUG]",
                "HIGH": "[HIGH ]"
            }.get(level, f"[{level}]")

        # DEBUG 로그는 항상 추가 (로그 뷰어에서 토글로 필터링)
        log_entry = f"{timestamp} {level_formatted} {message}"
        self.logs.append(log_entry)
        self._write_log_to_file(log_entry)
        if len(self.logs) > MAX_LOG_LINES:
            self.logs.pop(0)

        # 로그가 추가되었으므로 화면 갱신 필요
        self.needs_redraw = True

    def get_git_tracked_files(self, base_dir: str, async_mode=False) -> List[str]:
        """Git에 tracked 된 파일 목록 가져오기 (캐싱 적용)"""
        try:
            if not GitHelper.is_git_repo(base_dir):
                return []

            # 캐시 확인 (1분 이내)
            current_time = time.time()
            if (self.tracked_files_cache is not None and
                current_time - self.tracked_files_cache_time < self.tracked_files_cache_timeout):
                return self.tracked_files_cache

            # 로딩 중이면 빈 리스트 반환 (async 모드)
            if async_mode and self.tracked_files_loading:
                return []

            # 캐시 미스 - Git에서 가져오기
            if async_mode:
                # Background thread로 실행
                self.tracked_files_loading = True
                thread = threading.Thread(
                    target=self._load_tracked_files_async,
                    args=(base_dir,),
                    daemon=True
                )
                thread.start()
                return []
            else:
                # 동기 모드 (Full Refresh)
                result = GitHelper.run_git_command(['ls-files'], cwd=base_dir, capture_output=True)
                if result:
                    tracked_files = [f.strip() for f in result.split('\n') if f.strip()]
                    # 캐시 업데이트
                    self.tracked_files_cache = tracked_files
                    self.tracked_files_cache_time = current_time
                    return tracked_files
                return []
        except Exception as e:
            self.add_log(f"Git tracked files error: {e}", "ERROR")
            return []

    def _load_tracked_files_async(self, base_dir: str):
        """Background에서 git ls-files 실행"""
        try:
            self.add_log("백그라운드에서 파일 목록 로딩 중...", "INFO")

            # 종료 신호 확인
            if self.stop_refresh_event.is_set():
                self.add_log("Background loading cancelled", "INFO")
                with self.refresh_lock:
                    self.tracked_files_loading = False
                return

            result = GitHelper.run_git_command(['ls-files'], cwd=base_dir, capture_output=True)

            # 종료 신호 재확인 (git 명령 실행 후)
            if self.stop_refresh_event.is_set():
                self.add_log("Background loading cancelled", "INFO")
                with self.refresh_lock:
                    self.tracked_files_loading = False
                return

            if result:
                tracked_files = [f.strip() for f in result.split('\n') if f.strip()]
                with self.refresh_lock:
                    self.tracked_files_cache = tracked_files
                    self.tracked_files_cache_time = time.time()
                    self.tracked_files_loading = False
                self.add_log(f"파일 목록 로드됨: {len(tracked_files)}개 파일", "DEBUG")
                # UI 새로고침 요청
                self.needs_redraw = True
        except Exception as e:
            self.add_log(f"Background file list load failed: {e}", "ERROR")
            with self.refresh_lock:
                self.tracked_files_loading = False

    def get_current_directory_items(self, base_dir: str, current_path: str, async_mode=False):
        """현재 디렉토리의 항목들만 가져오기 (즉시 파일시스템 스캔, Git 정보는 background)"""
        try:
            import fnmatch

            if current_path:
                scan_dir = os.path.join(base_dir, current_path)
            else:
                scan_dir = base_dir

            if not os.path.exists(scan_dir):
                return set(), []

            directories = set()
            files = []

            # 1. 파일시스템 기반으로 즉시 스캔 (빠름!)
            for item in os.listdir(scan_dir):
                # .git, .cccopy 제외
                if item in ['.git', '.cccopy']:
                    continue

                item_path = os.path.join(scan_dir, item)

                if os.path.isdir(item_path):
                    directories.add(item)
                else:
                    files.append(item)

            # 2. SOURCES 패턴 기반 필터링 (async/sync 모두 적용)
            source_patterns = self.workspace.get_source_patterns()
            exclude_patterns = self.workspace.get_exclude_patterns()

            # 2-1. SOURCES 패턴으로 디렉토리 필터링
            if not current_path:  # 루트 디렉토리인 경우
                valid_dirs = set()
                for pattern in source_patterns:
                    # 패턴에서 첫 번째 디렉토리 추출
                    # 예: "AAA/**" -> "AAA", "BBB/*" -> "BBB"
                    parts = pattern.split('/')
                    if parts and parts[0] and not parts[0].startswith('*'):
                        valid_dirs.add(parts[0])

                # SOURCES에 정의된 디렉토리만 표시
                directories = directories & valid_dirs

            # 2-2. EXCLUDES 패턴으로 디렉토리 필터링
            filtered_dirs = set()
            for dir_name in directories:
                # 현재 경로 기준 상대 경로
                if current_path:
                    rel_path = os.path.join(current_path, dir_name)
                else:
                    rel_path = dir_name

                # EXCLUDES 패턴 체크
                exclude = False
                for exclude_pattern in exclude_patterns:
                    # 디렉토리 패턴 매칭
                    # 예: "**/node_modules/" -> "AAA/node_modules" 매칭
                    #     "**/backup*" -> "AAA/backup_old" 매칭
                    if fnmatch.fnmatch(rel_path, exclude_pattern.rstrip('/')):
                        exclude = True
                        break
                    # 디렉토리 이름만으로도 체크
                    if fnmatch.fnmatch(dir_name, exclude_pattern.rstrip('/')):
                        exclude = True
                        break
                    # **/ 패턴 처리
                    if exclude_pattern.startswith('**/'):
                        pattern_tail = exclude_pattern[3:].rstrip('/')
                        if fnmatch.fnmatch(dir_name, pattern_tail):
                            exclude = True
                            break

                if not exclude:
                    filtered_dirs.add(dir_name)

            directories = filtered_dirs

            # 2-3. EXCLUDES 패턴으로 파일 필터링
            filtered_files = []
            for file_name in files:
                # 현재 경로 기준 상대 경로
                if current_path:
                    rel_path = os.path.join(current_path, file_name)
                else:
                    rel_path = file_name

                # EXCLUDES 패턴 체크
                exclude = False
                for exclude_pattern in exclude_patterns:
                    # 파일 패턴 매칭
                    # 예: "**/*.log" -> "AAA/test.log" 매칭
                    #     "**/*.tmp" -> "BBB/cache.tmp" 매칭
                    if fnmatch.fnmatch(rel_path, exclude_pattern):
                        exclude = True
                        break
                    # 파일 이름만으로도 체크
                    if fnmatch.fnmatch(file_name, exclude_pattern):
                        exclude = True
                        break
                    # **/ 패턴 처리
                    if exclude_pattern.startswith('**/'):
                        pattern_tail = exclude_pattern[3:]
                        if fnmatch.fnmatch(file_name, pattern_tail):
                            exclude = True
                            break

                if not exclude:
                    filtered_files.append(file_name)

            files = filtered_files

            # 3. SOURCES 패턴으로 파일 필터링 (Git tracked와 무관하게 적용)
            sources_filtered_files = []
            for file_name in files:
                # 현재 경로 기준 상대 경로
                if current_path:
                    rel_path = os.path.join(current_path, file_name)
                else:
                    rel_path = file_name

                # SOURCES 패턴 체크
                match = False
                for pattern in source_patterns:
                    if fnmatch.fnmatch(rel_path, pattern):
                        match = True
                        break
                    # **/ 패턴 처리
                    if '**' in pattern:
                        # AAA/** -> AAA로 시작하는 모든 파일
                        if pattern.endswith('/**'):
                            prefix = pattern[:-3]
                            if rel_path.startswith(prefix + '/') or rel_path == prefix:
                                match = True
                                break
                        # **/file -> 모든 하위의 file
                        elif pattern.startswith('**/'):
                            tail = pattern[3:]
                            if fnmatch.fnmatch(rel_path, '*/' + tail) or fnmatch.fnmatch(rel_path, tail):
                                match = True
                                break

                if match:
                    sources_filtered_files.append(file_name)

            files = sources_filtered_files

            # 4. SOURCES 패턴으로 디렉토리 필터링 (하위에 매칭되는 패턴이 있는지)
            sources_valid_dirs = set()
            for dir_name in directories:
                # 현재 경로 기준 상대 경로
                if current_path:
                    dir_path = os.path.join(current_path, dir_name)
                else:
                    dir_path = dir_name

                # SOURCES 패턴에 이 디렉토리 하위가 포함되는지 체크
                has_match = False
                for pattern in source_patterns:
                    # AAA/** 패턴이면 AAA와 그 하위 모두 매칭
                    if pattern.endswith('/**'):
                        prefix = pattern[:-3]
                        if dir_path.startswith(prefix + '/') or dir_path == prefix:
                            has_match = True
                            break
                    # AAA/sub/file 같은 패턴이면 AAA, AAA/sub 디렉토리 모두 표시
                    if '/' in pattern:
                        pattern_parts = pattern.split('/')
                        dir_parts = dir_path.split('/')
                        # 패턴의 일부가 현재 디렉토리 경로와 매칭되는지 체크
                        if len(dir_parts) < len(pattern_parts):
                            # 패턴의 앞부분이 현재 디렉토리와 일치하면 표시
                            if pattern_parts[:len(dir_parts)] == dir_parts:
                                has_match = True
                                break

                if has_match:
                    sources_valid_dirs.add(dir_name)

            directories = sources_valid_dirs

            # 5. Git tracked files는 Background에서 로딩만 (필터링에는 사용 안 함)
            # SOURCES 패턴으로 필터링된 파일 + 파일시스템에 있는 파일 모두 표시
            # 이유: Download 후 Work에 파일이 복사되었지만 아직 Git commit 안 된 경우에도 표시
            if async_mode:
                # Background에서 git ls-files 로딩 시작 (UI는 blocking 안 됨)
                self.get_git_tracked_files(base_dir, async_mode=True)
            else:
                # 동기 모드: Git tracked files 로딩 (상태 표시용, 필터링에는 미사용)
                self.get_git_tracked_files(base_dir, async_mode=False)

            return directories, files

        except Exception as e:
            self.add_log(f"Directory scan error: {e}", "ERROR")
            return set(), []

    def get_state_symbol(self, state) -> str:
        """파일 상태 심볼 반환 (파일 목록용)"""
        state_map = {
            FileState.MODIFIED: "M",
            FileState.SAME: "S",
            FileState.UPDATED: "U",
            FileState.CONFLICTED: "C",
            FileState.PENDING: " ",  # PENDING 상태 - 공백으로 표시
        }
        return state_map.get(state, "?")

    def get_state_color(self, state) -> int:
        """파일 상태별 색상 반환"""
        colors = getattr(self, 'colors', {})
        color_map = {
            FileState.MODIFIED: colors.get('modified', curses.A_NORMAL),
            FileState.SAME: colors.get('same', curses.A_NORMAL),
            FileState.UPDATED: colors.get('updated', curses.A_NORMAL),
            FileState.CONFLICTED: colors.get('conflicted', curses.A_REVERSE),
            FileState.PENDING: colors.get('pending', curses.A_DIM),  # PENDING 상태 - 회색(DEBUG 색상)
        }
        return color_map.get(state, colors.get('default', curses.A_NORMAL))

    def get_log_color(self, level: str) -> int:
        """로그 레벨별 색상 반환"""
        colors = getattr(self, 'colors', {})
        color_map = {
            'INFO': colors.get('log_info', curses.A_NORMAL),
            'DEBUG': colors.get('log_debug', curses.A_DIM),
            'ERROR': colors.get('log_error', curses.A_BOLD),
            'WARNING': colors.get('log_warning', curses.A_BOLD),
            'WARN': colors.get('log_warning', curses.A_BOLD),
            'HIGH': colors.get('log_high', curses.A_BOLD),  # HIGH 레벨 추가 (MAGENTA 색상)
            'LOG': colors.get('log_default', curses.A_NORMAL),
        }
        return color_map.get(level, colors.get('log_default', curses.A_NORMAL))

    def extract_log_level(self, log_message: str) -> str:
        """로그 메시지에서 레벨 추출"""
        if not log_message:
            return "LOG"

        # 로그 형식: "LEVEL: message" 또는 "timestamp LEVEL: message" 또는 "[LEVEL] message"
        if "[INFO ]" in log_message or "INFO:" in log_message:
            return "INFO"
        elif "[DEBUG]" in log_message or "DEBUG:" in log_message:
            return "DEBUG"
        elif "[ERROR]" in log_message or "ERROR:" in log_message:
            return "ERROR"
        elif "[WARN ]" in log_message or "WARNING:" in log_message:
            return "WARN"
        elif "[HIGH ]" in log_message or "HIGH:" in log_message:
            return "HIGH"
        else:
            return "LOG"

    def format_log_message(self, log_message: str):
        """로그 메시지 포맷 통일 (키워드 대괄호화)"""
        if not log_message:
            return log_message

        # 키워드 찾기 및 포맷 변경 (길이 통일을 위한 대괄호 형태)
        keyword_mapping = {
            "INFO:": "[INFO ]",
            "DEBUG:": "[DEBUG]",
            "ERROR:": "[ERROR]",
            "HIGH:": "[HIGH ]"
        }

        for original_keyword, formatted in keyword_mapping.items():
            if original_keyword in log_message:
                return log_message.replace(original_keyword, formatted)

        return log_message

    def render_log_with_colored_keyword(self, stdscr, row, col, log_message: str, max_width: int):
        """로그 메시지에서 키워드만 색상을 적용하여 출력 (길이 통일)"""
        if not log_message:
            return

        # 먼저 포맷팅 적용
        formatted_message = self.format_log_message(log_message)

        # 키워드 찾기 및 색상 적용 (이미 포맷된 키워드를 찾음)
        keyword_mapping = {
            "[INFO ]": "[INFO ]",
            "[DEBUG]": "[DEBUG]",
            "[ERROR]": "[ERROR]",
            "[WARN ]": "[WARN ]",
            "[HIGH ]": "[HIGH ]"
        }

        keyword_found = None
        keyword_pos = -1
        formatted_keyword = None

        for original_keyword, formatted in keyword_mapping.items():
            pos = formatted_message.find(original_keyword)
            if pos != -1:
                keyword_found = original_keyword
                keyword_pos = pos
                formatted_keyword = formatted
                break

        try:
            if keyword_found and keyword_pos != -1:
                # 키워드 이전 부분
                before_keyword = formatted_message[:keyword_pos]
                # 키워드 이후 부분 (포맷된 키워드 길이만큼 건너뛰기)
                after_keyword = formatted_message[keyword_pos + len(keyword_found):]

                # 전체 길이 체크 및 자르기 (포맷된 키워드 길이로 계산)
                total_text = before_keyword + formatted_keyword + after_keyword
                if len(total_text) > max_width:
                    # 길이 초과시 뒤에서부터 자르기
                    truncate_length = len(total_text) - max_width + 3  # "..." 공간
                    if len(after_keyword) > truncate_length:
                        after_keyword = after_keyword[:-truncate_length] + "..."
                    else:
                        # after_keyword가 부족하면 before_keyword에서도 자르기
                        remaining = truncate_length - len(after_keyword)
                        if remaining > 0 and remaining < len(before_keyword):
                            before_keyword = before_keyword[:-remaining]
                        after_keyword = "..." if len(after_keyword) > 0 else ""

                current_col = col

                # 키워드 이전 부분 출력 (기본 색상)
                if before_keyword:
                    safe_before = before_keyword.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                    stdscr.addstr(row, current_col, safe_before)
                    current_col += len(before_keyword)

                # 포맷된 키워드 부분 출력 (색상 적용)
                keyword_level = keyword_found.replace("[", "").replace("]", "").strip()
                keyword_color = self.get_log_color(keyword_level)
                safe_keyword = formatted_keyword.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                stdscr.addstr(row, current_col, safe_keyword, keyword_color)
                current_col += len(formatted_keyword)

                # 키워드 이후 부분 출력 (기본 색상)
                if after_keyword:
                    safe_after = after_keyword.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                    stdscr.addstr(row, current_col, safe_after)
            else:
                # 키워드가 없는 경우 전체를 기본 색상으로 출력 (포맷팅 적용)
                display_text = formatted_message
                if len(display_text) > max_width:
                    display_text = display_text[:max_width-3] + "..."
                safe_text = display_text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                stdscr.addstr(row, col, safe_text)

        except curses.error:
            # 실패시 ASCII 변환으로 fallback
            try:
                ascii_text = ''.join(c if ord(c) < 128 else '?' for c in log_message)
                if len(ascii_text) > max_width:
                    ascii_text = ascii_text[:max_width-3] + "..."
                stdscr.addstr(row, col, ascii_text)
            except curses.error:
                pass

    def format_size(self, size: int) -> str:
        """파일 크기 포맷팅 (B, K, M, G 단위)"""
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size/1024:.0f}K"
        elif size < 1024 * 1024 * 1024:
            return f"{size/(1024*1024):.0f}M"
        else:
            return f"{size/(1024*1024*1024):.1f}G"

    def build_directory_view(self):
        """현재 디렉토리의 항목들 구성 (Partial Refresh - 현재 디렉토리만)"""
        try:
            # 기본 디렉토리 설정
            if self.mode == ViewMode.WORK:
                base_dir = self.workspace.working_dir
            else:
                base_dir = self.workspace.production_dir

            # 현재 디렉토리의 항목들만 가져오기 (async 모드로 빠르게 반환)
            current_path = self.current_directory
            directories, files = self.get_current_directory_items(base_dir, current_path, async_mode=True)

            # 항목 목록 구성
            self.directory_entries = []

            # 상위 디렉토리 항목 추가 (루트가 아닌 경우)
            if self.current_directory:
                parent_path = self.get_parent_directory(self.current_directory)
                self.directory_entries.append({
                    'type': 'parent',
                    'name': '..',
                    'path': parent_path
                })

            # 디렉토리들 추가 (정렬)
            for dir_name in sorted(directories):
                dir_path = os.path.join(self.current_directory, dir_name) if self.current_directory else dir_name
                self.directory_entries.append({
                    'type': 'directory',
                    'name': dir_name + "/",
                    'path': dir_path
                })

            # 파일들 추가 (정렬)
            for file_name in sorted(files):
                file_path = os.path.join(self.current_directory, file_name) if self.current_directory else file_name
                full_path = os.path.join(base_dir, file_path)

                # 파일 정보 가져오기
                try:
                    size = os.path.getsize(full_path) if os.path.exists(full_path) else 0

                    # 현재 파일의 mtime 가져오기
                    current_mtime = os.path.getmtime(full_path) if os.path.exists(full_path) else 0

                    # Cache에서 상태 조회 (mtime 전달)
                    cached_state = self.get_cached_state(file_path, current_mtime)
                    if cached_state is not None:
                        # 캐시 히트 - 캐시 값 사용
                        state = cached_state
                    else:
                        # 캐시 미스 - PENDING 상태로 설정하고 thread로 처리
                        state = FileState.PENDING
                        # Thread로 상태 확인 요청
                        self.request_state_check(file_path, full_path)

                except Exception:
                    size = 0
                    state = FileState.SAME

                self.directory_entries.append({
                    'type': 'file',
                    'name': file_name,
                    'path': file_path,
                    'size': size,
                    'state': state
                })

            # 선택 인덱스 조정
            if self.selected_index >= len(self.directory_entries):
                self.selected_index = max(0, len(self.directory_entries) - 1)

            self.add_log(f"Directory view: {len(self.directory_entries)} items", "DEBUG")

        except Exception as e:
            self.add_log(f"Directory view build failed: {e}", "ERROR")
            self.directory_entries = []

    def build_tree_view(self):
        """트리 뷰 모드를 위한 항목 목록 구성 (모든 파일과 디렉토리 표시)"""
        try:
            # 기본 디렉토리 설정
            if self.mode == ViewMode.WORK:
                base_dir = self.workspace.working_dir
            else:
                base_dir = self.workspace.production_dir

            # 재귀적으로 모든 파일과 디렉토리 탐색
            self.directory_entries = []
            self._build_tree_recursive(base_dir, "", 0)

            # 선택 인덱스 조정
            if self.selected_index >= len(self.directory_entries):
                self.selected_index = max(0, len(self.directory_entries) - 1)

            self.add_log(f"Tree view: {len(self.directory_entries)} items", "DEBUG")

        except Exception as e:
            self.add_log(f"Tree view build failed: {e}", "ERROR")
            self.directory_entries = []

    def _build_tree_recursive(self, base_dir: str, rel_path: str, depth: int):
        """재귀적으로 트리 항목 구성

        Args:
            base_dir: 기본 디렉토리 (절대 경로)
            rel_path: 상대 경로
            depth: 현재 깊이 (들여쓰기 레벨)
        """
        import fnmatch

        current_full_path = os.path.join(base_dir, rel_path) if rel_path else base_dir

        # 디렉토리가 아니면 종료
        if not os.path.isdir(current_full_path):
            return

        try:
            # 현재 디렉토리의 항목들 가져오기
            items = os.listdir(current_full_path)

            # Exclude 패턴 가져오기
            exclude_patterns = self.workspace.get_exclude_patterns()
            source_patterns = self.workspace.get_source_patterns()

            # 디렉토리와 파일 분류
            directories = []
            files = []

            for item in items:
                # .git, .cccopy 제외
                if item in ['.git', '.cccopy']:
                    continue

                item_path = os.path.join(current_full_path, item)
                if os.path.isdir(item_path):
                    directories.append(item)
                else:
                    files.append(item)

            # EXCLUDES 패턴으로 디렉토리 필터링
            filtered_dirs = []
            for dir_name in directories:
                # 현재 경로 기준 상대 경로
                if rel_path:
                    dir_rel_path = os.path.join(rel_path, dir_name)
                else:
                    dir_rel_path = dir_name

                # EXCLUDES 패턴 체크
                exclude = False
                for exclude_pattern in exclude_patterns:
                    # 디렉토리 패턴 매칭
                    if fnmatch.fnmatch(dir_rel_path, exclude_pattern.rstrip('/')):
                        exclude = True
                        break
                    # 디렉토리 이름만으로도 체크
                    if fnmatch.fnmatch(dir_name, exclude_pattern.rstrip('/')):
                        exclude = True
                        break
                    # **/ 패턴 처리
                    if exclude_pattern.startswith('**/'):
                        pattern_tail = exclude_pattern[3:].rstrip('/')
                        if fnmatch.fnmatch(dir_name, pattern_tail):
                            exclude = True
                            break

                if not exclude:
                    filtered_dirs.append(dir_name)

            directories = filtered_dirs

            # SOURCES 패턴으로 디렉토리 필터링 (루트 디렉토리일 때만)
            if not rel_path:  # 루트 디렉토리인 경우
                valid_dirs = set()
                for pattern in source_patterns:
                    # 패턴에서 첫 번째 디렉토리 추출
                    # 예: "AAA/**" -> "AAA", "BBB/*" -> "BBB"
                    parts = pattern.split('/')
                    if parts and parts[0] and not parts[0].startswith('*'):
                        valid_dirs.add(parts[0])

                # SOURCES에 정의된 디렉토리만 표시
                directories = [d for d in directories if d in valid_dirs]

            # EXCLUDES 패턴으로 파일 필터링
            filtered_files = []
            for file_name in files:
                # 현재 경로 기준 상대 경로
                if rel_path:
                    file_rel_path = os.path.join(rel_path, file_name)
                else:
                    file_rel_path = file_name

                # EXCLUDES 패턴 체크
                exclude = False
                for exclude_pattern in exclude_patterns:
                    # 파일 패턴 매칭
                    if fnmatch.fnmatch(file_rel_path, exclude_pattern):
                        exclude = True
                        break
                    # 파일 이름만으로도 체크
                    if fnmatch.fnmatch(file_name, exclude_pattern):
                        exclude = True
                        break
                    # **/ 패턴 처리
                    if exclude_pattern.startswith('**/'):
                        pattern_tail = exclude_pattern[3:]
                        if fnmatch.fnmatch(file_name, pattern_tail):
                            exclude = True
                            break

                if not exclude:
                    filtered_files.append(file_name)

            files = filtered_files

            # SOURCES 패턴으로 파일 필터링 (depth=0일 때만)
            if depth == 0:
                sources_filtered_files = []
                for file_name in files:
                    file_rel_path = file_name  # depth=0이므로 루트

                    # SOURCES 패턴 체크
                    match = False
                    for pattern in source_patterns:
                        if fnmatch.fnmatch(file_rel_path, pattern):
                            match = True
                            break
                        # **/ 패턴 처리
                        if pattern.startswith('**/'):
                            pattern_tail = pattern[3:]
                            if fnmatch.fnmatch(file_name, pattern_tail):
                                match = True
                                break

                    if match:
                        sources_filtered_files.append(file_name)

                files = sources_filtered_files

            # 정렬
            directories.sort()
            files.sort()

            # 디렉토리 먼저 추가
            for dir_name in directories:
                dir_rel_path = os.path.join(rel_path, dir_name) if rel_path else dir_name
                is_expanded = dir_rel_path in self.tree_expanded_dirs

                # 디렉토리 항목 추가
                self.directory_entries.append({
                    'type': 'tree_directory',
                    'name': dir_name,
                    'path': dir_rel_path,
                    'depth': depth,
                    'expanded': is_expanded
                })

                # 펼쳐져 있으면 하위 항목도 추가
                if is_expanded:
                    self._build_tree_recursive(base_dir, dir_rel_path, depth + 1)

            # 파일들 추가 (항상 현재 레벨의 파일 표시)
            for file_name in files:
                file_rel_path = os.path.join(rel_path, file_name) if rel_path else file_name
                full_path = os.path.join(base_dir, file_rel_path)

                try:
                    size = os.path.getsize(full_path) if os.path.exists(full_path) else 0
                    current_mtime = os.path.getmtime(full_path) if os.path.exists(full_path) else 0

                    # Cache에서 상태 조회
                    cached_state = self.get_cached_state(file_rel_path, current_mtime)
                    if cached_state is not None:
                        state = cached_state
                    else:
                        state = FileState.PENDING
                        self.request_state_check(file_rel_path, full_path)
                except Exception:
                    size = 0
                    state = FileState.SAME

                self.directory_entries.append({
                    'type': 'tree_file',
                    'name': file_name,
                    'path': file_rel_path,
                    'depth': depth,
                    'size': size,
                    'state': state
                })

        except PermissionError:
            self.add_log(f"Permission denied: {current_full_path}", "WARNING")
        except Exception as e:
            self.add_log(f"Error reading directory {current_full_path}: {e}", "ERROR")

    def get_parent_directory(self, path: str) -> str:
        """상위 디렉토리 경로 반환"""
        if not path:
            return ""
        parts = path.split("/")
        if len(parts) <= 1:
            return ""
        return "/".join(parts[:-1])

    def draw_header(self, stdscr):
        """헤더 영역 그리기"""
        _, width = stdscr.getmaxyx()

        # TAG 정보 가져오기
        current_tag = self.get_current_project_tag()

        # 뷰 스타일 정보 추가
        view_style_text = self.view_style.value  # "detail" 또는 "tree"

        # TAG가 있으면 모드 다음에 추가, 없으면 기존과 동일
        if current_tag:
            header_text = f"CCCopy v{CCCOPY_VERSION} | [M]ode: {self.mode.value} | View: {view_style_text} | {current_tag}"
        else:
            header_text = f"CCCopy v{CCCOPY_VERSION} | [M]ode: {self.mode.value} | View: {view_style_text}"

        # 헤더 그리기 (Unicode 박스 문자 사용)
        try:
            stdscr.addstr(0, 0, "┌" + "─" * (width - 2) + "┐")
            stdscr.addstr(1, 0, "│")
            if len(header_text) <= width-3:
                self._draw_commands_with_color(stdscr, 1, 1, header_text, width-3)
            else:
                truncated = header_text[:width-6] + "..."
                self._draw_commands_with_color(stdscr, 1, 1, truncated, width-3)
            stdscr.addstr(1, width-1, "│")
            stdscr.addstr(2, 0, "├" + "─" * (width - 2) + "┤")
        except curses.error:
            # 안전한 대안
            stdscr.addstr(0, 0, "CCCopy TUI")
            stdscr.addstr(1, 0, f"Mode: {self.mode.value}")

    def draw_path(self, stdscr):
        """경로 영역 그리기"""
        height, width = stdscr.getmaxyx()

        if self.mode == ViewMode.WORK:
            base_path = self.workspace.working_dir
        else:
            base_path = self.workspace.production_dir

        # 현재 디렉토리 경로 구성
        if self.view_style == ViewStyle.TREE:
            # 트리 뷰 모드에서는 전체 경로만 표시
            current_path = base_path
        else:
            # Detail 모드에서는 현재 디렉토리 경로 표시
            if self.current_directory:
                current_path = f"{base_path}/{self.current_directory}"
            else:
                current_path = base_path

        # [T]erminal | [V]iew 메뉴 추가
        path_text = f"{current_path} | [T]erminal | [V]iew"
        available_width = width - 5
        if len(path_text) > available_width:
            path_text = path_text[:available_width-3] + "..."

        try:
            stdscr.addstr(3, 0, "│")
            # [T]erminal도 색상 처리
            self._draw_commands_with_color(stdscr, 3, 1, path_text, width-3)
            stdscr.addstr(3, width-1, "│")
            stdscr.addstr(4, 0, "├" + "─" * (width - 2) + "┤")
        except curses.error:
            # 안전한 대안
            stdscr.addstr(3, 0, current_path[:width-1])

    def draw_file_list(self, stdscr):
        """파일 리스트 영역 그리기"""
        height, width = stdscr.getmaxyx()
        start_row = 5
        end_row = height - 11  # 명령어 영역을 위한 공간 확보
        list_height = end_row - start_row

        # 스크롤 조정
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + list_height:
            self.scroll_offset = self.selected_index - list_height + 1

        # 파일 리스트 그리기
        for i in range(list_height):
            row = start_row + i
            entry_index = self.scroll_offset + i

            try:
                stdscr.addstr(row, 0, "│")
                stdscr.addstr(row, width-1, "│")

                if entry_index < len(self.directory_entries):
                    entry = self.directory_entries[entry_index]

                    # 트리 뷰 모드인지 확인
                    is_tree_mode = (self.view_style == ViewStyle.TREE)

                    # 항목 타입별 표시
                    if entry['type'] == 'parent':
                        name_text = ".."
                        suffix_text = ""
                        color = getattr(self, 'colors', {}).get('folder', curses.A_BOLD)
                        indent = ""
                    elif entry['type'] == 'directory':
                        name_text = entry['name']
                        suffix_text = ""
                        color = getattr(self, 'colors', {}).get('folder', curses.A_BOLD)
                        indent = ""
                    elif entry['type'] == 'tree_directory':
                        # 트리 뷰 디렉토리
                        depth = entry.get('depth', 0)
                        is_expanded = entry.get('expanded', False)
                        expand_symbol = "- " if is_expanded else "+ "
                        indent = "  " * depth  # 2칸 들여쓰기
                        name_text = expand_symbol + entry['name']
                        suffix_text = ""
                        color = getattr(self, 'colors', {}).get('folder', curses.A_BOLD)
                    elif entry['type'] == 'tree_file':
                        # 트리 뷰 파일
                        depth = entry.get('depth', 0)
                        indent = "  " * depth  # 2칸 들여쓰기
                        state_symbol = self.get_state_symbol(entry['state'])
                        size_text = self.format_size(entry['size'])
                        name_text = entry['name']
                        suffix_text = f"{size_text:>6} [{state_symbol}]"
                        color = self.get_state_color(entry['state'])
                    else:  # file (detail mode)
                        state_symbol = self.get_state_symbol(entry['state'])
                        size_text = self.format_size(entry['size'])
                        name_text = entry['name']
                        suffix_text = f"{size_text:>6} [{state_symbol}]"
                        color = self.get_state_color(entry['state'])
                        indent = ""

                    # 가용 너비 계산 (│ > indent name ... suffix [S] │)
                    available_width = width - 4  # 양쪽 │과 선택 표시(> or ' ') 제외
                    indent_width = self.get_display_width(indent)
                    suffix_width = self.get_display_width(suffix_text)
                    max_name_width = available_width - indent_width - suffix_width

                    # 파일명이 너무 길면 잘라내기 (한글 폭 고려)
                    name_display_width = self.get_display_width(name_text)
                    if name_display_width > max_name_width:
                        name_text = self.truncate_text(name_text, max_name_width - 3) + "..."

                    # 선택된 항목 하이라이트
                    if entry_index == self.selected_index:
                        selected_color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                        stdscr.addstr(row, 1, ">")
                        # 들여쓰기 + 파일명 출력
                        full_text = indent + name_text
                        self.safe_addstr(stdscr, row, 3, full_text, selected_color)
                        # 크기와 상태를 오른쪽 끝에 출력 (한글 폭 고려)
                        if suffix_text:
                            suffix_col = width - 1 - self.get_display_width(suffix_text) - 1
                            self.safe_addstr(stdscr, row, suffix_col, suffix_text, selected_color)
                    else:
                        stdscr.addstr(row, 1, " ")  # 선택 표시 자리에 공백
                        # 들여쓰기 + 파일명 출력
                        full_text = indent + name_text
                        self.safe_addstr(stdscr, row, 3, full_text, color)
                        # 크기와 상태를 오른쪽 끝에 출력 (한글 폭 고려)
                        if suffix_text:
                            suffix_col = width - 1 - self.get_display_width(suffix_text) - 1
                            self.safe_addstr(stdscr, row, suffix_col, suffix_text, color)
                else:
                    # 빈 줄
                    stdscr.addstr(row, 1, " " * min(width - 2, 50))
            except curses.error:
                # 안전한 대안 - 간단한 텍스트만 (동일한 정렬)
                try:
                    if entry_index < len(self.directory_entries):
                        entry = self.directory_entries[entry_index]
                        simple_text = f"{entry['name']}"[:width-7]
                        if entry_index == self.selected_index:
                            stdscr.addstr(row, 1, ">")
                            stdscr.addstr(row, 3, simple_text)
                        else:
                            stdscr.addstr(row, 1, " ")
                            stdscr.addstr(row, 3, simple_text)
                except curses.error:
                    pass  # 완전히 실패한 경우 건너뛰기

    def draw_commands(self, stdscr):
        """명령어 영역 그리기"""
        height, width = stdscr.getmaxyx()
        row = height - 11  # 8줄 로그 영역 + 명령어 영역(3줄) = 11줄

        try:
            # 구분선 그리기 (튜토리얼 활성화시 V 포함)
            separator = "├" + "─" * (width - 2) + "┤"

            # 튜토리얼이 활성화되어 있으면 해당 키 위치에 V 삽입
            # key가 None인 경우(요약 화면)는 V를 그리지 않음
            if self.tutorial_enabled and self.tutorial_step < len(self.tutorial_steps):
                step = self.tutorial_steps[self.tutorial_step]
                if step['key'] is not None:
                    commands = "[D]ownload [U]pload [S]ave [H]istory [P]roject [L]ogs [A]pps [F2]Help [Q]uit"
                    key_pattern = f"[{step['key']}]"
                    key_index = commands.find(key_pattern)

                    if key_index != -1:
                        # 명령어는 "│" 다음 1열부터 시작하므로 key_index + 1
                        # [D]의 가운데 D 위치는 key_index + 1
                        v_position = 1 + key_index + 1  # 1(│ 다음) + key_index + 1([D]의 D)

                        if 0 < v_position < len(separator) - 1:
                            separator = separator[:v_position] + "V" + separator[v_position + 1:]

            stdscr.addstr(row, 0, separator)
            commands = "[D]ownload [U]pload [S]ave [H]istory [P]roject [L]ogs [A]pps [F2]Help [Q]uit"
            stdscr.addstr(row + 1, 0, "│")
            self._draw_commands_with_color(stdscr, row + 1, 1, commands, width - 3)
            stdscr.addstr(row + 1, width-1, "│")
        except curses.error:
            # 안전한 대안
            try:
                stdscr.addstr(row, 0, "Commands: D/U/S/H/L/Q")
            except curses.error:
                pass

    def _draw_commands_with_color(self, stdscr, row, col, commands, max_width):
        """명령어 문자열에서 [X] 부분을 노란색으로 표시"""
        try:
            current_col = col
            remaining_width = max_width
            i = 0

            while i < len(commands) and remaining_width > 0:
                if commands[i] == '[':
                    # [ 부터 ] 까지 찾기
                    end_bracket = commands.find(']', i)
                    if end_bracket != -1 and end_bracket - i <= remaining_width:
                        # [X] 전체를 노란색으로 표시
                        bracket_text = commands[i:end_bracket+1]
                        if len(bracket_text) <= remaining_width:
                            stdscr.addstr(row, current_col, bracket_text, curses.color_pair(2))  # 노란색
                            current_col += len(bracket_text)
                            remaining_width -= len(bracket_text)
                            i = end_bracket + 1
                        else:
                            break
                    else:
                        # ] 를 찾을 수 없으면 그냥 일반 문자로 처리
                        stdscr.addstr(row, current_col, commands[i])
                        current_col += 1
                        remaining_width -= 1
                        i += 1
                else:
                    # 일반 문자는 기본 색상으로 표시
                    stdscr.addstr(row, current_col, commands[i])
                    current_col += 1
                    remaining_width -= 1
                    i += 1
        except curses.error:
            # 에러 발생시 안전하게 처리
            try:
                safe_commands = commands[:max_width]
                stdscr.addstr(row, col, safe_commands)
            except curses.error:
                pass

    def draw_tutorial(self, stdscr):
        """튜토리얼 오버레이 그리기"""
        if not self.tutorial_enabled or self.tutorial_step >= len(self.tutorial_steps):
            return

        height, width = stdscr.getmaxyx()
        step = self.tutorial_steps[self.tutorial_step]

        # 메시지 준비 (줄바꿈 처리)
        message_lines = step['message'].split('\n')

        # 메시지 박스 폭 계산 (가장 긴 줄 기준)
        max_msg_width = max(self.get_display_width(line) for line in message_lines)
        box_width = max_msg_width + 4  # 양쪽 "│ " 공간

        # key가 None이면 중앙 배치 (화살표 없음)
        if step['key'] is None:
            # 화면 폭 제한
            if box_width > width - 4:
                box_width = width - 4
                max_msg_width = box_width - 4

            # 메시지 박스 크기 계산
            box_lines = len(message_lines) + 2  # 상단/하단 테두리

            # 화면 중앙에 배치
            box_top_row = (height - box_lines) // 2
            arrow_col = (width - box_width) // 2

            # 화살표 라인 수는 0
            arrow_lines = 0
        else:
            # 'T' 키는 경로 라인(row 3)에 있고, 다른 키들은 명령어 영역에 있음
            if step['key'] == 'T':
                # [T]erminal은 경로 라인에 있음 (row 3)
                # 경로 텍스트 재구성
                if self.mode == ViewMode.WORK:
                    base_path = self.workspace.working_dir
                else:
                    base_path = self.workspace.production_dir

                if self.current_directory:
                    current_path = f"{base_path}/{self.current_directory}"
                else:
                    current_path = base_path

                path_text = f"{current_path} | [T]erminal"
                key_pattern = "[T]"
                key_index = path_text.find(key_pattern)

                if key_index == -1:
                    return  # 키를 찾지 못하면 렌더링 안 함

                # [T]의 'T' 위치 계산 (경로는 "│" 다음 1열부터 시작)
                # [T]의 T는 key_index + 1 위치
                arrow_col = 1 + key_index + 1

                # 화면 폭 제한
                if box_width > width - arrow_col - 2:
                    box_width = width - arrow_col - 2
                    max_msg_width = box_width - 4

                # 메시지 박스 시작 위치 계산
                # 경로 라인은 row 3이고, row 4는 구분선(├───┤)
                # 화살표: row 4에 "^", row 5에 "│"
                # 박스는 row 6부터 시작
                path_row = 3
                path_separator_row = 4
                box_lines = len(message_lines) + 2  # 상단/하단 테두리
                arrow_lines = 2  # "^" (row 4) + "│" (row 5)
                total_lines = box_lines + arrow_lines

                # 화살표 시작 위치
                box_top_row = path_separator_row + arrow_lines
            else:
                # 명령어 영역 계산
                # draw_commands에서: row = height - 11 (테두리 줄)
                # 명령어 텍스트는 row + 1 = height - 10
                command_separator_row = height - 11  # "├───┤" 줄
                command_text_row = height - 10       # 명령어 텍스트 줄

                # 명령어 문자열에서 해당 키의 위치를 찾습니다
                commands = "[D]ownload [U]pload [S]ave [H]istory [P]roject [L]ogs [F2]Help [Q]uit"
                key_pattern = f"[{step['key']}]"
                key_index = commands.find(key_pattern)

                if key_index == -1:
                    return  # 키를 찾지 못하면 렌더링 안 함

                # 화살표와 메시지를 표시할 위치 계산
                # 명령어는 "│" 다음 1열부터 시작하므로 key_index + 1
                arrow_col = 1 + key_index

                # 화면 폭 제한
                if box_width > width - arrow_col - 2:
                    box_width = width - arrow_col - 2
                    max_msg_width = box_width - 4

                # 메시지 박스 시작 위치 계산
                # 박스는 (상단 테두리, 메시지 줄들, 하단 테두리) + 화살표 1줄 (│만, V는 구분선에 통합)
                box_lines = len(message_lines) + 2  # 상단/하단 테두리
                arrow_lines = 1  # "│" 만 (V는 draw_commands에서 구분선에 표시)
                total_lines = box_lines + arrow_lines

                # 명령어 영역 위에 배치
                box_top_row = command_separator_row - total_lines

        try:
            # 배경색을 녹색으로 설정 (curses.color_pair(16) = 녹색 배경 흰색 글자)
            # 색상이 없다면 일반 텍스트로 표시
            try:
                color_attr = curses.color_pair(16) | curses.A_BOLD
            except:
                color_attr = curses.A_BOLD

            current_row = box_top_row

            # 상단 테두리 그리기 (왼쪽에 "튜토리얼(N/6)" 추가)
            if current_row >= 0 and current_row < height:
                title_text = f" 튜토리얼({self.tutorial_step + 1}/{len(self.tutorial_steps)}) "
                title_width = self.get_display_width(title_text)

                # 상단 테두리: ┌─ 튜토리얼(N/6) ──────┐
                if box_width > title_width + 3:
                    # 충분한 공간이 있는 경우
                    right_width = box_width - title_width - 3  # ┌, ─, ┐ 제외
                    top_border = "┌─" + title_text + "─" * right_width + "┐"
                else:
                    # 공간이 부족한 경우 기본 테두리
                    top_border = "┌" + "─" * (box_width - 2) + "┐"

                stdscr.addstr(current_row, arrow_col, top_border, color_attr)
            current_row += 1

            # 메시지 줄들 그리기
            for line in message_lines:
                if current_row >= 0 and current_row < height:
                    # 한글 안전 패딩
                    display_width = self.get_display_width(line)
                    if display_width > max_msg_width:
                        line = self.truncate_text(line, max_msg_width)
                        display_width = self.get_display_width(line)

                    # 좌측 정렬로 패딩
                    padding = max_msg_width - display_width
                    padded_line = f"│ {line}{' ' * padding} │"
                    stdscr.addstr(current_row, arrow_col, padded_line, color_attr)
                current_row += 1

            # 하단 테두리 그리기 (우측에 "← Prev | Next →" 또는 "← Prev | Next → | Enter: 더이상 안보기" 추가)
            if current_row >= 0 and current_row < height:
                # 마지막 단계인지 확인
                is_last_step = (self.tutorial_step == len(self.tutorial_steps) - 1)

                if is_last_step:
                    hint_text = " ← Prev | Next → | Enter: 더이상 안보기 "
                    # 한글 폭 고려
                    hint_width = self.get_display_width(hint_text)
                else:
                    hint_text = " ← Prev | Next → "
                    hint_width = len(hint_text)  # ASCII만이므로 len 사용

                # 하단 테두리: └──────── hint_text ─┘
                if box_width > hint_width + 3:
                    # 충분한 공간이 있는 경우
                    left_width = box_width - hint_width - 3  # └, ─, ┘ 제외
                    bottom_border = "└" + "─" * left_width + hint_text + "─┘"
                else:
                    # 공간이 부족한 경우 기본 테두리
                    bottom_border = "└" + "─" * (box_width - 2) + "┘"

                stdscr.addstr(current_row, arrow_col, bottom_border, color_attr)
            current_row += 1

            # 화살표 표시 - 색상 없음
            # key가 None이 아닐 때만 화살표 표시
            if step['key'] is not None:
                if step['key'] == 'T':
                    # 'T' 키는 위에서 아래로 향하는 화살표
                    # row 4 (path_separator_row): "^"
                    # row 5: "│"
                    path_separator_row = 4
                    try:
                        stdscr.addstr(path_separator_row, arrow_col, "^")
                        stdscr.addstr(path_separator_row + 1, arrow_col, "│")
                    except curses.error:
                        pass
                else:
                    # 다른 키들은 아래에서 위로 향하는 화살표
                    # 박스 바로 아래에 "│" 표시 (V는 구분선에 표시됨)
                    if current_row >= 0 and current_row < height:
                        stdscr.addstr(current_row, arrow_col + 1, "│")

        except curses.error:
            # 화면 크기가 작아서 렌더링 실패시 무시
            pass

    def start_tutorial(self, force=False):
        """튜토리얼 시작 (처음부터)

        Args:
            force: True이면 TUTORIAL.STARTUP_SHOW 설정 무시하고 무조건 실행 (F9 키용)
        """
        self.tutorial_enabled = True
        self.tutorial_step = 0
        self.add_log(f"튜토리얼 시작: {self.tutorial_step + 1}/{len(self.tutorial_steps)} 단계", "INFO")
        self.needs_redraw = True

    def draw_logs(self, stdscr):
        """로그 영역 그리기"""
        height, width = stdscr.getmaxyx()
        start_row = height - 8  # 7줄 로그 영역 시작 (하단 테두리 보호를 위해)

        try:
            # 로그 영역 상단 테두리 (명령어 영역과 분리)
            stdscr.addstr(start_row - 1, 0, "├" + "─" * (width - 2) + "┤")

            # 최근 로그 7줄 표시 (하단 테두리 보호)
            recent_logs = self.logs[-7:] if len(self.logs) >= 7 else self.logs
            for i, log in enumerate(recent_logs):
                row = start_row + i
                stdscr.addstr(row, 0, "│")
                # 텍스트 길이 제한 및 화면 클리어 (한글 안전 자르기)
                max_text_width = width - 3
                if log:
                    # 한글 안전 자르기 - 바이트 단위가 아닌 문자 단위로 자르기
                    if len(log) <= max_text_width:
                        display_text = log
                    else:
                        # 안전하게 자르고 ... 추가
                        display_text = log[:max_text_width-3] + "..."
                else:
                    display_text = ""

                # 줄 클리어 최적화 (중복 작업 제거)
                try:
                    stdscr.move(row, 1)
                    stdscr.clrtoeol()  # 커서 위치부터 줄 끝까지 지우기 (한번만)
                except curses.error:
                    pass

                # 새 텍스트 작성 (키워드만 색상 적용)
                if display_text:
                    # 키워드만 색상을 적용하여 출력
                    self.render_log_with_colored_keyword(stdscr, row, 1, display_text, max_text_width)
                stdscr.addstr(row, width-1, "│")

            # 빈 로그 줄 채우기 (7줄까지, 하단 테두리 보호)
            for i in range(len(recent_logs), 7):
                row = start_row + i
                try:
                    stdscr.addstr(row, 0, "│")
                    stdscr.move(row, 1)
                    stdscr.clrtoeol()  # 빈 줄도 clrtoeol로 처리
                    stdscr.addstr(row, width-1, "│")
                except curses.error:
                    pass

            # 하단 테두리
            stdscr.addstr(height - 1, 0, "└" + "─" * (width - 2) + "┘")
        except curses.error:
            # 안전한 대안 - 간단한 로그 표시 (일관된 형식으로)
            try:
                if self.logs:
                    # 최근 로그 몇 개만 간단하게 표시 (하단 테두리 보호)
                    recent_logs = self.logs[-7:] if len(self.logs) >= 7 else self.logs
                    for i, log in enumerate(recent_logs):
                        row = height - 8 + i
                        if row >= 0 and row < height - 1:
                            # 일관된 형식: | + 로그 내용 + | (한글 안전 자르기)
                            if log:
                                if len(log) <= width-3:
                                    display_text = log
                                else:
                                    display_text = log[:width-6] + "..."
                            else:
                                display_text = ""
                            stdscr.addstr(row, 0, "│")
                            # 줄 클리어 최적화
                            try:
                                stdscr.move(row, 1)
                                stdscr.clrtoeol()  # 커서 위치부터 줄 끝까지 지우기
                            except curses.error:
                                pass
                            # 키워드만 색상 적용
                            if display_text:
                                # 키워드만 색상을 적용하여 출력
                                self.render_log_with_colored_keyword(stdscr, row, 1, display_text, width-3)
                            if width > 2:
                                stdscr.addstr(row, width-1, "│")
            except curses.error:
                pass

    def refresh_tree(self, full_refresh=False):
        """Refresh tree and directory view

        Args:
            full_refresh: True면 Full Refresh (thread 종료, cache clear, 동기 처리)
                         False면 Partial Refresh (thread 사용, cache 활용)
        """
        try:
            # Production 자동 커밋 먼저 실행
            # Full Refresh시 force=True (캐시 무시), Partial시 force=False (캐시 활용)
            try:
                self.workspace.auto_commit_production_changes(force=full_refresh)
            except Exception as e:
                self.add_log(f"Production 자동 커밋 실패: {e}", "WARNING")

            # 뷰 스타일에 따라 다른 메서드 호출
            if self.view_style == ViewStyle.TREE:
                # 트리 뷰 모드
                self.add_log("트리 뷰 새로고침 중...", "INFO")
                self.build_tree_view()
            else:
                # Detail 모드
                if full_refresh:
                    self.add_log("전체 파일 목록 새로고침 중...", "INFO")
                    # Full Refresh: thread 종료, cache clear, 동기 처리
                    self.build_directory_view_full()
                else:
                    self.add_log("파일 목록 새로고침 중...", "INFO")
                    # Partial Refresh: thread 사용, cache 활용
                    self.build_directory_view()

            self.selected_index = 0
            self.scroll_offset = 0
            self.add_log(f"{len(self.directory_entries)}개 항목 로드됨", "INFO")
            # 화면 갱신 필요 플래그 설정
            self.needs_redraw = True
        except Exception as e:
            self.add_log(f"Refresh failed: {e}", "ERROR")

    def force_refresh_screen(self):
        """Force complete screen refresh - clears and redraws everything"""
        try:
            if hasattr(self, 'stdscr') and self.stdscr is not None:
                self.add_log("강제 화면 새로고침 시작...", "INFO")
                # 화면 완전 지우기
                self.stdscr.clear()
                # app_viewer_mode일 때는 refresh_tree 호출 안 함
                if not getattr(self, 'app_viewer_mode', False):
                    # Partial Refresh로 빠르게 화면 다시 그리기
                    self.refresh_tree(full_refresh=False)
                # 즉시 화면 업데이트
                self.stdscr.refresh()
                self.needs_redraw = True  # 다음 루프에서 화면 다시 그리기
                self.add_log("화면 새로고침 완료", "INFO")
        except Exception as e:
            self.add_log(f"화면 새로고침 실패: {e}", "ERROR")

    def toggle_mode(self):
        """Toggle Work/Production mode"""
        if self.mode == ViewMode.WORK:
            self.mode = ViewMode.PRODUCTION
        else:
            self.mode = ViewMode.WORK
        self.refresh_tree()
        self.add_log(f"모드 변경됨: {self.mode.value}", "INFO")

    def handle_enter(self):
        """Handle Enter key - navigate directory or show file details"""

        if 0 <= self.selected_index < len(self.directory_entries):
            entry = self.directory_entries[self.selected_index]

            if entry['type'] == 'parent':
                # 상위 디렉토리로 이동
                old_dir = self.current_directory
                self.current_directory = entry['path']
                self.add_log(f"상위 디렉토리로 이동: {old_dir} -> {self.current_directory or 'root'}", "INFO")
                # 디렉토리 변경시 기존 pending 작업들 취소
                self.stop_all_refresh_threads()
                self.build_directory_view()
                self.selected_index = 0  # 선택 인덱스 초기화
                self.needs_redraw = True
                # Watch 디렉토리 변경 알림
                self.notify_directory_changed()

            elif entry['type'] == 'directory':
                # 하위 디렉토리로 이동
                old_dir = self.current_directory
                self.current_directory = entry['path']
                self.add_log(f"디렉토리 진입: {old_dir} -> {self.current_directory}", "INFO")
                # 디렉토리 변경시 기존 pending 작업들 취소
                self.stop_all_refresh_threads()
                self.build_directory_view()
                self.selected_index = 0  # 선택 인덱스 초기화
                self.needs_redraw = True
                # Watch 디렉토리 변경 알림
                self.notify_directory_changed()

            elif entry['type'] == 'tree_directory':
                # 트리 뷰에서 디렉토리 토글 (expand/collapse)
                self.handle_tree_toggle()

            elif entry['type'] == 'file' or entry['type'] == 'tree_file':
                # 파일 상세 정보 표시
                self.add_log(f"File: {entry['path']}", "INFO")
                # PENDING 상태는 (Pending)으로 표시, 나머지는 심볼로 표시
                state_display = "(Pending)" if entry['state'] == FileState.PENDING else self.get_state_symbol(entry['state'])
                self.add_log(f"State: {state_display}, Size: {self.format_size(entry['size'])}", "INFO")
        else:
            self.add_log(f"Invalid selection: index {self.selected_index} out of range", "ERROR")

    def toggle_view_style(self):
        """뷰 스타일 토글 (detail ⟷ tree)"""
        if self.view_style == ViewStyle.DETAIL:
            self.view_style = ViewStyle.TREE
            self.add_log("트리 뷰 모드로 전환", "INFO")
        else:
            self.view_style = ViewStyle.DETAIL
            self.add_log("상세 뷰 모드로 전환", "INFO")

        # 뷰 재구성
        self.refresh_tree()

    def handle_tree_toggle(self):
        """트리 뷰에서 디렉토리 expand/collapse 토글"""
        if 0 <= self.selected_index < len(self.directory_entries):
            entry = self.directory_entries[self.selected_index]

            if entry['type'] == 'tree_directory':
                dir_path = entry['path']
                if dir_path in self.tree_expanded_dirs:
                    # Collapse
                    self.tree_expanded_dirs.remove(dir_path)
                    self.add_log(f"디렉토리 접기: {dir_path}", "DEBUG")
                else:
                    # Expand
                    self.tree_expanded_dirs.add(dir_path)
                    self.add_log(f"디렉토리 펼치기: {dir_path}", "DEBUG")

                # 트리 뷰 재구성
                self.build_tree_view()
                self.needs_redraw = True

    def handle_tree_collapse(self):
        """트리 뷰에서 선택된 항목에 대한 LEFT 키 처리

        - 디렉토리 (expanded) -> Collapse
        - 디렉토리 (collapsed) -> 한 칸 위로 이동
        - 파일 -> 한 칸 위로 이동
        """
        if 0 <= self.selected_index < len(self.directory_entries):
            entry = self.directory_entries[self.selected_index]

            if entry['type'] == 'tree_directory':
                dir_path = entry['path']
                if dir_path in self.tree_expanded_dirs:
                    # Expanded 상태 -> Collapse
                    self.tree_expanded_dirs.remove(dir_path)
                    self.add_log(f"디렉토리 접기: {dir_path}", "DEBUG")
                    self.build_tree_view()
                    self.needs_redraw = True
                else:
                    # 이미 Collapsed 상태 -> 한 칸 위로 이동
                    if self.selected_index > 0:
                        self.selected_index -= 1
                        self.add_log(f"위로 이동", "DEBUG")
            elif entry['type'] == 'tree_file':
                # 파일 항목 -> 한 칸 위로 이동
                if self.selected_index > 0:
                    self.selected_index -= 1
                    self.add_log(f"위로 이동", "DEBUG")

    def handle_tree_expand(self):
        """트리 뷰에서 선택된 항목에 대한 RIGHT 키 처리

        - 디렉토리 (collapsed) -> Expand
        - 디렉토리 (expanded) -> 한 칸 아래로 이동
        - 파일 -> 한 칸 아래로 이동
        """
        if 0 <= self.selected_index < len(self.directory_entries):
            entry = self.directory_entries[self.selected_index]

            if entry['type'] == 'tree_directory':
                dir_path = entry['path']
                if dir_path not in self.tree_expanded_dirs:
                    # Collapsed 상태 -> Expand
                    self.tree_expanded_dirs.add(dir_path)
                    self.add_log(f"디렉토리 펼치기: {dir_path}", "DEBUG")
                    self.build_tree_view()
                    self.needs_redraw = True
                else:
                    # 이미 Expanded 상태 -> 한 칸 아래로 이동
                    if self.selected_index < len(self.directory_entries) - 1:
                        self.selected_index += 1
                        self.add_log(f"아래로 이동", "DEBUG")
            elif entry['type'] == 'tree_file':
                # 파일 항목 -> 한 칸 아래로 이동
                if self.selected_index < len(self.directory_entries) - 1:
                    self.selected_index += 1
                    self.add_log(f"아래로 이동", "DEBUG")

    def toggle_log_viewer(self):
        """로그 뷰어 모드 토글"""
        self.log_viewer_mode = not self.log_viewer_mode
        if self.log_viewer_mode:
            # 로그 뷰어 첫 진입시 플래그 설정 (화면 그리기에서 처리됨)
            self.log_viewer_first_time = True
        else:
            # 로그 뷰어에서 메인으로 돌아갈 때 강제 새로고침
            self.force_refresh_screen()
        # 모드 전환시 즉시 화면 갱신
        self.needs_redraw = True

    def show_help(self):
        """도움말 뷰어 모드 시작"""
        self.help_viewer_mode = True
        self.help_selected_index = 0
        self.help_scroll_offset = 0
        self.needs_redraw = True

    def open_preference_editor(self):
        """환경설정 에디터 실행 (ALT+P)"""
        import curses
        import subprocess

        self.add_log("환경설정 파일을 편집합니다...", "INFO")

        # Curses 일시 중지
        curses.def_prog_mode()
        curses.endwin()

        try:
            # 환경설정 편집
            self.preference.edit()

            # 환경설정 다시 로드 (이미 edit()에서 로드하지만 명시적으로)
            # TUTORIAL.STARTUP_SHOW 설정이 변경되었을 수 있음
            startup_show = self.preference.get('', 'TUTORIAL.STARTUP_SHOW').upper()
            self.add_log(f"환경설정 로드 완료: TUTORIAL.STARTUP_SHOW={startup_show}", "INFO")

        except Exception as e:
            self.add_log(f"환경설정 편집 중 오류 발생: {e}", "ERROR")

        # Curses 재개
        curses.reset_prog_mode()
        self.stdscr.refresh()
        self.needs_redraw = True

    def handle_help_viewer_key(self, key):
        """도움말 뷰어에서 키 입력 처리"""
        needs_update = False

        if key == curses.KEY_UP:
            if self.help_selected_index > 0:
                self.help_selected_index -= 1
                # 스크롤 조정
                if self.help_selected_index < self.help_scroll_offset:
                    self.help_scroll_offset = self.help_selected_index
                needs_update = True
        elif key == curses.KEY_DOWN:
            help_content = self.get_help_content()
            if self.help_selected_index < len(help_content) - 1:
                self.help_selected_index += 1
                # 스크롤 조정 (화면 크기 고려)
                height, _ = self.stdscr.getmaxyx()
                visible_lines = height - 6  # 헤더, 푸터 제외
                if self.help_selected_index >= self.help_scroll_offset + visible_lines:
                    self.help_scroll_offset = self.help_selected_index - visible_lines + 1
                needs_update = True
        elif key == curses.KEY_HOME:
            self.help_selected_index = 0
            self.help_scroll_offset = 0
            needs_update = True
        elif key == curses.KEY_END:
            help_content = self.get_help_content()
            self.help_selected_index = len(help_content) - 1
            height, _ = self.stdscr.getmaxyx()
            visible_lines = height - 6
            self.help_scroll_offset = max(0, len(help_content) - visible_lines)
            needs_update = True
        elif key == curses.KEY_PPAGE:  # Page Up
            visible_lines = self.stdscr.getmaxyx()[0] - 6
            self.help_selected_index = max(0, self.help_selected_index - visible_lines)
            self.help_scroll_offset = max(0, self.help_scroll_offset - visible_lines)
            needs_update = True
        elif key == curses.KEY_NPAGE:  # Page Down
            help_content = self.get_help_content()
            visible_lines = self.stdscr.getmaxyx()[0] - 6
            self.help_selected_index = min(len(help_content) - 1, self.help_selected_index + visible_lines)
            height, _ = self.stdscr.getmaxyx()
            visible_lines = height - 6
            if self.help_selected_index >= self.help_scroll_offset + visible_lines:
                self.help_scroll_offset = self.help_selected_index - visible_lines + 1
            needs_update = True

        # 실제로 변경이 있을 때만 화면 갱신
        if needs_update:
            self.needs_redraw = True
        return True

    def get_help_content(self):
        """도움말 내용 반환"""
        return [
            "CCCopy TUI 도움말",
            "",
            "=== 기본 조작 ===",
            "↑↓ 방향키     : 파일 선택 이동",
            "Space         : 폴더 펼치기/접기",
            "Enter         : 파일 상세 정보 / 작업 실행 / 트리 토글",
            "← 방향키      : (트리 뷰) 디렉토리 접기 또는 위로 이동 (파일 포함)",
            "→ 방향키      : (트리 뷰) 디렉토리 펼치기 또는 아래로 이동 (파일 포함)",
            "Backspace     : 상위 경로로 이동",
            "Tab           : 포커스 전환 (파일 트리 <-> 로그 영역)",
            "ESC / Q       : 프로그램 종료",
            "",
            "=== 주요 기능 ===",
            "M             : Work <-> Production 모드 전환",
            "V             : View 스타일 전환 (detail <-> tree)",
            "D             : Download (Production -> Work)",
            "U             : Upload (Work -> Production)",
            "S             : Save (Work 저장소 커밋)",
            "H             : History (Git 히스토리 조회)",
            "├ R           : Rollback (Work 모드, 선택한 커밋으로 롤백)",
            "├ E           : Export (Production 모드, 스냅샷 zip 다운로드)",
            "└ F           : Filter (파일명 필터)",
            "P             : Project (프로젝트 관리)",
            "T             : Terminal (현재 디렉토리에서 터미널 열기)",
            "L             : Log 전체 보기",
            "R             : 파일 목록 새로고침",
            "F5            : 강제 화면 새로고침 (dialog 잔상 제거)",
            "",
            "=== 뷰 모드 ===",
            "Detail 모드   : 디렉토리별 탐색 (기본값)",
            "Tree 모드     : 전체 파일/디렉토리를 트리 구조로 표시",
            "              - 처음에는 1차 depth까지만 표시 (collapsed)",
            "              - Enter/←→ 키로 디렉토리 펼치기/접기",
            "              - 들여쓰기: 2칸 단위로 depth 표시",
            "",
            "=== 기능키 ===",
            "F2            : 도움말 (현재 화면)",
            "F9            : 튜토리얼",
            "ALT+P         : 환경설정 (preference 편집)",
            "",
            "=== 파일 상태 표시 ===",
            "[S]AME        : Production과 Work가 동일",
            "[M]ODIFIED    : Work에서 수정된 파일",
            "[U]PDATED     : Production에서 업데이트된 파일 ([D]ownload로 동기화 필요)",
            "[C]ONFLICTED  : 양쪽 모두 수정되어 충돌",
            "[ ]PENDING    : 상태 체크 진행 중인 파일",
            "",
            "=== Git 기반 협업 도구 ===",
            "CCCopy는 Work와 Production 두 Git 저장소를 관리하여",
            "안전한 팀 협업 환경을 제공합니다.",
            "",
            "Work 저장소   : 개인 작업 공간",
            "Production    : 공유 프로덕션 환경",
            "",
            "=== 충돌 해결 ===",
            "충돌 발생시 VS Code diff 또는 gvimdiff를 통해",
            "수동으로 병합할 수 있습니다.",
            "",
            "=== 프로젝트 관리 ===",
            "P키를 통해 여러 프로젝트를 생성하고 전환할 수 있습니다.",
            "각 프로젝트는 독립적인 작업 디렉토리와 설정을 가집니다.",
            "",
            "=== 보안 ===",
            "Production 쓰기 작업시에만 높은 권한으로 상승하여",
            "안전한 파일 작업을 보장합니다.",
            "[HIGH]로 시작하는 로그는 높은 권한으로 실행된 경우를 의미합니다.",
            "",
            "=== 지원 환경 ===",
            "Python 3.7+, Git 1.8+, Linux/Unix (NFS 지원)",
            "외부 라이브러리 의존성 없음 (순수 표준 라이브러리)",
        ]

    def draw_help_viewer(self, stdscr):
        """도움말 뷰어 화면 그리기"""
        height, width = stdscr.getmaxyx()

        # 헤더 그리기
        try:
            stdscr.addstr(0, 0, "┌" + "─" * (width - 2) + "┐")

            # 헤더 텍스트 라인: 각 문자를 개별적으로 써서 확실히 덮어쓰기
            header_text = "CCCopy 도움말 - Help Viewer"
            # 1번 라인 전체를 개별 문자로 구성
            line1_chars = ['│']  # 왼쪽 테두리
            line1_chars.append(' ')  # 공백

            # 헤더 텍스트 추가
            for ch in header_text:
                line1_chars.append(ch)

            # 나머지를 공백으로 채우기
            remaining = width - 2 - len(header_text) - 1  # 테두리(2) - 텍스트 - 앞공백(1)
            for _ in range(remaining):
                line1_chars.append(' ')

            line1_chars.append('│')  # 오른쪽 테두리

            # 각 문자를 개별적으로 쓰기 (확실한 덮어쓰기)
            for col, ch in enumerate(line1_chars):
                if col < width:
                    stdscr.addch(1, col, ch)

            # 구분선 클리어 후 그리기
            stdscr.move(2, 0)
            for col in range(width):
                stdscr.addch(2, col, ' ')
            stdscr.addstr(2, 0, "├" + "─" * (width - 2) + "┤")
        except curses.error:
            pass

        # 도움말 내용 영역
        help_start_row = 3
        help_end_row = height - 3  # 푸터 영역 위까지
        visible_lines = help_end_row - help_start_row

        help_content = self.get_help_content()

        # 도움말 내용 출력
        for i in range(visible_lines):
            row = help_start_row + i
            help_index = self.help_scroll_offset + i

            if row >= help_end_row:
                break

            try:
                # 줄 클리어 (한글 잔여물 방지) - 로그 뷰어와 동일한 방식
                stdscr.move(row, 0)
                for col in range(width):
                    stdscr.addch(row, col, ' ')

                # 테두리 먼저 그리기
                stdscr.addstr(row, 0, "│")
                stdscr.addstr(row, width-1, "│")

                if help_index < len(help_content):
                    content = help_content[help_index]

                    # 선택된 줄 강조
                    if help_index == self.help_selected_index:
                        try:
                            stdscr.addstr(row, 1, " " + content[:width-4], curses.A_REVERSE)
                        except curses.error:
                            stdscr.addstr(row, 1, " " + content[:width-4])
                    else:
                        # 파일 상태 표시 라인에 색상 적용 (상태 부분만 색상 적용)
                        if content.startswith("[S]AME"):
                            stdscr.addstr(row, 1, " ", curses.A_NORMAL)
                            stdscr.addstr(row, 2, "[S]AME", self.get_state_color(FileState.SAME))
                            stdscr.addstr(row, 8, content[6:][:width-10], curses.A_NORMAL)
                        elif content.startswith("[M]ODIFIED"):
                            stdscr.addstr(row, 1, " ", curses.A_NORMAL)
                            stdscr.addstr(row, 2, "[M]ODIFIED", self.get_state_color(FileState.MODIFIED))
                            stdscr.addstr(row, 12, content[10:][:width-14], curses.A_NORMAL)
                        elif content.startswith("[U]PDATED"):
                            stdscr.addstr(row, 1, " ", curses.A_NORMAL)
                            stdscr.addstr(row, 2, "[U]PDATED", self.get_state_color(FileState.UPDATED))
                            stdscr.addstr(row, 11, content[9:][:width-13], curses.A_NORMAL)
                        elif content.startswith("[C]ONFLICTED"):
                            stdscr.addstr(row, 1, " ", curses.A_NORMAL)
                            stdscr.addstr(row, 2, "[C]ONFLICTED", self.get_state_color(FileState.CONFLICTED))
                            stdscr.addstr(row, 14, content[12:][:width-16], curses.A_NORMAL)
                        elif content.startswith("[ ]PENDING"):
                            stdscr.addstr(row, 1, " ", curses.A_NORMAL)
                            stdscr.addstr(row, 2, "[ ]PENDING", self.get_state_color(FileState.PENDING))
                            stdscr.addstr(row, 12, content[10:][:width-14], curses.A_NORMAL)
                        else:
                            stdscr.addstr(row, 1, " " + content[:width-4])

            except curses.error:
                pass

        # 푸터 그리기
        try:
            # 구분선 클리어 후 그리기
            stdscr.move(height - 3, 0)
            for col in range(width):
                stdscr.addch(height - 3, col, ' ')
            stdscr.addstr(height - 3, 0, "├" + "─" * (width - 2) + "┤")

            # 도움말 텍스트 라인 - character-by-character + 색상
            help_line_chars = []
            help_line_colors = []

            # 왼쪽 테두리
            help_line_chars.append('│')
            help_line_colors.append(0)

            # 공백
            help_line_chars.append(' ')
            help_line_colors.append(0)

            # [ESC] - 노란색
            for ch in "[ESC]":
                help_line_chars.append(ch)
                help_line_colors.append(13)  # WARNING 색상

            # Exit
            for ch in "Exit":
                help_line_chars.append(ch)
                help_line_colors.append(0)

            # 나머지 공백
            remaining = width - 2 - 1 - 5 - 4  # 테두리(2) - 공백(1) - [ESC](5) - Exit(4)
            for _ in range(remaining):
                help_line_chars.append(' ')
                help_line_colors.append(0)

            # 오른쪽 테두리
            help_line_chars.append('│')
            help_line_colors.append(0)

            # 각 문자를 색상과 함께 개별 출력
            for col, (ch, color) in enumerate(zip(help_line_chars, help_line_colors)):
                if col < width:
                    stdscr.addch(height - 2, col, ch, curses.color_pair(color))

            # 하단 테두리 클리어 후 그리기
            # 마지막 라인은 width-1까지만 클리어 (curses 제약)
            stdscr.move(height - 1, 0)
            for col in range(width - 1):
                stdscr.addch(height - 1, col, ' ')
            scroll_info = f"Line {self.help_selected_index + 1}/{len(help_content)}"
            stdscr.addstr(height - 1, 0, "└" + "─" * (width - len(scroll_info) - 2) + scroll_info + "┘")
        except curses.error:
            pass

    def draw_history_viewer(self, stdscr):
        """전체 화면 히스토리 뷰어 그리기""" # greenfish
        height, width = stdscr.getmaxyx()

        # 헤더 그리기 (재시도 로직 포함)
        for attempt in range(2):  # 최대 2번 시도
            try:
                stdscr.addstr(0, 0, "┌" + "─" * (width - 2) + "┐")

                # 헤더 텍스트 라인: 각 문자를 개별적으로 써서 확실히 덮어쓰기
                header_text = f"No   Hash     Date         Author          Message"
                # 1번 라인 전체를 개별 문자로 구성
                line1_chars = ['│']  # 왼쪽 테두리
                line1_chars.append(' ')  # 공백

                # 헤더 텍스트 추가
                for ch in header_text:
                    line1_chars.append(ch)

                # 나머지를 공백으로 채우기
                remaining = width - 2 - len(header_text) - 1  # 테두리(2) - 텍스트 - 앞공백(1)
                for _ in range(remaining):
                    line1_chars.append(' ')

                line1_chars.append('│')  # 오른쪽 테두리

                # 각 문자를 개별적으로 쓰기 (확실한 덮어쓰기)
                for col, ch in enumerate(line1_chars):
                    if col < width:
                        stdscr.addch(1, col, ch)

                # 구분선 클리어 후 그리기
                stdscr.move(2, 0)
                for col in range(width):
                    stdscr.addch(2, col, ' ')
                stdscr.addstr(2, 0, "├" + "─" * (width - 2) + "┤")
                break
            except curses.error:
                if attempt == 0:
                    # 첫 번째 시도 실패시 화면 크기 재확인
                    height, width = stdscr.getmaxyx()
                    if height < 3 or width < 10:
                        break
                else:
                    # 두 번째 시도 실패시 안전한 대체
                    try:
                        stdscr.addstr(0, 0, "HISTORY VIEWER")
                        stdscr.addstr(1, 0, f"{len(self.history_list)} entries")
                    except curses.error:
                        pass

        # 히스토리 영역 계산
        history_start_row = 3
        history_end_row = height - 3  # 도움말 영역 위까지
        visible_lines = history_end_row - history_start_row

        # 스크롤 처리
        if len(self.history_list) > visible_lines:
            max_scroll_offset = len(self.history_list) - visible_lines
            self.history_scroll_offset = min(self.history_scroll_offset, max_scroll_offset)
            self.history_scroll_offset = max(0, self.history_scroll_offset)

            if self.history_selected_index < self.history_scroll_offset:
                self.history_scroll_offset = self.history_selected_index
            elif self.history_selected_index >= self.history_scroll_offset + visible_lines:
                self.history_scroll_offset = self.history_selected_index - visible_lines + 1
        else:
            self.history_scroll_offset = 0

        # 히스토리 그리기
        for i in range(visible_lines):
            row = history_start_row + i
            history_index = self.history_scroll_offset + i

            try:
                if history_index < len(self.history_list):
                    commit = self.history_list[history_index]

                    # 정보 포맷팅
                    num = f"{history_index + 1:3d}"
                    hash_short = commit['hash'][:7]
                    date_str = commit['date'][:10]  # YYYY-MM-DD
                    author = commit['author'][:15]  # 15자로 제한

                    # 고정 부분의 길이 계산 (num + hash + date + author + 구분자들)
                    fixed_part = f"{num}  {hash_short:7}  {date_str:10}  {author:15}  "
                    fixed_length = len(fixed_part)

                    # 메시지에 사용할 수 있는 공간 계산
                    inner_width = width - 2
                    available_message_width = inner_width - 1 - fixed_length  # 앞공백(1) 제외

                    # 메시지 길이를 사용 가능한 공간에 맞게 조정
                    if available_message_width > 0:
                        if len(commit['message']) > available_message_width:
                            message = commit['message'][:available_message_width-3] + "..."
                        else:
                            message = commit['message']
                    else:
                        message = ""

                    line_text = fixed_part + message

                    # 도움말처럼 라인 전체를 문자 배열로 구성
                    line_chars = ['│']  # 왼쪽 테두리
                    line_chars.append(' ')  # 공백

                    # 텍스트 추가
                    for ch in line_text:
                        line_chars.append(ch)

                    # 나머지를 공백으로 채우기
                    remaining = width - 2 - len(line_text) - 1  # 테두리(2) - 텍스트 - 앞공백(1)
                    for _ in range(remaining):
                        line_chars.append(' ')

                    line_chars.append('│')  # 오른쪽 테두리

                    # 각 문자를 개별적으로 쓰기 (확실한 덮어쓰기)
                    if history_index == self.history_selected_index:
                        color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                        for col, ch in enumerate(line_chars):
                            if col < width:
                                stdscr.addch(row, col, ch, color)
                    else:
                        for col, ch in enumerate(line_chars):
                            if col < width:
                                stdscr.addch(row, col, ch)
                else:
                    # 빈 줄 - 도움말처럼 전체 라인 구성
                    line_chars = ['│']
                    for _ in range(width - 2):
                        line_chars.append(' ')
                    line_chars.append('│')

                    for col, ch in enumerate(line_chars):
                        if col < width:
                            stdscr.addch(row, col, ch)
            except curses.error:
                pass

        # 하단 테두리와 도움말
        try:
            # 구분선 클리어 후 그리기
            stdscr.move(height - 3, 0)
            for col in range(width):
                stdscr.addch(height - 3, col, ' ')
            stdscr.addstr(height - 3, 0, "├" + "─" * (width - 2) + "┤")

            # 도움말 텍스트 라인 - character-by-character로 작성
            # 필터 상태 표시
            if self.history_filter.get('filename'):
                filter_part = f"[F]ilter(:{self.history_filter['filename']})"
            else:
                filter_part = "[F]ilter"

            # mode에 따라 다른 도움말 표시
            if self.mode == ViewMode.WORK:
                help_text = f"[Enter]Detail {filter_part} [R]ollback [ESC]Exit"
            else:  # production
                help_text = f"[Enter]Detail {filter_part} [E]xport [ESC]Exit"

            # 라인 전체를 문자 배열 + 색상 배열로 구성
            help_line_chars = []
            help_line_colors = []

            # 왼쪽 테두리
            help_line_chars.append('│')
            help_line_colors.append(0)

            # 공백
            help_line_chars.append(' ')
            help_line_colors.append(0)

            # help_text를 파싱하여 [X] 부분은 노란색으로
            i = 0
            while i < len(help_text):
                if help_text[i] == '[':
                    # [ 부터 ] 까지 찾기
                    end_bracket = help_text.find(']', i)
                    if end_bracket != -1:
                        # [X] 전체를 노란색으로
                        for ch in help_text[i:end_bracket+1]:
                            help_line_chars.append(ch)
                            help_line_colors.append(2)  # 노란색
                        i = end_bracket + 1
                    else:
                        help_line_chars.append(help_text[i])
                        help_line_colors.append(0)
                        i += 1
                else:
                    help_line_chars.append(help_text[i])
                    help_line_colors.append(0)
                    i += 1

            # 나머지를 공백으로 채우기
            help_remaining = width - 2 - len(help_text) - 1
            for _ in range(help_remaining):
                help_line_chars.append(' ')
                help_line_colors.append(0)

            # 오른쪽 테두리
            help_line_chars.append('│')
            help_line_colors.append(0)

            # 스크롤 정보를 배열에 직접 덮어쓰기
            if len(self.history_list) > visible_lines:
                scroll_info = f"[{self.history_selected_index + 1}/{len(self.history_list)}]"
                scroll_pos = width - len(scroll_info) - 2
                if scroll_pos > 10:
                    # 스크롤 정보를 문자 배열에 직접 삽입
                    for i, ch in enumerate(scroll_info):
                        help_line_chars[scroll_pos + i] = ch
                        help_line_colors[scroll_pos + i] = 0  # 기본 색상

            # 각 문자를 색상과 함께 개별 출력
            for col, (ch, color) in enumerate(zip(help_line_chars, help_line_colors)):
                if col < width:
                    stdscr.addch(height - 2, col, ch, curses.color_pair(color))

            # 하단 테두리 클리어 후 그리기
            # 마지막 라인은 width-1까지만 클리어 (curses 제약)
            stdscr.move(height - 1, 0)
            for col in range(width - 1):
                stdscr.addch(height - 1, col, ' ')
            stdscr.addstr(height - 1, 0, "└" + "─" * (width - 2) + "┘")
        except curses.error:
            pass

    def draw_history_detail_viewer(self, stdscr):
        """히스토리 상세 뷰어 그리기"""
        height, width = stdscr.getmaxyx()

        if self.history_selected_index >= len(self.history_list):
            return

        commit = self.history_list[self.history_selected_index]

        # 헤더 그리기
        try:
            stdscr.addstr(0, 0, "┌" + "─" * (width - 2) + "┐")

            header_text = f"커밋: {commit['hash'][:7]}"
            stdscr.addstr(1, 0, "│")
            stdscr.addstr(1, 1, " " + header_text)
            stdscr.addstr(1, width-1, "│")

            author_text = f"작성자: {commit['author']}"
            stdscr.addstr(2, 0, "│")
            stdscr.addstr(2, 1, " " + author_text)
            stdscr.addstr(2, width-1, "│")

            date_text = f"날짜: {commit['date']}"
            stdscr.addstr(3, 0, "│")
            stdscr.addstr(3, 1, " " + date_text)
            stdscr.addstr(3, width-1, "│")

            message_text = f"메시지: {commit['message']}"
            stdscr.addstr(4, 0, "│")
            stdscr.addstr(4, 1, " " + message_text)
            stdscr.addstr(4, width-1, "│")

            stdscr.addstr(5, 0, "├" + "─" * (width - 2) + "┤")

            files_header = "변경된 파일:"
            stdscr.addstr(6, 0, "│")
            stdscr.addstr(6, 1, " " + files_header)
            stdscr.addstr(6, width-1, "│")

        except curses.error:
            stdscr.addstr(0, 0, "HISTORY DETAIL")

        # 파일 목록 영역 계산
        files_start_row = 7
        files_end_row = height - 3
        visible_lines = files_end_row - files_start_row

        # 스크롤 처리
        if len(self.history_detail_files) > visible_lines:
            max_scroll_offset = len(self.history_detail_files) - visible_lines
            self.history_detail_scroll_offset = min(self.history_detail_scroll_offset, max_scroll_offset)
            self.history_detail_scroll_offset = max(0, self.history_detail_scroll_offset)

            if self.history_detail_selected_index < self.history_detail_scroll_offset:
                self.history_detail_scroll_offset = self.history_detail_selected_index
            elif self.history_detail_selected_index >= self.history_detail_scroll_offset + visible_lines:
                self.history_detail_scroll_offset = self.history_detail_selected_index - visible_lines + 1
        else:
            self.history_detail_scroll_offset = 0

        # 파일 목록 그리기
        for i in range(visible_lines):
            row = files_start_row + i
            file_index = self.history_detail_scroll_offset + i

            try:
                stdscr.addstr(row, 0, "│")

                if file_index < len(self.history_detail_files):
                    file_info = self.history_detail_files[file_index]
                    line_text = f" {file_index + 1:2d}. {file_info}"

                    # 텍스트 길이 제한
                    max_text_width = width - 4
                    if len(line_text) > max_text_width:
                        line_text = line_text[:max_text_width-3] + "..."

                    # 선택된 항목 하이라이트
                    if file_index == self.history_detail_selected_index:
                        color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                        try:
                            safe_text = line_text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                            stdscr.addstr(row, 1, safe_text, color)
                        except curses.error:
                            ascii_text = ''.join(c if ord(c) < 128 else '?' for c in line_text)
                            try:
                                stdscr.addstr(row, 1, ascii_text, color)
                            except curses.error:
                                pass
                    else:
                        try:
                            safe_text = line_text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                            stdscr.addstr(row, 1, safe_text)
                        except curses.error:
                            ascii_text = ''.join(c if ord(c) < 128 else '?' for c in line_text)
                            try:
                                stdscr.addstr(row, 1, ascii_text)
                            except curses.error:
                                pass
                else:
                    # 빈 줄 - 테두리 내부만 클리어
                    try:
                        for col in range(1, width-1):
                            stdscr.addch(row, col, ' ')
                    except curses.error:
                        pass

                # 오른쪽 테두리
                stdscr.addstr(row, width-1, "│")
            except curses.error:
                pass

        # 하단 테두리와 도움말
        try:
            stdscr.addstr(height - 3, 0, "├" + "─" * (width - 2) + "┤")

            # 도움말 텍스트 라인 - character-by-character + 색상
            help_text = "[Enter]VS Code Diff [ESC]Back to History"
            help_line_chars = []
            help_line_colors = []

            # 왼쪽 테두리
            help_line_chars.append('│')
            help_line_colors.append(0)

            # 공백
            help_line_chars.append(' ')
            help_line_colors.append(0)

            # help_text를 파싱하여 [X] 부분은 노란색으로
            i = 0
            while i < len(help_text):
                if help_text[i] == '[':
                    # [ 부터 ] 까지 찾기
                    end_bracket = help_text.find(']', i)
                    if end_bracket != -1:
                        # [X] 전체를 노란색으로
                        for ch in help_text[i:end_bracket+1]:
                            help_line_chars.append(ch)
                            help_line_colors.append(2)  # 노란색
                        i = end_bracket + 1
                    else:
                        help_line_chars.append(help_text[i])
                        help_line_colors.append(0)
                        i += 1
                else:
                    help_line_chars.append(help_text[i])
                    help_line_colors.append(0)
                    i += 1

            # 나머지를 공백으로 채우기
            help_remaining = width - 2 - len(help_text) - 1
            for _ in range(help_remaining):
                help_line_chars.append(' ')
                help_line_colors.append(0)

            # 오른쪽 테두리
            help_line_chars.append('│')
            help_line_colors.append(0)

            # 스크롤 정보를 배열에 직접 덮어쓰기
            if len(self.history_detail_files) > visible_lines:
                scroll_info = f"[{self.history_detail_selected_index + 1}/{len(self.history_detail_files)}]"
                scroll_pos = width - len(scroll_info) - 2
                if scroll_pos > 10:
                    # 스크롤 정보를 문자 배열에 직접 삽입
                    for i, ch in enumerate(scroll_info):
                        help_line_chars[scroll_pos + i] = ch
                        help_line_colors[scroll_pos + i] = 0  # 기본 색상

            # 각 문자를 색상과 함께 개별 출력
            for col, (ch, color) in enumerate(zip(help_line_chars, help_line_colors)):
                if col < width:
                    stdscr.addch(height - 2, col, ch, curses.color_pair(color))

            # 최종 하단 테두리
            stdscr.addstr(height - 1, 0, "└" + "─" * (width - 2) + "┘")
        except curses.error:
            pass

    def draw_log_viewer(self, stdscr):
        """전체 화면 로그 뷰어 그리기"""
        height, width = stdscr.getmaxyx()

        # 로그 파일을 보고 있는 경우, 파일에서 로그 읽기
        if self.viewing_log_file:
            try:
                with open(self.viewing_log_file, 'r', encoding='utf-8') as f:
                    file_logs = [line.rstrip('\n') for line in f]
            except Exception as e:
                self.add_log(f"로그 파일 읽기 실패: {e}", "ERROR")
                self.viewing_log_file = None
                file_logs = self.logs
        else:
            file_logs = self.logs

        # DEBUG 로그 필터링 (self.log_show_all_debug에 따라)
        if self.log_show_all_debug:
            # 전체 DEBUG 로그 표시
            filtered_logs = file_logs
        else:
            # DEBUG 로그는 최신 5개만 표시
            debug_logs = [log for log in file_logs if "[DEBUG]" in log]
            if len(debug_logs) > 5:
                # 가장 오래된 DEBUG 로그들을 제외
                old_debug_count = len(debug_logs) - 5
                excluded_debug = 0
                filtered_logs = []
                for log in file_logs:
                    if "[DEBUG]" in log:
                        if excluded_debug < old_debug_count:
                            excluded_debug += 1
                            continue  # 오래된 DEBUG 로그는 제외
                    filtered_logs.append(log)
            else:
                filtered_logs = file_logs

        # 헤더 그리기
        try:
            stdscr.addstr(0, 0, "┌" + "─" * (width - 2) + "┐")
            debug_mode_str = "ALL DEBUG" if self.log_show_all_debug else "LAST 5 DEBUG"

            # 현재 로그 파일 또는 선택한 로그 파일 표시
            if self.viewing_log_file:
                log_filename = os.path.basename(self.viewing_log_file)
                header_text = f"LOG VIEWER - {log_filename} ({len(filtered_logs)} entries, {debug_mode_str})"
            else:
                header_text = f"LOG VIEWER - Current ({len(filtered_logs)} entries, {debug_mode_str})"

            help_text = "[D]EBUG 보기 [F]iles [ESC]Exit"

            # 헤더와 도움말을 한 줄에 표시
            stdscr.addstr(1, 0, "│")
            combined_text = f"{header_text} - "
            if len(combined_text) + len(help_text) <= width - 3:
                stdscr.addstr(1, 1, combined_text)
                # 도움말 부분에 색상 적용
                self._draw_commands_with_color(stdscr, 1, 1 + len(combined_text), help_text, width - 3 - len(combined_text))
            else:
                # 공간이 부족하면 헤더만 표시
                if len(header_text) <= width-3:
                    stdscr.addstr(1, 1, header_text)
                else:
                    stdscr.addstr(1, 1, header_text[:width-6] + "...")
            stdscr.addstr(1, width-1, "│")
            stdscr.addstr(2, 0, "├" + "─" * (width - 2) + "┤")
        except curses.error:
            stdscr.addstr(0, 0, "LOG VIEWER")
            stdscr.addstr(1, 0, f"{len(filtered_logs)} log entries")

        # 로그 영역 계산
        log_start_row = 3
        log_end_row = height - 1  # 하단 테두리 바로 위까지
        visible_lines = log_end_row - log_start_row

        # 로그 선택 인덱스 범위 검증 (로그 파일 변경시 중요)
        if len(filtered_logs) == 0:
            self.log_selected_index = 0
            self.log_scroll_offset = 0
        else:
            # 선택 인덱스가 범위를 벗어나면 조정
            if self.log_selected_index >= len(filtered_logs):
                self.log_selected_index = len(filtered_logs) - 1
            if self.log_selected_index < 0:
                self.log_selected_index = 0

        # 첫 번째 진입시 스크롤 위치를 마지막 로그가 화면 맨 아래에 오도록 조정
        if self.log_viewer_first_time:
            self.log_viewer_first_time = False
            if len(filtered_logs) > 0:
                if len(filtered_logs) > visible_lines:
                    # 마지막 로그가 화면 맨 아래 줄에 오도록 스크롤 오프셋 설정
                    self.log_scroll_offset = len(filtered_logs) - visible_lines
                    # 마지막 로그를 선택
                    self.log_selected_index = len(filtered_logs) - 1
                else:
                    # 로그가 화면보다 적으면 처음부터 표시
                    self.log_scroll_offset = 0
                    self.log_selected_index = len(filtered_logs) - 1

        # 스크롤 처리 - 화면 크기 변경에 대응
        if len(filtered_logs) > visible_lines:
            # 스크롤 오프셋이 유효한 범위를 벗어나지 않도록 보정
            max_scroll_offset = len(filtered_logs) - visible_lines
            self.log_scroll_offset = min(self.log_scroll_offset, max_scroll_offset)
            self.log_scroll_offset = max(0, self.log_scroll_offset)

            # 선택된 항목이 화면에 보이도록 스크롤 조정
            if self.log_selected_index < self.log_scroll_offset:
                self.log_scroll_offset = self.log_selected_index
            elif self.log_selected_index >= self.log_scroll_offset + visible_lines:
                self.log_scroll_offset = self.log_selected_index - visible_lines + 1
        else:
            # 로그 개수가 화면보다 적은 경우 스크롤 오프셋 초기화
            self.log_scroll_offset = 0

        # 로그 그리기
        for i in range(visible_lines):
            row = log_start_row + i
            log_index = self.log_scroll_offset + i

            try:
                stdscr.addstr(row, 0, "│")

                if log_index < len(filtered_logs):
                    log_entry = filtered_logs[log_index]

                    # 텍스트 길이 제한 (한글 안전 자르기)
                    max_text_width = width - 4
                    if len(log_entry) <= max_text_width:
                        display_text = log_entry
                    else:
                        display_text = log_entry[:max_text_width-3] + "..."

                    # 줄 클리어 (한글 잔여물 방지) - 오른쪽 테두리 전까지만
                    try:
                        stdscr.move(row, 1)
                        # width-2까지만 클리어하여 오른쪽 테두리 보호
                        for col in range(1, width-1):
                            stdscr.addch(row, col, ' ')
                    except curses.error:
                        pass

                    # 선택된 항목 하이라이트 (모든 로그 동일한 열에서 시작)
                    if log_index == self.log_selected_index:
                        # 선택된 항목은 반전 색상 우선 적용
                        selected_color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                        stdscr.addstr(row, 1, ">")
                        try:
                            formatted_text = self.format_log_message(display_text)
                            safe_text = formatted_text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                            stdscr.addstr(row, 3, safe_text, selected_color)
                        except curses.error:
                            # 한글 표시 실패시 ASCII 변환 (포맷팅 적용)
                            formatted_text = self.format_log_message(display_text)
                            ascii_text = ''.join(c if ord(c) < 128 else '?' for c in formatted_text)
                            try:
                                stdscr.addstr(row, 3, ascii_text, selected_color)
                            except curses.error:
                                pass
                    else:
                        stdscr.addstr(row, 1, " ")  # 선택 표시 자리에 공백
                        # 키워드만 색상을 적용하여 출력
                        self.render_log_with_colored_keyword(stdscr, row, 3, display_text, max_text_width - 3)
                else:
                    # 빈 줄 - 테두리 보호하며 클리어
                    try:
                        for col in range(1, width-1):
                            stdscr.addch(row, col, ' ')
                    except curses.error:
                        pass

                # 오른쪽 테두리 그리기
                stdscr.addstr(row, width-1, "│")
            except curses.error:
                # 안전한 대안 (동일한 정렬)
                try:
                    if log_index < len(filtered_logs):
                        log_text = filtered_logs[log_index]
                        formatted_log = self.format_log_message(log_text)
                        if len(formatted_log) <= width-7:
                            simple_text = formatted_log
                        else:
                            simple_text = formatted_log[:width-10] + "..."
                        if log_index == self.log_selected_index:
                            stdscr.addstr(row, 1, ">")
                            stdscr.addstr(row, 3, simple_text)
                        else:
                            stdscr.addstr(row, 1, " ")
                            stdscr.addstr(row, 3, simple_text)
                except curses.error:
                    pass

        # 하단 테두리 그리기
        try:
            stdscr.addstr(height - 1, 0, "└" + "─" * (width - 2) + "┘")
        except curses.error:
            pass

        # 스크롤 정보 표시 (헤더에 표시)
        try:
            if len(filtered_logs) > visible_lines:
                scroll_info = f"[{self.log_selected_index + 1}/{len(filtered_logs)}]"
                scroll_pos = width - len(scroll_info) - 2
                if scroll_pos > 10:
                    stdscr.addstr(1, scroll_pos, scroll_info)
        except curses.error:
            pass

    def load_history_detail_files(self, commit_hash):
        """히스토리 상세 파일 목록 로드"""
        try:
            if self.mode == ViewMode.WORK:
                base_dir = self.workspace.working_dir
            else:
                base_dir = self.workspace.production_dir

            # Git 명령으로 커밋에서 변경된 파일 목록 가져오기
            result = GitHelper.run_git_command(
                ['show', '--name-status', commit_hash],
                cwd=base_dir,
                capture_output=True
            )

            files = []
            if result:
                lines = result.strip().split('\n')
                for line in lines:
                    if line and '\t' in line:
                        # 파일 상태와 이름 분리 (A\tfile.txt 형식)
                        parts = line.split('\t', 1)
                        if len(parts) == 2:
                            status, filename = parts
                            status_text = {
                                'A': '[Added]',
                                'M': '[Modified]',
                                'D': '[Deleted]',
                                'R': '[Renamed]',
                                'C': '[Copied]'
                            }.get(status, f'[{status}]')
                            files.append(f"{status_text} {filename}")

            self.history_detail_files = files
            self.history_detail_selected_index = 0
            self.history_detail_scroll_offset = 0
            self.current_commit_hash = commit_hash

            self.add_log(f"{len(files)}개 변경 파일 로드됨", "INFO")

        except Exception as e:
            self.add_log(f"파일 목록 로드 실패: {e}", "ERROR")
            self.history_detail_files = []

    def show_history_filter_dialog(self):
        """히스토리 필터 다이얼로그 표시"""
        try:
            # 현재 필터 값 가져오기
            current_filter = self.history_filter.get('filename', '')

            # 입력 다이얼로그 표시
            result = self.show_input_dialog(
                "파일명 필터",
                "파일명 또는 경로를 입력하세요 (예: readme.txt)\n공백 입력시 필터 해제",
                current_filter
            )

            if result is None:
                # 취소
                return

            # 필터 적용
            if result.strip():
                self.history_filter['filename'] = result.strip()
                self.apply_history_filter()
                self.add_log(f"필터 적용: {result.strip()}", "INFO")
            else:
                # 필터 해제
                self.history_filter = {}
                self.history_list = self.history_list_original[:]
                self.history_selected_index = 0
                self.history_scroll_offset = 0
                self.add_log("필터 해제됨", "INFO")

            self.needs_redraw = True

        except Exception as e:
            self.add_log(f"필터 다이얼로그 오류: {e}", "ERROR")

    def apply_history_filter(self):
        """히스토리 필터 적용"""
        try:
            if not self.history_filter:
                # 필터가 없으면 원본 목록 사용
                self.history_list = self.history_list_original[:]
                return

            filename_filter = self.history_filter.get('filename', '').strip()

            if not filename_filter:
                # 파일명 필터가 없으면 원본 목록 사용
                self.history_list = self.history_list_original[:]
                return

            # 파일명 필터 적용
            filtered_list = []

            if self.mode == ViewMode.WORK:
                base_dir = self.workspace.working_dir
            else:
                base_dir = self.workspace.production_dir

            for commit in self.history_list_original:
                # 각 커밋에서 변경된 파일 목록 확인 (diff-tree는 파일명만 출력)
                result = GitHelper.run_git_command(
                    ['diff-tree', '--no-commit-id', '--name-only', '-r', commit['hash']],
                    cwd=base_dir,
                    capture_output=True
                )

                if result:
                    files = [f.strip() for f in result.strip().split('\n') if f.strip()]
                    # 파일명 필터와 매칭되는 파일이 있는지 확인
                    for file_path in files:
                        if filename_filter.lower() in file_path.lower():
                            filtered_list.append(commit)
                            break

            if filtered_list:
                self.history_list = filtered_list
                self.history_selected_index = 0
                self.history_scroll_offset = 0
                self.add_log(f"필터 결과: {len(filtered_list)}개 커밋 (전체: {len(self.history_list_original)}개)", "INFO")
            else:
                self.add_log(f"해당 조건의 커밋이 없습니다: {filename_filter}", "WARN")
                # 빈 목록으로 설정
                self.history_list = []
                self.history_selected_index = 0
                self.history_scroll_offset = 0

        except Exception as e:
            self.add_log(f"필터 적용 실패: {e}", "ERROR")
            # 실패시 원본 목록 복원
            self.history_list = self.history_list_original[:]

    def rollback_work_to_commit(self):
        """Work 저장소를 선택한 커밋 직전 상태로 롤백"""
        if self.history_selected_index >= len(self.history_list):
            return

        try:
            commit = self.history_list[self.history_selected_index]
            commit_hash = commit['hash']
            commit_msg = commit['message'][:30]

            # 경고 메시지 표시
            warning_msg = f"""선택한 커밋: {commit_hash[:7]}
메시지: {commit_msg}

현재 작업 중인 파일은 보관되지 않고,
선택한 시점의 상태로 즉시 롤백됩니다.

계속하시겠습니까?"""

            result = self.messagebox(
                warning_msg,
                "Work 롤백 경고",
                "warn",
                "yesno"
            )

            if result != "yes":
                self.add_log("롤백 취소됨", "INFO")
                self.needs_redraw = True
                return

            # 롤백 실행
            base_dir = self.workspace.working_dir
            self.add_log(f"Work 롤백 시작: {commit_hash[:7]} 시점으로...", "HIGH")

            # 1. git checkout HEAD (현재 작업 중인 변경사항 모두 버림)
            self.add_log("현재 작업 중인 변경사항 버리는 중...", "INFO")
            GitHelper.run_git_command(['checkout', 'HEAD', '.'], cwd=base_dir)

            # 2. 선택한 커밋이 HEAD인지 확인
            head_hash = GitHelper.run_git_command(
                ['rev-parse', 'HEAD'],
                cwd=base_dir,
                capture_output=True
            ).strip()

            # 선택한 커밋도 전체 해시로 변환
            full_commit_hash = GitHelper.run_git_command(
                ['rev-parse', commit_hash],
                cwd=base_dir,
                capture_output=True
            ).strip()

            self.add_log(f"HEAD: {head_hash[:7]}, 선택: {full_commit_hash[:7]}", "DEBUG")

            if head_hash == full_commit_hash:
                # 최신 커밋을 선택한 경우: git checkout HEAD로 작업 파일을 HEAD 상태로 되돌림
                self.add_log(f"최신 커밋 {commit_hash[:7]} 상태로 복원 완료", "HIGH")
                self.messagebox(
                    f"""최신 커밋 {commit_hash[:7]} 상태로 복원되었습니다.

수정된 파일들이 HEAD 상태로 되돌아갔습니다.""",
                    "롤백 완료",
                    "info",
                    "ok"
                )
            else:
                # 과거 커밋을 선택한 경우: 해당 커밋 이후의 모든 변경사항 revert
                self.add_log(f"커밋 {commit_hash[:7]} 이후 변경사항 되돌리는 중...", "INFO")
                GitHelper.run_git_command(
                    ['revert', '--no-commit', f'{commit_hash}..HEAD'],
                    cwd=base_dir
                )

                self.add_log(f"롤백 완료: 변경사항이 uncommitted 상태로 남았습니다", "HIGH")
                self.messagebox(
                    """롤백이 완료되었습니다.

변경사항이 uncommitted 상태로 남아있습니다.
필요시 [S]ave 키로 커밋하세요.""",
                    "롤백 완료",
                    "info",
                    "ok"
                )

            # 히스토리 뷰어 종료 및 화면 갱신
            self.history_viewer_mode = False
            self.needs_redraw = True
            # Rollback 후 강제 Full Refresh (캐시 무시하고 Production 변경 체크)
            self.refresh_tree(full_refresh=True)

        except Exception as e:
            self.add_log(f"롤백 실패: {e}", "ERROR")
            self.messagebox(
                f"롤백 실패: {e}",
                "오류",
                "error",
                "ok"
            )
            self.needs_redraw = True

    def export_production_snapshot(self):
        """Production 저장소의 선택한 커밋 직전 상태를 zip으로 export"""
        if self.history_selected_index >= len(self.history_list):
            return

        try:
            import tempfile
            import re

            commit = self.history_list[self.history_selected_index]
            commit_hash = commit['hash']

            # 프로젝트명과 해시로 기본 파일명 생성
            project_name = self.workspace.get_current_project_name() or "cccopy"
            # 금칙문자 제거 (파일명에 사용 불가한 문자)
            safe_project_name = re.sub(r'[<>:"/\\|?*]', '_', project_name)
            default_filename = f"{safe_project_name}_{commit_hash[:7]}.zip"
            default_path = os.path.join(tempfile.gettempdir(), default_filename)

            # 경로 입력 다이얼로그
            message = f"""선택한 커밋: {commit_hash[:7]}
메시지: {commit['message'][:30]}

선택된 시점의 상태를 압축하여
다운로드합니다.

저장할 경로를 입력하세요:"""

            output_path = self.show_input_dialog(
                "Production Export",
                message,
                default_path
            )

            if not output_path or output_path.strip() == "":
                self.add_log("Export 취소됨", "INFO")
                self.needs_redraw = True
                return

            output_path = output_path.strip()

            # Export 실행
            base_dir = self.workspace.production_dir
            self.add_log(f"Production export 시작: {commit_hash[:7]} 시점 상태", "HIGH")

            # git archive --format=zip -o {output}.zip <commit_id>
            self.add_log(f"압축 파일 생성 중: {output_path}", "INFO")

            # 선택한 커밋의 전체 해시 구하기
            full_commit_hash = GitHelper.run_git_command(
                ['rev-parse', commit_hash],
                cwd=base_dir,
                capture_output=True
            ).strip()

            # git archive 실행 (선택한 커밋 상태)
            GitHelper.run_git_command(
                ['archive', '--format=zip', f'-o{output_path}', full_commit_hash],
                cwd=base_dir
            )

            # 파일 크기 확인
            file_size = os.path.getsize(output_path)
            size_mb = file_size / (1024 * 1024)

            self.add_log(f"Export 완료: {output_path} ({size_mb:.2f} MB)", "HIGH")
            self.messagebox(
                f"""Export가 완료되었습니다.

파일: {output_path}
크기: {size_mb:.2f} MB""",
                "Export 완료",
                "info",
                "ok"
            )

            self.needs_redraw = True

        except Exception as e:
            self.add_log(f"Export 실패: {e}", "ERROR")
            self.messagebox(
                f"Export 실패: {e}",
                "오류",
                "error",
                "ok"
            )
            self.needs_redraw = True

    def run_vscode_diff_for_history_file(self, file_index):
        """히스토리 파일에 대해 VS Code diff 실행 (커밋 변경사항 표시)"""
        if file_index >= len(self.history_detail_files):
            return

        try:
            file_info = self.history_detail_files[file_index]
            # 파일명 추출 ('[Status] filename' 형식에서 filename 부분)
            if '] ' in file_info:
                filename = file_info.split('] ', 1)[1]
            else:
                self.add_log("파일명을 추출할 수 없습니다", "ERROR")
                return

            if self.mode == ViewMode.WORK:
                base_dir = self.workspace.working_dir
            else:
                base_dir = self.workspace.production_dir

            # 선택한 커밋의 부모 커밋 찾기
            try:
                parent_commit = GitHelper.run_git_command(
                    ['rev-parse', f'{self.current_commit_hash}^'],
                    cwd=base_dir,
                    capture_output=True
                )
                if not parent_commit:
                    self.add_log("부모 커밋을 찾을 수 없습니다 (초기 커밋일 수 있음)", "WARNING")
                    parent_commit = None
            except:
                parent_commit = None

            import tempfile
            temp_files = []

            try:
                # 왼쪽: 부모 커밋의 파일 내용 (변경 전)
                if parent_commit:
                    with tempfile.NamedTemporaryFile(mode='w', suffix=f'_before_{parent_commit[:7]}_{os.path.basename(filename)}', delete=False) as temp_before:
                        try:
                            before_content = GitHelper.run_git_command(
                                ['show', f'{parent_commit}:{filename}'],
                                cwd=base_dir,
                                capture_output=True
                            )
                            if before_content is not None:
                                temp_before.write(before_content)
                                temp_before.flush()
                                temp_before_path = temp_before.name
                                temp_files.append(temp_before_path)
                            else:
                                # 파일이 새로 생성된 경우 빈 파일 생성
                                temp_before_path = temp_before.name
                                temp_files.append(temp_before_path)
                        except:
                            # 파일이 새로 생성된 경우 빈 파일
                            temp_before_path = temp_before.name
                            temp_files.append(temp_before_path)
                else:
                    # 초기 커밋인 경우 빈 파일과 비교
                    with tempfile.NamedTemporaryFile(mode='w', suffix=f'_initial_empty_{os.path.basename(filename)}', delete=False) as temp_before:
                        temp_before_path = temp_before.name
                        temp_files.append(temp_before_path)

                # 오른쪽: 선택한 커밋의 파일 내용 (변경 후)
                with tempfile.NamedTemporaryFile(mode='w', suffix=f'_after_{self.current_commit_hash[:7]}_{os.path.basename(filename)}', delete=False) as temp_after:
                    try:
                        after_content = GitHelper.run_git_command(
                            ['show', f'{self.current_commit_hash}:{filename}'],
                            cwd=base_dir,
                            capture_output=True
                        )
                        if after_content is not None:
                            temp_after.write(after_content)
                            temp_after.flush()
                            temp_after_path = temp_after.name
                            temp_files.append(temp_after_path)

                            # VS Code diff 실행 (읽기 전용)
                            self.add_log(f"VS Code diff 실행: {filename} ({self.current_commit_hash[:7]}의 변경사항)", "INFO")
                            # curses 환경에서 안전하게 VS Code diff 실행
                            self.safe_run_external_program(
                                self.run_vscode_diff_external,
                                temp_before_path, temp_after_path, f"{filename} - 커밋 변경사항"
                            )

                        else:
                            self.add_log("커밋 당시 파일 내용을 가져올 수 없습니다", "ERROR")

                    except Exception as e:
                        self.add_log(f"Git show 실패: {e}", "ERROR")

            finally:
                # 모든 임시 파일 삭제
                for temp_file in temp_files:
                    try:
                        os.unlink(temp_file)
                    except:
                        pass

        except Exception as e:
            self.add_log(f"VS Code diff 실행 실패: {e}", "ERROR")

    def handle_history_viewer_key(self, key):
        """히스토리 뷰어에서 키 입력 처리"""
        needs_update = False

        if self.history_detail_mode:
            # 상세 모드에서의 키 처리
            if key == 27:  # ESC - 히스토리 목록으로 돌아가기
                self.history_detail_mode = False
                needs_update = True
            elif key == curses.KEY_UP:
                if self.history_detail_selected_index > 0:
                    self.history_detail_selected_index -= 1
                    needs_update = True
            elif key == curses.KEY_DOWN:
                if self.history_detail_selected_index < len(self.history_detail_files) - 1:
                    self.history_detail_selected_index += 1
                    needs_update = True
            elif key == curses.KEY_HOME:
                self.history_detail_selected_index = 0
                needs_update = True
            elif key == curses.KEY_END:
                self.history_detail_selected_index = max(0, len(self.history_detail_files) - 1)
                needs_update = True
            elif key == curses.KEY_PPAGE:  # Page Up
                self.history_detail_selected_index = max(0, self.history_detail_selected_index - 10)
                needs_update = True
            elif key == curses.KEY_NPAGE:  # Page Down
                self.history_detail_selected_index = min(len(self.history_detail_files) - 1, self.history_detail_selected_index + 10)
                needs_update = True
            elif key == ord('\n') or key == 10 or key == 13 or key == curses.KEY_ENTER:  # Enter - VS Code diff
                self.run_vscode_diff_for_history_file(self.history_detail_selected_index)
        else:
            # 목록 모드에서의 키 처리
            if key == 27:  # ESC - 히스토리 뷰어 종료
                self.history_viewer_mode = False
                self.needs_redraw = True
                return True
            elif key == ord('f') or key == ord('F'):  # F키 - 필터
                self.show_history_filter_dialog()
                return True
            elif key == ord('r') or key == ord('R'):  # R키 - Work 롤백
                if self.mode == ViewMode.WORK:
                    self.rollback_work_to_commit()
                    return True
            elif key == ord('e') or key == ord('E'):  # E키 - Production export
                if self.mode == ViewMode.PRODUCTION:
                    self.export_production_snapshot()
                    return True
            elif key == curses.KEY_UP:
                if self.history_selected_index > 0:
                    self.history_selected_index -= 1
                    needs_update = True
            elif key == curses.KEY_DOWN:
                if self.history_selected_index < len(self.history_list) - 1:
                    self.history_selected_index += 1
                    needs_update = True
            elif key == curses.KEY_HOME:
                self.history_selected_index = 0
                needs_update = True
            elif key == curses.KEY_END:
                self.history_selected_index = max(0, len(self.history_list) - 1)
                needs_update = True
            elif key == curses.KEY_PPAGE:  # Page Up
                self.history_selected_index = max(0, self.history_selected_index - 10)
                needs_update = True
            elif key == curses.KEY_NPAGE:  # Page Down
                self.history_selected_index = min(len(self.history_list) - 1, self.history_selected_index + 10)
                needs_update = True
            elif key == ord('\n') or key == 10 or key == 13 or key == curses.KEY_ENTER:  # Enter - 상세 보기
                if self.history_selected_index < len(self.history_list):
                    commit = self.history_list[self.history_selected_index]
                    self.load_history_detail_files(commit['hash'])
                    self.history_detail_mode = True
                    needs_update = True

        # 실제로 변경이 있을 때만 화면 갱신
        if needs_update:
            self.needs_redraw = True
        return True

    def handle_log_viewer_key(self, key):
        """로그 뷰어에서 키 입력 처리"""
        needs_update = False

        if key == 27:  # ESC
            # 로그 파일을 보고 있는 경우, 현재 로그로 돌아가기
            if self.viewing_log_file:
                self.viewing_log_file = None
                self.log_selected_index = 0
                self.log_scroll_offset = 0
                self.log_viewer_first_time = True
                needs_update = True
            else:
                # 현재 로그를 보고 있는 경우, 로그 뷰어 종료
                self.toggle_log_viewer()
                return True

        # F키 - 로그 파일 목록 선택
        elif key == ord('f') or key == ord('F'):
            self.show_log_file_selector()
            return True

        # D키 - DEBUG 로그 전체/최신 5개 토글
        elif key == ord('d') or key == ord('D'):
            self.log_show_all_debug = not self.log_show_all_debug
            # 선택 인덱스 초기화 (필터링된 로그 개수가 변경되므로)
            self.log_selected_index = 0
            self.log_scroll_offset = 0
            needs_update = True

        else:
            # 현재 보고 있는 로그 가져오기 (파일 또는 메모리)
            if self.viewing_log_file:
                try:
                    with open(self.viewing_log_file, 'r', encoding='utf-8') as f:
                        current_logs = [line.rstrip('\n') for line in f]
                except Exception:
                    current_logs = self.logs
            else:
                current_logs = self.logs

            # 현재 필터링된 로그 개수 계산 (네비게이션에 사용)
            if self.log_show_all_debug:
                filtered_count = len(current_logs)
            else:
                debug_logs = [log for log in current_logs if "[DEBUG]" in log]
                if len(debug_logs) > 5:
                    old_debug_count = len(debug_logs) - 5
                    filtered_count = len(current_logs) - old_debug_count
                else:
                    filtered_count = len(current_logs)

            # 네비게이션 키들
            if key == curses.KEY_UP:
                if self.log_selected_index > 0:
                    self.log_selected_index -= 1
                    needs_update = True
            elif key == curses.KEY_DOWN:
                if self.log_selected_index < filtered_count - 1:
                    self.log_selected_index += 1
                    needs_update = True
            elif key == curses.KEY_HOME:
                self.log_selected_index = 0
                self.log_scroll_offset = 0
                needs_update = True
            elif key == curses.KEY_END:
                self.log_selected_index = max(0, filtered_count - 1)
                needs_update = True
            elif key == curses.KEY_PPAGE:  # Page Up
                self.log_selected_index = max(0, self.log_selected_index - 10)
                needs_update = True
            elif key == curses.KEY_NPAGE:  # Page Down
                self.log_selected_index = min(filtered_count - 1, self.log_selected_index + 10)
                needs_update = True
            elif key == curses.KEY_RESIZE or key == 410:  # Terminal resize
                # 터미널 크기 변경 감지 - 화면 다시 그리기 필요
                needs_update = True

        # 실제로 변경이 있을 때만 화면 갱신
        if needs_update:
            self.needs_redraw = True
        return True

    def handle_space(self):
        """Handle Space key - same as Enter for directory navigation"""
        self.handle_enter()

    def handle_backspace(self):
        """Handle Backspace key - navigate to parent directory"""
        if self.current_directory:
            # 상위 디렉토리로 이동
            parent_path = self.get_parent_directory(self.current_directory)
            self.current_directory = parent_path
            self.add_log(f"상위 디렉토리로 이동: {self.current_directory or 'root'}", "INFO")
            self.build_directory_view()
            self.selected_index = 0
            self.scroll_offset = 0
        else:
            # 이미 루트 디렉토리인 경우
            self.add_log("이미 루트 디렉토리입니다", "INFO")

    def run_download(self):
        """다운로드 실행"""
        self.add_log("DOWNLOAD 시작...", "INFO")
        try:
            # 백그라운드 실행을 위해 스레드 사용
            import threading
            def download_task():
                try:
                    self.workspace.download()
                    self.add_log("DOWNLOAD 완료", "INFO")
                    # Download 완료 후 파일 상태가 변경되므로 Full Refresh 필요
                    self.add_log("파일 상태 업데이트 중...", "INFO")
                    self.refresh_tree(full_refresh=True)
                    self.add_log("화면 새로고침 완료", "INFO")
                except Exception as e:
                    self.add_log(f"DOWNLOAD 실패: {e}", "ERROR")

            thread = threading.Thread(target=download_task)
            thread.daemon = True
            thread.start()

        except Exception as e:
            self.add_log(f"다운로드 실행 실패: {e}", "ERROR")

    def run_upload(self):
        """업로드 뷰어 열기"""
        self.add_log("업로드 대상 파일 확인 중...", "INFO")
        try:
            import threading
            def upload_check_task():
                try:
                    # 업로드 가능한 파일(Modified 상태) 및 충돌 파일 수집 (Git tracked 파일만)
                    files = self.workspace.collect_files_from_git(include_work_only=True)
                    upload_files = []
                    conflicted_files = []

                    for work_file, rel_path in files:
                        if rel_path == '.gitignore':  # .gitignore 제외
                            continue
                        production_file = os.path.join(self.workspace.production_dir, rel_path)
                        # work_file 경로를 명확히 working_dir 기준으로 설정
                        actual_work_file = os.path.join(self.workspace.working_dir, rel_path)
                        state = self.workspace.get_file_state(production_file, actual_work_file, rel_path)
                        if state == FileState.MODIFIED:
                            upload_files.append({
                                'rel_path': rel_path,
                                'work_file': actual_work_file,
                                'production_file': production_file,
                                'state': state
                            })
                        elif state == FileState.CONFLICTED:
                            conflicted_files.append(rel_path)

                    self.upload_files = upload_files

                    # 충돌 파일이 있으면 경고 메시지 표시
                    if conflicted_files:
                        self.add_log("충돌된 파일이 감지되었습니다:", "WARNING")
                        for file_path in conflicted_files:
                            self.add_log(f"  [충돌] {file_path}", "WARNING")
                        self.add_log("먼저 DOWNLOAD로 충돌을 해결한 후 업로드하세요.", "WARNING")
                    elif upload_files:
                        self.add_log(f"{len(upload_files)}개 업로드 가능한 파일 발견", "INFO")
                        self.upload_viewer_mode = True
                        self.upload_selected_index = 0
                        self.upload_scroll_offset = 0
                        self.needs_redraw = True
                    else:
                        self.add_log("업로드할 파일이 없습니다", "INFO")
                except Exception as e:
                    self.add_log(f"업로드 파일 확인 실패: {e}", "ERROR")

            thread = threading.Thread(target=upload_check_task)
            thread.daemon = True
            thread.start()

        except Exception as e:
            self.add_log(f"업로드 뷰어 실행 실패: {e}", "ERROR")

    def run_save(self):
        """저장 실행"""
        self.add_log("SAVE 시작...", "INFO")
        try:
            import threading
            def save_task():
                try:
                    self.workspace.save()
                    self.add_log("SAVE 완료", "INFO")
                    # Save 완료 후 Git 상태가 변경되므로 Full Refresh 필요
                    self.add_log("파일 상태 업데이트 중...", "INFO")
                    self.refresh_tree(full_refresh=True)
                    self.add_log("화면 새로고침 완료", "INFO")
                except Exception as e:
                    self.add_log(f"SAVE 실패: {e}", "ERROR")

            thread = threading.Thread(target=save_task)
            thread.daemon = True
            thread.start()

        except Exception as e:
            self.add_log(f"저장 실행 실패: {e}", "ERROR")

    def run_history(self):
        """히스토리 보기"""
        self.stdscr.clear() # greenfish : 화면 잔상 삭제
        self.add_log("HISTORY 조회...", "INFO")
        try:
            import threading
            def history_task():
                try:
                    if self.mode == ViewMode.WORK:
                        commits = GitHelper.get_git_log(self.workspace.working_dir, limit=100)
                        self.history_list = commits if commits else []
                    else:  # Production mode
                        # Production 히스토리도 조회 가능하도록 변경
                        commits = GitHelper.get_git_log(self.workspace.production_dir, limit=100)
                        self.history_list = commits if commits else []

                    if self.history_list:
                        # 원본 목록 저장 (필터링을 위해)
                        self.history_list_original = self.history_list[:]
                        self.add_log(f"{len(self.history_list)}개 커밋 로드됨", "INFO")
                        self.history_viewer_mode = True
                        self.history_selected_index = 0
                        self.history_scroll_offset = 0
                        self.history_detail_mode = False
                        # 히스토리 모드로 전환시 즉시 화면 갱신
                        self.needs_redraw = True
                    else:
                        self.add_log("히스토리가 없습니다", "INFO")
                except Exception as e:
                    self.add_log(f"히스토리 조회 실패: {e}", "ERROR")

            thread = threading.Thread(target=history_task)
            thread.daemon = True
            thread.start()

        except Exception as e:
            self.add_log(f"히스토리 조회 실패: {e}", "ERROR")

    def safe_run_external_program(self, func, *args, **kwargs):
        """curses 환경에서 안전하게 외부 프로그램 실행"""
        try:
            # 외부 프로그램 실행 (curses 상태 변경 없이)
            result = func(*args, **kwargs)

            # 외부 프로그램 실행 후 화면 새로고침만 요청
            self.needs_redraw = True

            return result
        except Exception as e:
            self.add_log(f"외부 프로그램 실행 실패: {e}", "ERROR")
            return False

    def run_vscode_diff_external(self, temp_production_file, work_file, rel_path):
        """VS Code diff를 외부 프로그램으로 실행 (curses 안전) - 향상된 경로 탐색"""
        try:
            import subprocess
            from ..utils.helpers import find_vscode_command

            # VS Code 명령어 찾기 (향상된 탐색 로직)
            vscode_cmd = find_vscode_command()

            if not vscode_cmd:
                self.add_log("VS Code를 찾을 수 없습니다. 다음을 확인하세요:", "ERROR")
                self.add_log("  1. config.ini에 [VSCODE] PATH=/path/to/code 설정", "ERROR")
                self.add_log("  2. 환경변수 VSCODE_PATH 설정", "ERROR")
                self.add_log("  3. code 또는 vscode 명령 설치 확인", "ERROR")
                return False

            self.add_log(f"VS Code 경로: {vscode_cmd}", "INFO")

            # VS Code diff 실행 (--new-window, --no-sandbox 옵션 포함)
            # --no-sandbox: 회사 환경에서 제대로 출력되도록 보장
            result = subprocess.run([
                vscode_cmd, '--no-sandbox', '--new-window', '--wait', '--diff',
                temp_production_file, work_file
            ], capture_output=False, text=True)

            return result.returncode == 0

        except Exception as e:
            self.add_log(f"VS Code diff 실행 오류: {e}", "ERROR")
            return False

    def check_command_exists(self, command):
        """명령어가 시스템에 존재하는지 확인 (deprecated - find_vscode_command 사용 권장)"""
        try:
            import subprocess
            subprocess.run(['which', command], capture_output=True, check=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def draw_upload_viewer(self, stdscr):
        """업로드 뷰어 그리기"""
        height, width = stdscr.getmaxyx()

        # 헤더 그리기 (재시도 로직 포함)
        for attempt in range(2):  # 최대 2번 시도
            try:
                stdscr.addstr(0, 0, "┌" + "─" * (width - 2) + "┐")
                header_text = f"업로드 가능한 파일 목록 ({len(self.upload_files)}개)"
                stdscr.addstr(1, 0, "│")
                stdscr.addstr(1, 1, " " + header_text[:width-4])
                stdscr.addstr(1, width-1, "│")
                stdscr.addstr(2, 0, "├" + "─" * (width - 2) + "┤")
                break
            except curses.error:
                if attempt == 0:
                    # 첫 번째 시도 실패시 화면 크기 재확인
                    height, width = stdscr.getmaxyx()
                    if height < 3 or width < 10:
                        break
                else:
                    # 두 번째 시도 실패시 안전한 대체
                    try:
                        stdscr.addstr(0, 0, "UPLOAD VIEWER")
                        stdscr.addstr(1, 0, f"{len(self.upload_files)} files")
                    except curses.error:
                        pass

        # 파일 영역 계산
        file_start_row = 3
        file_end_row = height - 3  # 도움말 영역 위까지
        visible_lines = file_end_row - file_start_row

        # 스크롤 처리
        if len(self.upload_files) > visible_lines:
            max_scroll_offset = len(self.upload_files) - visible_lines
            self.upload_scroll_offset = min(self.upload_scroll_offset, max_scroll_offset)
            self.upload_scroll_offset = max(0, self.upload_scroll_offset)

            if self.upload_selected_index < self.upload_scroll_offset:
                self.upload_scroll_offset = self.upload_selected_index
            elif self.upload_selected_index >= self.upload_scroll_offset + visible_lines:
                self.upload_scroll_offset = self.upload_selected_index - visible_lines + 1
        else:
            self.upload_scroll_offset = 0

        # 파일 목록 그리기
        for i in range(visible_lines):
            row = file_start_row + i
            file_index = self.upload_scroll_offset + i

            try:
                stdscr.addstr(row, 0, "│")
                stdscr.addstr(row, width-1, "│")

                if file_index < len(self.upload_files):
                    upload_file = self.upload_files[file_index]
                    line_text = f" [M] {upload_file['rel_path']}"

                    # 텍스트 길이 제한
                    max_text_width = width - 4
                    if len(line_text) > max_text_width:
                        line_text = line_text[:max_text_width-3] + "..."

                    # 선택된 항목 하이라이트
                    if file_index == self.upload_selected_index:
                        color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                        try:
                            safe_text = line_text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                            stdscr.addstr(row, 1, safe_text, color)
                        except curses.error:
                            pass
                    else:
                        try:
                            safe_text = line_text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                            stdscr.addstr(row, 1, safe_text)
                        except curses.error:
                            pass
                else:
                    # 빈 줄인 경우에도 중간 공간을 공백으로 채워서 │ 문자가 제대로 보이도록 함
                    try:
                        empty_line = " " * (width - 2)
                        stdscr.addstr(row, 1, empty_line)
                    except curses.error:
                        pass

            except curses.error:
                continue

        # 도움말 영역
        try:
            stdscr.addstr(height - 3, 0, "├" + "─" * (width - 2) + "┤")

            # 도움말 텍스트 라인 - character-by-character
            help_text = "↑↓:이동 │ Enter:Diff │ U:업로드 │ ESC:뒤로가기"
            help_line_chars = []

            # 왼쪽 테두리
            help_line_chars.append('│')

            # 공백
            help_line_chars.append(' ')

            # 도움말 텍스트 추가
            for ch in help_text:
                help_line_chars.append(ch)

            # 나머지를 공백으로 채우기
            help_remaining = width - 2 - len(help_text) - 1
            for _ in range(help_remaining):
                help_line_chars.append(' ')

            # 오른쪽 테두리
            help_line_chars.append('│')

            # 스크롤 정보를 배열에 직접 덮어쓰기
            if len(self.upload_files) > visible_lines:
                scroll_info = f"[{self.upload_selected_index + 1}/{len(self.upload_files)}]"
                scroll_pos = width - len(scroll_info) - 2
                if scroll_pos > 10:
                    # 스크롤 정보를 문자 배열에 직접 삽입
                    for i, ch in enumerate(scroll_info):
                        help_line_chars[scroll_pos + i] = ch

            # 각 문자를 개별적으로 쓰기
            for col, ch in enumerate(help_line_chars):
                if col < width:
                    stdscr.addch(height - 2, col, ch)

            # 최종 하단 테두리
            stdscr.addstr(height - 1, 0, "└" + "─" * (width - 2) + "┘")

        except curses.error:
            pass

    def handle_upload_viewer_key(self, key):
        """업로드 뷰어 키 처리"""
        if key == curses.KEY_UP:
            if self.upload_selected_index > 0:
                self.upload_selected_index -= 1
        elif key == curses.KEY_DOWN:
            if self.upload_selected_index < len(self.upload_files) - 1:
                self.upload_selected_index += 1
        elif key == curses.KEY_HOME:
            self.upload_selected_index = 0
        elif key == curses.KEY_END:
            self.upload_selected_index = max(0, len(self.upload_files) - 1)
        elif key == curses.KEY_PPAGE:  # Page Up
            self.upload_selected_index = max(0, self.upload_selected_index - 10)
        elif key == curses.KEY_NPAGE:  # Page Down
            self.upload_selected_index = min(len(self.upload_files) - 1, self.upload_selected_index + 10)
        elif key == ord('\n') or key == 10 or key == 13 or key == curses.KEY_ENTER:  # Enter - Diff
            self.run_upload_file_diff(self.upload_selected_index)
        elif key == ord('u') or key == ord('U'):  # U key - Upload
            self.confirm_upload()

        return True

    def run_upload_file_diff(self, file_index):
        """업로드 파일에 대해 Production과의 diff 실행"""
        if file_index >= len(self.upload_files):
            return

        try:
            upload_file = self.upload_files[file_index]
            work_file = upload_file['work_file']
            production_file = upload_file['production_file']
            rel_path = upload_file['rel_path']

            self.add_log(f"Diff 실행: {rel_path}", "INFO")
            self.add_log(f"Work 파일: {work_file}", "INFO")
            self.add_log(f"Production 파일: {production_file}", "INFO")

            import tempfile
            import shutil

            # 임시 파일 생성 (Production 버전)
            temp_production_file = None
            try:
                if os.path.exists(production_file):
                    with tempfile.NamedTemporaryFile(mode='w', suffix=f"_prod_{os.path.basename(rel_path)}", delete=False) as temp_file:
                        temp_production_file = temp_file.name
                        shutil.copy2(production_file, temp_production_file)
                else:
                    # 새 파일인 경우 빈 임시 파일 생성
                    with tempfile.NamedTemporaryFile(mode='w', suffix=f"_prod_{os.path.basename(rel_path)}", delete=False) as temp_file:
                        temp_production_file = temp_file.name
                        temp_file.write("")

                # curses 환경에서 안전하게 외부 프로그램 실행
                success = self.safe_run_external_program(
                    self.run_vscode_diff_external,
                    temp_production_file, work_file, rel_path
                )

                if success:
                    self.add_log(f"Diff 완료: {rel_path}", "INFO")
                else:
                    self.add_log(f"Diff 실행 실패: {rel_path}", "ERROR")

            finally:
                # 임시 파일 정리
                if temp_production_file and os.path.exists(temp_production_file):
                    try:
                        os.unlink(temp_production_file)
                    except OSError:
                        pass

        except Exception as e:
            self.add_log(f"Diff 실행 실패: {e}", "ERROR")

    def confirm_upload(self):
        """업로드 확인 및 실행"""
        if not self.upload_files:
            self.add_log("업로드할 파일이 없습니다", "INFO")
            return

        # 커밋 메시지 입력받기
        message = self.get_commit_message()
        if not message:
            self.add_log("업로드가 취소되었습니다", "INFO")
            return

        self.add_log("업로드 시작...", "INFO")
        try:
            import threading
            def upload_task():
                try:
                    # 임시로 upload 메시지를 설정하는 방법이 필요하지만,
                    # 현재는 기존 upload 메서드를 그대로 사용
                    self.workspace.upload()
                    self.add_log("업로드 완료", "INFO")
                    # Upload 완료 후 Full Refresh
                    self.upload_viewer_mode = False
                    self.needs_redraw = True
                    self.refresh_tree(full_refresh=True)
                except Exception as e:
                    self.add_log(f"업로드 실패: {e}", "ERROR")

            thread = threading.Thread(target=upload_task)
            thread.daemon = True
            thread.start()

        except Exception as e:
            self.add_log(f"업로드 실행 실패: {e}", "ERROR")

    def get_commit_message(self):
        """커밋 메시지 입력받기 (간단한 구현)"""
        # TODO: 더 나은 입력 다이얼로그 구현 필요
        # 현재는 기본 메시지 사용
        return f"Upload {len(self.upload_files)} modified files via TUI"

    def handle_key(self, key):
        """키 입력 처리 - 모든 키를 동일하게 처리"""

        # 대화상자가 활성화된 동안에는 모든 키 입력 무시
        if self.dialog_active:
            self.add_log(f"Main: Ignoring key {key} during dialog", "DEBUG")
            return True

        # 도움말 뷰어 모드일 때 키 처리
        if self.help_viewer_mode:
            if key == 27:  # ESC
                self.help_viewer_mode = False
                # 도움말 뷰어에서 메인으로 돌아갈 때 강제 새로고침
                self.force_refresh_screen()
                self.needs_redraw = True
                return True
            elif key == ord('q') or key == ord('Q'):
                self.add_log("Exit key pressed", "INFO")
                return False
            else:
                return self.handle_help_viewer_key(key)

        # 히스토리 뷰어 모드일 때는 ESC를 제외한 모든 키를 히스토리 뷰어에서 처리
        if self.history_viewer_mode:
            # ESC 키는 히스토리 뷰어 종료 또는 프로그램 종료
            if key == 27:  # ESC
                if self.history_detail_mode:
                    self.history_detail_mode = False
                else:
                    self.history_viewer_mode = False
                    # 히스토리 뷰어에서 메인으로 돌아갈 때 강제 새로고침
                    self.force_refresh_screen()
                self.needs_redraw = True
                return True
            # Q 키는 프로그램 종료
            elif key == ord('q') or key == ord('Q'):
                self.add_log("Exit key pressed", "INFO")
                return False
            # 다른 모든 키는 히스토리 뷰어에서 처리
            else:
                return self.handle_history_viewer_key(key)

        # 업로드 뷰어 모드일 때는 ESC를 제외한 모든 키를 업로드 뷰어에서 처리
        elif self.upload_viewer_mode:
            # ESC 키는 업로드 뷰어 종료 또는 프로그램 종료
            if key == 27:  # ESC
                self.upload_viewer_mode = False
                self.needs_redraw = True
                return True
            # Q 키는 프로그램 종료
            elif key == ord('q') or key == ord('Q'):
                self.add_log("Exit key pressed", "INFO")
                return False
            # 다른 모든 키는 업로드 뷰어에서 처리
            else:
                return self.handle_upload_viewer_key(key)

        # App 뷰어 모드일 때는 ESC를 제외한 모든 키를 App 뷰어에서 처리
        elif self.app_viewer_mode:
            # ESC 키는 App 뷰어 종료 또는 프로그램 종료
            if key == 27:  # ESC
                self.app_viewer_mode = False
                self.needs_redraw = True
                return True
            # Q 키는 프로그램 종료
            elif key == ord('q') or key == ord('Q'):
                self.add_log("Exit key pressed", "INFO")
                return False
            # 다른 모든 키는 App 뷰어에서 처리
            else:
                return self.handle_app_viewer_key(key)

        # 로그 뷰어 모드일 때는 ESC를 제외한 모든 키를 로그 뷰어에서 처리
        elif self.log_viewer_mode:
            # ESC 키는 로그 뷰어 종료 또는 프로그램 종료
            if key == 27:  # ESC
                self.toggle_log_viewer()
                return True
            # Q 키는 프로그램 종료
            elif key == ord('q') or key == ord('Q'):
                self.add_log("Exit key pressed", "INFO")
                return False
            # 다른 모든 키는 로그 뷰어에서 처리
            else:
                return self.handle_log_viewer_key(key)

        # 일반 모드에서 종료 키들 (최우선 처리)
        if key == ord('q') or key == ord('Q'):  # q, Q만 종료 (ESC 제거)
            self.add_log("Exit key pressed", "INFO")
            return False  # 즉시 종료

        # 키 디버깅 로그 제거 (노이즈 방지)

        # 튜토리얼 모드에서 좌우 키 및 Enter 키 처리
        if self.tutorial_enabled and self.tutorial_step < len(self.tutorial_steps):
            if key == curses.KEY_LEFT:
                # 이전 단계로
                if self.tutorial_step > 0:
                    self.tutorial_step -= 1
                    self.add_log(f"튜토리얼 {self.tutorial_step + 1}/{len(self.tutorial_steps)} 단계", "INFO")
                return True
            elif key == curses.KEY_RIGHT:
                # 다음 단계로
                self.tutorial_step += 1
                if self.tutorial_step >= len(self.tutorial_steps):
                    self.add_log("튜토리얼 완료!", "INFO")
                else:
                    self.add_log(f"튜토리얼 {self.tutorial_step + 1}/{len(self.tutorial_steps)} 단계", "INFO")
                return True
            elif key == ord('\n') or key == 10 or key == 13 or key == curses.KEY_ENTER:
                # 마지막 단계에서 Enter 누르면 TUTORIAL.STARTUP_SHOW=OFF 저장
                if self.tutorial_step == len(self.tutorial_steps) - 1:
                    self.add_log("튜토리얼을 더이상 시작시 표시하지 않습니다.", "INFO")
                    self.preference.set('', 'TUTORIAL.STARTUP_SHOW', 'OFF')
                    self.preference.save()
                    # 튜토리얼 종료
                    self.tutorial_enabled = False
                    self.tutorial_step = 0
                    self.needs_redraw = True
                    return True

        # 네비게이션 키들
        if key == curses.KEY_UP:
            if self.selected_index > 0:
                self.selected_index -= 1
        elif key == curses.KEY_DOWN:
            if self.selected_index < len(self.directory_entries) - 1:
                self.selected_index += 1
        elif key == curses.KEY_LEFT:
            # 트리 뷰 모드에서는 collapse, detail 모드에서는 동작 없음
            if self.view_style == ViewStyle.TREE:
                self.handle_tree_collapse()
        elif key == curses.KEY_RIGHT:
            # 트리 뷰 모드에서는 expand, detail 모드에서는 동작 없음
            if self.view_style == ViewStyle.TREE:
                self.handle_tree_expand()
        elif key == curses.KEY_HOME:
            self.selected_index = 0
        elif key == curses.KEY_END:
            self.selected_index = max(0, len(self.directory_entries) - 1)
        elif key == curses.KEY_NPAGE:  # Page Down
            self.selected_index = min(len(self.directory_entries) - 1,
                                    self.selected_index + 10)
        elif key == curses.KEY_PPAGE:  # Page Up
            self.selected_index = max(0, self.selected_index - 10)

        # 기능 키들
        if key == ord('m') or key == ord('M'):
            self.toggle_mode()
        elif key == ord('d') or key == ord('D'):
            self.run_download()
        elif key == ord('u') or key == ord('U'):
            self.run_upload()
        elif key == ord('s') or key == ord('S'):
            self.run_save()
        elif key == ord('h') or key == ord('H'):
            # 히스토리 로딩 중 상태 즉시 표시
            self.add_log("히스토리 로딩 중...", "INFO")
            self.history_viewer_mode = True
            self.history_list = []  # 임시로 빈 리스트
            self.needs_redraw = True
            self.run_history()
        elif key == ord('p') or key == ord('P'):
            self.run_project_management()
        elif key == ord('t') or key == ord('T'):
            self.launch_terminal_at_current_dir()
        elif key == ord('a') or key == ord('A'):
            self.open_app_viewer()
        elif key == ord('v') or key == ord('V'):
            self.toggle_view_style()
        elif key == ord('l') or key == ord('L'):
            self.toggle_log_viewer()
        elif key == ord('r') or key == ord('R') or key == 410:  # R or resize
            self.refresh_tree()
        elif key == curses.KEY_F5 or key == 269:  # F5 - 강제 화면 새로고침
            self.force_refresh_screen()
        elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:  # BACKSPACE
            self.handle_backspace()
        elif key == curses.KEY_F2 or key == 266:  # F2 key - 도움말
            self.show_help()
        elif key == curses.KEY_F9 or key == 273:  # F9 key - 튜토리얼
            self.add_log("F9 key detected - starting tutorial", "DEBUG")
            self.start_tutorial(force=True)  # F9는 설정 무시하고 무조건 실행
        elif key == 27:  # ESC 키 - ALT+키 시퀀스일 수 있음
            # ALT+키는 많은 터미널에서 ESC + 키로 전달됨
            # nodelay 모드로 다음 키를 빠르게 확인
            self.stdscr.nodelay(True)
            next_key = self.stdscr.getch()
            self.stdscr.nodelay(False)

            if next_key == ord('p') or next_key == ord('P'):  # ALT+P
                self.open_preference_editor()
            else:
                # 일반 ESC 키 동작 (다른 모드들에서 처리)
                # next_key가 -1이면 단순 ESC, 그 외는 시퀀스 무시
                pass
        elif key == 224 or (key >= 128 and chr(key) == 'p'):  # ALT+P - 다른 터미널 환경
            # 일부 터미널에서는 224나 다른 값으로 전달될 수 있음
            self.open_preference_editor()

        # 인터랙션 키들
        elif key == ord(' '):  # Space
            self.handle_space()
        elif key == ord('\n') or key == 10 or key == 13 or key == curses.KEY_ENTER:  # Enter (다양한 값 지원)
            self.handle_enter()
        else:
            # Unhandled key - 디버깅을 위해 특정 키들만 로그
            if key in [13, 10, 343]:  # Enter 관련 키들만 로그
                self.add_log(f"Unhandled Enter-like key: {key}", "DEBUG")
            # 다른 키는 로그 출력하지 않음 (노이즈 방지)

        return True  # 계속 실행

    def main_loop(self, stdscr):
        """메인 루프"""
        # stdscr을 인스턴스 변수로 저장
        self.stdscr = stdscr

        try:
            # 기본 설정
            curses.curs_set(0)  # 커서 숨기기
            stdscr.keypad(1)  # 특수 키 활성화 (필수!)
            stdscr.timeout(100)  # 짧은 타임아웃으로 빠른 반응

            # 키 입력 설정
            curses.noecho()  # 입력 에코 방지
            curses.cbreak()  # 즉시 입력 처리
            curses.flushinp()  # 입력 버퍼 클리어

            # 색상 초기화
            self.init_colors()

            self.add_log("터미널 인터페이스 초기화 완료", "INFO")
        except Exception as e:
            self.add_log(f"curses 초기화 오류: {e}", "ERROR")

        # 화면 상태 추적을 위한 변수
        self.needs_redraw = True

        # 프로젝트 선택이 필요한지 확인 및 처리
        if self.workspace.needs_project_selection():
            # 프로젝트 선택이 필요한 경우: 초기 화면 그리지 않고 바로 다이얼로그 표시
            stdscr.erase()
            stdscr.refresh()
            self.add_log("신규 프로젝트 생성이 필요합니다", "INFO")
            try:
                # 첫 실행 시: 바로 "신규 프로젝트 생성" 다이얼로그로 시작 (is_initial_setup=True)
                if self.show_new_project_creation_dialog(is_initial_setup=True):
                    # 프로젝트 생성 완료 - 트리 구축
                    self.refresh_tree()
                    self.add_log(f"프로젝트 설정 완료: {self.workspace.get_current_project_name()}", "INFO")
                    self.needs_redraw = True
                else:
                    # 첫 실행 시 ESC → 프로그램 종료
                    self.add_log("신규 프로젝트 생성이 취소되었습니다. 프로그램을 종료합니다.", "WARN")
                    self.cleanup()
                    return
            except Exception as e:
                self.add_log(f"프로젝트 설정 중 오류: {e}", "ERROR")
                self.cleanup()
                return
        else:
            # 프로젝트가 이미 선택되어 있는 경우: 초기 화면 그리기
            try:
                self.add_log("CCCopy TUI 시작 - 로딩 중...", "INFO")
                stdscr.erase()
                self.draw_header(stdscr)
                self.draw_path(stdscr)
                self.draw_file_list(stdscr)  # 빈 리스트라도 표시
                self.draw_commands(stdscr)
                self.draw_tutorial(stdscr)  # 튜토리얼 오버레이
                self.draw_logs(stdscr)
                stdscr.noutrefresh()
                curses.doupdate()
                self.needs_redraw = False
            except Exception as e:
                self.add_log(f"Initial screen draw failed: {e}", "ERROR")

            # 초기 트리 구축 (Partial Refresh - 즉시 반환)
            try:
                self.refresh_tree(full_refresh=False)
                self.add_log("파일 목록 로드 완료", "INFO")
            except Exception as e:
                # 트리 구축 실패시 curses 종료하고 텍스트 모드로
                curses.endwin()
                print(f"트리 구축 실패: {e}")
                print("텍스트 모드로 전환합니다...")
                self.run_simple_tui()
                return

        # Watch thread 시작 (파일 변화 감지)
        self.start_watch_thread()

        # 앱 시작 시 운세 표시 (다이얼로그)
        self._show_startup_fortune()

        while True:
            try:
                # 화면 크기 확인
                height, width = stdscr.getmaxyx()
                if height < 15 or width < 60:
                    stdscr.erase()
                    stdscr.addstr(0, 0, "터미널 크기가 너무 작습니다. (최소 60x15)")
                    stdscr.addstr(1, 0, "Q 키를 누르면 종료됩니다.")
                    stdscr.noutrefresh()
                    curses.doupdate()
                    key = stdscr.getch()
                    if key == ord('q') or key == ord('Q'):
                        self.cleanup()
                        break
                    continue

                # Pending updates 적용 (thread에서 완료된 상태 업데이트)
                # 단, 도움말/히스토리/업로드/로그/앱 뷰어 모드에서는 무시 (깜박임 방지)
                if not (self.help_viewer_mode or self.history_viewer_mode or
                        self.upload_viewer_mode or self.log_viewer_mode or
                        self.app_viewer_mode):
                    if self.apply_pending_updates():
                        self.needs_redraw = True

                # 자동 refresh 체크 (Watch thread에서 파일 변경 감지시)
                if self.needs_auto_refresh:
                    with self.refresh_lock:
                        self.needs_auto_refresh = False
                    # Partial Refresh로 변경 (변경된 파일의 캐시만 클리어됨, Thread 기반 비동기)
                    self.refresh_tree(full_refresh=False)

                # 키 입력 처리
                if not self.dialog_active:
                    key = stdscr.getch()  # 블로킹: 키 입력시에만 반환
                    if key != -1:  # 실제 키 입력이 있는 경우
                        try:
                            # 키 처리 결과 확인
                            continue_running = self.handle_key(key)
                            if not continue_running:
                                self.add_log("Exiting TUI...", "INFO")
                                # Graceful shutdown: 모든 thread 및 리소스 정리
                                self.cleanup()
                                break
                            # 키 입력 후 버퍼 정리
                            curses.flushinp()  # 잔여 입력 버퍼 정리
                            # 뷰어 모드가 아닐 때만 화면 갱신 (뷰어는 자체 redraw 관리)
                            if not (self.help_viewer_mode or self.history_viewer_mode or
                                    self.upload_viewer_mode or self.log_viewer_mode):
                                self.needs_redraw = True
                        except Exception as e:
                            self.add_log(f"Key handling error: {e}", "ERROR")
                            self.needs_redraw = True

                # 화면 갱신이 필요한 경우에만 그리기 (Double Buffering 적용)
                if not self.dialog_active and self.needs_redraw:
                    try:
                        # Double Buffering: 백버퍼에 그리기
                        stdscr.erase()  # clear 대신 erase 사용으로 더 안전한 클리어

                        # 화면 모드에 따른 그리기
                        if self.help_viewer_mode:
                            # 도움말 뷰어 모드
                            self.draw_help_viewer(stdscr)
                        elif self.upload_viewer_mode:
                            # 업로드 뷰어 모드
                            self.draw_upload_viewer(stdscr)
                        elif self.app_viewer_mode:
                            # App 뷰어 모드
                            self.draw_app_viewer(stdscr)
                        elif self.history_viewer_mode:
                            # 히스토리 뷰어 모드
                            if self.history_detail_mode:
                                self.draw_history_detail_viewer(stdscr)
                            else:
                                self.draw_history_viewer(stdscr)
                        elif self.log_viewer_mode:
                            # 로그 뷰어 모드
                            self.draw_log_viewer(stdscr)
                        else:
                            # 일반 화면 구성 요소 그리기
                            self.draw_header(stdscr)
                            self.draw_path(stdscr)
                            self.draw_file_list(stdscr)
                            self.draw_commands(stdscr)
                            self.draw_tutorial(stdscr)  # 튜토리얼 오버레이
                            self.draw_logs(stdscr)

                        # Double Buffering: 백버퍼를 가상 스크린에 준비
                        stdscr.noutrefresh()

                        # 원자적 화면 업데이트: 모든 변경사항을 한번에 터미널에 반영
                        curses.doupdate()

                        self.needs_redraw = False

                    except Exception as e:
                        # 그리기 오류시 에러 메시지 표시 (Double Buffering 적용)
                        stdscr.erase()
                        stdscr.addstr(0, 0, f"화면 그리기 오류: {str(e)[:50]}")
                        stdscr.addstr(1, 0, "Q 키를 누르면 종료됩니다.")
                        stdscr.noutrefresh()
                        curses.doupdate()
                        key = stdscr.getch()
                        if key == ord('q') or key == ord('Q'):
                            self.cleanup()
                            break
                        self.needs_redraw = True
                        continue

                else:
                    # 대화상자가 활성화된 동안에는 최소한의 CPU 사용률 유지
                    import time
                    time.sleep(0.01)  # 대화상자 상태 체크를 위한 최소 대기
                    # 대화상자 종료 감지 시 강제 새로고침
                    if not self.dialog_active:
                        self.needs_redraw = True

            except KeyboardInterrupt:
                # Ctrl+C 종료시에도 cleanup
                self.add_log("Interrupted by user (Ctrl+C)", "INFO")
                self.cleanup()
                break
            except Exception as e:
                # 전체 루프 오류시 cleanup 후 안전 종료
                self.add_log(f"Fatal error in main loop: {e}", "ERROR")
                self.cleanup()
                try:
                    curses.endwin()
                except:
                    pass
                print(f"Curses 루프 오류: {e}")
                print("텍스트 모드로 전환합니다...")
                self.run_simple_tui()
                return

    def run_simple_tui(self):
        """간단한 텍스트 기반 TUI (curses 대안) - ANSI 색상 지원"""
        # ANSI 색상 코드 정의
        self.ansi_colors = {
            'reset': '\033[0m',
            'bold': '\033[1m',
            'red': '\033[31m',
            'green': '\033[32m',
            'yellow': '\033[33m',
            'blue': '\033[34m',
            'cyan': '\033[36m',
            'white': '\033[37m',
            'bg_white': '\033[47m',
            'bg_black': '\033[40m'
        }

        print("\n" + "="*60)
        print(f"{self.ansi_colors['bold']}CCCopy TUI - 텍스트 모드 (ANSI 색상 지원){self.ansi_colors['reset']}")
        print("="*60)

        # 프로젝트 선택이 필요한지 확인
        if self.workspace.needs_project_selection():
            self.add_log("신규 프로젝트 생성이 필요합니다", "INFO")
            # 텍스트 모드: ProjectSelectionManager 사용
            from ..utils.config import ProjectSelectionManager
            project_manager = ProjectSelectionManager(self.workspace)
            project_manager.show_project_management_menu()

            # 프로젝트 선택 완료 여부 확인
            if not self.workspace.needs_project_selection():
                self.add_log(f"프로젝트 설정 완료: {self.workspace.get_current_project_name()}", "INFO")
            else:
                self.add_log("프로젝트 설정이 취소되었습니다", "WARN")
                return

        # 초기 트리 구축
        self.refresh_tree()

        # 앱 시작 시 운세 표시 (텍스트 모드에서도)
        self._show_startup_fortune()

    def get_ansi_color_for_state(self, state):
        """파일 상태에 따른 ANSI 색상 반환"""
        if not hasattr(self, 'ansi_colors'):
            return ""

        color_map = {
            FileState.MODIFIED: self.ansi_colors['yellow'] + self.ansi_colors['bold'],  # 노란색 + 볼드
            FileState.SAME: self.ansi_colors['green'],      # 초록색
            FileState.UPDATED: self.ansi_colors['cyan'],    # 청록색
            FileState.CONFLICTED: self.ansi_colors['red'] + self.ansi_colors['bold'], # 빨간색 + 볼드
        }
        return color_map.get(state, "")

    def get_ansi_color_for_folder(self):
        """폴더용 ANSI 색상 반환"""
        if not hasattr(self, 'ansi_colors'):
            return ""
        return self.ansi_colors['blue'] + self.ansi_colors['bold']  # 파란색 + 볼드

    def get_ansi_color_for_log(self, log_line):
        """로그 레벨에 따른 ANSI 색상 적용"""
        if not hasattr(self, 'ansi_colors'):
            return log_line

        # 로그 레벨 추출
        if '[INFO ]' in log_line:
            # INFO는 녹색
            return log_line.replace('[INFO ]', f"{self.ansi_colors['green']}{self.ansi_colors['bold']}[INFO ]{self.ansi_colors['reset']}")
        elif '[WARN ]' in log_line:
            # WARNING은 노란색
            return log_line.replace('[WARN ]', f"{self.ansi_colors['yellow']}{self.ansi_colors['bold']}[WARN ]{self.ansi_colors['reset']}")
        elif '[ERROR]' in log_line:
            # ERROR는 빨간색
            return log_line.replace('[ERROR]', f"{self.ansi_colors['red']}{self.ansi_colors['bold']}[ERROR]{self.ansi_colors['reset']}")
        elif '[DEBUG]' in log_line:
            # DEBUG는 회색 (어둡게)
            return log_line.replace('[DEBUG]', f"\033[2m[DEBUG]{self.ansi_colors['reset']}")
        elif '[HIGH ]' in log_line:
            # HIGH는 CYAN (청록색)
            return log_line.replace('[HIGH ]', f"\033[1;36m[HIGH ]{self.ansi_colors['reset']}")

        return log_line

    def show_full_logs(self):
        """전체 로그 표시 (텍스트 모드용)"""
        print(f"\n=== 전체 로그 ({len(self.logs)}개) ===")

        if not self.logs:
            print("로그가 없습니다.")
            return

        # 모든 로그 표시 (색상 적용)
        for i, log in enumerate(self.logs, 1):
            colored_log = self.get_ansi_color_for_log(log)
            print(f"{i:3}. {colored_log}")

        print(f"\n총 {len(self.logs)}개 로그 항목")
        input("\nEnter 키를 누르면 메인 메뉴로 돌아갑니다...")

    def show_cli_help(self):
        """CLI 모드용 도움말 표시"""
        help_content = self.get_help_content()

        print("\n" + "=" * 60)
        for line in help_content:
            print(line)
        print("=" * 60)
        input("\nEnter 키를 누르면 메인 메뉴로 돌아갑니다...")

    def launch_terminal_at_current_dir(self):
        """현재 디렉토리에서 터미널 열기"""
        try:
            from ..utils.helpers import launch_terminal

            if self.mode == ViewMode.WORK:
                base_path = self.workspace.working_dir
            else:
                base_path = self.workspace.production_dir

            # 현재 디렉토리 경로 구성
            if self.current_directory:
                target_path = os.path.join(base_path, self.current_directory)
            else:
                target_path = base_path

            self.add_log(f"터미널 실행 요청: {target_path}", "INFO")

            # 터미널 실행
            success = launch_terminal(target_path)

            if success:
                self.add_log(f"터미널이 성공적으로 실행되었습니다", "INFO")
            else:
                self.add_log(f"터미널 실행에 실패했습니다", "ERROR")

        except Exception as e:
            self.add_log(f"터미널 실행 중 오류: {e}", "ERROR")

    def run_project_management(self):
        """프로젝트 관리 실행"""
        try:
            # TUI 모드 확인 - stdscr 존재 여부로 curses 모드 감지
            has_stdscr = hasattr(self, 'stdscr') and self.stdscr is not None
            is_text_mode = os.environ.get('CCCOPY_FORCE_TEXT_MODE') == '1'

            is_curses_mode = has_stdscr and not is_text_mode

            self.add_log(f"프로젝트 관리 모드 감지: Curses={is_curses_mode}, stdscr={has_stdscr}, text_mode={is_text_mode}", "DEBUG")

            if is_curses_mode:
                # Curses 모드에서는 dialog 사용
                self.add_log("Curses 다이얼로그 모드로 실행", "DEBUG")
                self.show_project_management_dialog()
            else:
                # 텍스트 모드에서는 간단한 인라인 프로젝트 관리
                self.add_log("텍스트 모드에서 간단한 프로젝트 관리 실행", "DEBUG")
                self.show_simple_project_management()

            # 프로젝트 변경이 있을 수 있으므로 강제 화면 새로고침
            self.force_refresh_screen()
            self.add_log("프로젝트 관리 완료", "INFO")

        except Exception as e:
            self.add_log(f"프로젝트 관리 중 오류: {e}", "ERROR")
            # Curses 환경에서는 traceback.print_exc() 사용하지 않음 (화면 깨짐 방지)

    def show_project_management_dialog(self):
        """프로젝트 관리 메인 다이얼로그"""
        menu_items = [
            "신규 프로젝트 생성",
            "프로젝트 목록"
        ]

        while True:
            selected = self.show_menu_dialog("프로젝트 관리", menu_items)

            if selected == -1:  # ESC 또는 취소
                break
            elif selected == 0:  # 신규 프로젝트 생성
                if self.show_new_project_creation_dialog():
                    break  # 성공시 메인으로
            elif selected == 1:  # 프로젝트 목록
                if self.show_project_switching_dialog():
                    break  # 성공시 메인으로

    def show_new_project_creation_dialog(self, is_initial_setup=False):
        """신규 프로젝트 생성 다이얼로그

        Args:
            is_initial_setup: True이면 첫 실행 시 호출 (ESC 시 종료)
                            False이면 [P]roject 키로 호출 (ESC 시 메뉴로 복귀)
        """
        # 사용 가능한 템플릿 목록
        template_projects = list(self.workspace.project_configs.keys())

        if not template_projects:
            self.show_error_dialog("사용 가능한 프로젝트 템플릿이 없습니다.\nproject/ 디렉토리에 *.ini 파일을 확인하세요.")
            return False

        # 첫 실행 시 환영 메시지 표시
        if is_initial_setup and hasattr(self, 'stdscr') and self.stdscr:
            stdscr = self.stdscr
            height, width = stdscr.getmaxyx()
            welcome_msg = "CCCopy 첫 실행을 환영합니다."
            welcome_width = self.get_display_width(welcome_msg)
            welcome_x = max(0, (width - welcome_width) // 2)
            welcome_y = max(0, (height // 2) - 5)  # 다이얼로그 위에 표시

            try:
                import curses
                # 노란색 (볼드) 속성 사용
                if curses.has_colors():
                    stdscr.addstr(welcome_y, welcome_x, welcome_msg, curses.color_pair(3) | curses.A_BOLD)
                else:
                    stdscr.addstr(welcome_y, welcome_x, welcome_msg, curses.A_BOLD)
                stdscr.refresh()
            except:
                pass  # 화면 크기 문제 등 무시

        # 템플릿 선택
        help_msg = "↑↓: 선택, Enter: 확인, ESC: " + ("종료" if is_initial_setup else "취소")
        selected_idx = self.show_menu_dialog("신규 프로젝트 생성 - 템플릿 선택", template_projects, help_msg)

        if selected_idx == -1:  # 취소
            return False

        selected_template = template_projects[selected_idx]

        # 템플릿의 WORKING_BASE_DIR 기본값 가져오기
        default_working_dir = ""
        try:
            template_info = self.workspace.get_project_info(selected_template)
            if template_info and 'working_base_dir' in template_info:
                base_dir = template_info['working_base_dir']

                # 중복 경로 체크 및 고유한 경로 생성
                from ..utils.config import ProjectSelectionManager
                project_manager = ProjectSelectionManager(self.workspace)

                if not project_manager._is_path_already_used(base_dir):
                    # 중복 없음 - 그대로 사용
                    default_working_dir = base_dir
                else:
                    # 중복 있음 - _1, _2, ... 추가
                    counter = 1
                    while True:
                        candidate_dir = f"{base_dir}_{counter}"
                        if not project_manager._is_path_already_used(candidate_dir):
                            default_working_dir = candidate_dir
                            break
                        counter += 1
                        if counter > 100:  # 무한 루프 방지
                            default_working_dir = base_dir
                            break
        except Exception as e:
            self.add_log(f"템플릿 기본 경로 조회 실패: {e}", "DEBUG")

        # 작업 디렉토리 경로 입력
        while True:
            custom_dir = self.show_input_dialog(
                "작업 디렉토리 경로 입력",
                f"{selected_template} 프로젝트의 작업 디렉토리 경로를 입력하세요.\n(예: ~/work/my_work)",
                default_working_dir  # 템플릿의 WORKING_BASE_DIR (중복 시 _1, _2 등 추가)
            )

            if not custom_dir:  # 취소 또는 빈 입력
                return False

            # 중복 경로 체크
            from ..utils.config import ProjectSelectionManager

            project_manager = ProjectSelectionManager(self.workspace)
            if project_manager._is_path_already_used(custom_dir):
                self.show_error_dialog(f"이미 등록된 경로입니다:\n{custom_dir}\n\n다른 경로를 입력하세요.")
                continue

            # TAG 입력 받기
            tag = self.show_input_dialog(
                "TAG 입력 (옵션)",
                f"{selected_template} 프로젝트의 TAG를 입력하세요.\n빈 입력시 TAG 없이 생성됩니다.",
                ""
            )

            if tag is None:  # 취소
                return False

            # 프로젝트 추가 설정 변경
            use_custom_settings = False
            temp_ini_file = None

            while True:
                choice = self.show_choice_dialog(
                    "프로젝트 추가 설정 변경",
                    ["템플릿 기본값 사용 (권장)", "변경 진행 (SOURCES 편집)", "취소"]
                )

                if choice == -1 or choice == 2:  # ESC 또는 취소
                    # 작업 디렉토리 입력으로 돌아감
                    break
                elif choice == 0:  # 템플릿 기본값 사용
                    self.add_log("템플릿 기본값을 사용합니다.", "INFO")
                    break
                elif choice == 1:  # 변경 진행 - SOURCES 편집
                    from ..utils.config import ProjectSelectionManager
                    from ..utils.helpers import launch_text_editor
                    project_manager = ProjectSelectionManager(self.workspace)

                    # 임시 파일 생성
                    temp_ini_file = project_manager._create_sources_edit_file(selected_template)
                    if temp_ini_file:
                        # 텍스트 에디터 실행
                        if launch_text_editor(temp_ini_file):
                            use_custom_settings = True
                            self.add_log("SOURCES 편집이 완료되었습니다.", "INFO")
                            self.show_info_dialog("SOURCES 편집이 완료되었습니다.")
                            break
                        else:
                            self.show_error_dialog("텍스트 에디터 실행에 실패했습니다.")
                            if temp_ini_file and os.path.exists(temp_ini_file):
                                os.remove(temp_ini_file)
                            temp_ini_file = None
                            # 다시 메뉴로 돌아감
                    else:
                        self.show_error_dialog("임시 파일 생성에 실패했습니다.")
                        # 다시 메뉴로 돌아감

            # 뒤로가기(취소)를 선택한 경우 작업 디렉토리 입력으로 돌아감
            if choice == -1 or choice == 2:
                continue

            # 프로젝트 생성
            try:
                self.workspace.select_project_and_setup(
                    selected_template,
                    custom_dir,
                    tag,
                    temp_ini_file if use_custom_settings else None
                )

                # 임시 파일 정리
                if temp_ini_file and os.path.exists(temp_ini_file):
                    os.remove(temp_ini_file)

                # 프로젝트 생성시 캐시 초기화
                self.tracked_files_cache = None
                self.tracked_files_cache_time = 0
                self.file_state_cache.clear()
                self.workspace.last_production_check_time = 0  # Production 체크 시간도 초기화
                self.add_log("프로젝트 생성으로 캐시 초기화됨", "DEBUG")

                # 프로젝트 생성 후 트리 새로고침
                self.refresh_tree()

                message = f"프로젝트가 성공적으로 생성되었습니다!\n\n템플릿: {selected_template}"
                if tag:
                    message += f"\nTAG: {tag}"
                message += f"\n작업 경로: {custom_dir}"
                if use_custom_settings:
                    message += f"\nSOURCES 커스터마이징 적용됨"
                message += f"\n\n새 프로젝트로 자동 전환되었습니다."
                self.show_info_dialog(message)

                # 자동 Download 실행
                self.add_log("프로젝트 생성 후 자동 Download를 시작합니다...", "HIGH")
                try:
                    self.workspace.download()
                    self.add_log("Download 완료", "HIGH")
                    # Full Refresh: Download 후 정확한 상태 반영
                    self.refresh_tree(full_refresh=True)
                except Exception as e:
                    self.add_log(f"Download 실패: {e}", "ERROR")
                    self.show_error_dialog(f"Download 중 오류가 발생했습니다:\n{e}")

                return True
            except Exception as e:
                # 오류 발생시 임시 파일 정리
                if temp_ini_file and os.path.exists(temp_ini_file):
                    os.remove(temp_ini_file)
                self.show_error_dialog(f"프로젝트 생성 실패:\n{e}")
                return False

    def show_project_switching_dialog(self):
        """프로젝트 목록 다이얼로그"""
        from ..utils.config import ProjectSelectionManager

        project_manager = ProjectSelectionManager(self.workspace)
        registered_projects = project_manager._get_registered_projects()

        if not registered_projects:
            self.show_error_dialog("등록된 프로젝트가 없습니다.\n먼저 신규 프로젝트를 생성하세요.")
            return False

        current_project = self.workspace.get_current_project_name()

        # 메뉴 항목 구성
        menu_items = []
        for project_count, project_name, work_dir, tag, create_date in registered_projects:
            current_marker = " [현재]" if str(project_count) == current_project else ""
            if tag:
                display_name = f"{project_name}({tag})"
            else:
                display_name = project_name
            menu_items.append(f"{display_name} ({work_dir}){current_marker}")

        while True:
            selected_idx = self.show_menu_dialog("프로젝트 목록", menu_items, "↑↓: 선택, Enter: 선택, ESC: 취소")

            if selected_idx == -1:  # 취소
                return False

            project_count, project_name, work_dir, tag, create_date = registered_projects[selected_idx]

            # 디스플레이 이름 생성
            if tag:
                display_name = f"{project_name}({tag})"
            else:
                display_name = project_name

            # 이 프로젝트에 대한 action을 반복 선택할 수 있도록 내부 루프
            while True:
                action = self.show_choice_dialog(
                    f"프로젝트: {display_name}\n경로: {work_dir}",
                    ["프로젝트 선택", "프로젝트 편집", "프로젝트 삭제", "프로젝트 복제", "취소"]
                )

                if action == 4 or action == -1:  # 취소
                    break  # 외부 루프로 (프로젝트 목록으로)

                if action == 0:  # 프로젝트 선택
                    if str(project_count) == current_project:
                        self.show_info_dialog(f"이미 현재 프로젝트입니다:\n{display_name}")
                        continue

                    try:
                        self.workspace._load_project(project_name)
                        # 4자리 패딩된 프로젝트 번호 생성
                        padded_project_number = f"{project_count:04d}"
                        self.workspace.current_project_number = padded_project_number  # 현재 프로젝트 번호 저장
                        self.workspace._apply_final_config()  # 설정 적용하여 working_dir 업데이트
                        project_manager._update_last_project(padded_project_number)

                        # 프로젝트 변경시 캐시 초기화
                        self.tracked_files_cache = None
                        self.tracked_files_cache_time = 0
                        self.file_state_cache.clear()
                        self.workspace.last_production_check_time = 0  # Production 체크 시간도 초기화
                        self.add_log("프로젝트 변경으로 캐시 초기화됨", "DEBUG")

                        self.show_info_dialog(f"프로젝트가 변경되었습니다:\n{display_name}\n\n작업 경로: {self.workspace.working_dir}")  # 실제 working_dir 표시
                        return True
                    except Exception as e:
                        self.show_error_dialog(f"프로젝트 변경 실패:\n{e}")
                        continue

                elif action == 1:  # 프로젝트 편집
                    # Curses 일시 중단
                    curses.def_prog_mode()
                    curses.endwin()

                    try:
                        padded_project_count = f"{project_count:04d}"
                        changed = project_manager.edit_project(padded_project_count, project_name, tag)

                        if changed and str(project_count) == current_project:
                            # 현재 프로젝트 설정이 변경된 경우 재로드
                            self.workspace._apply_final_config()
                            # 캐시 초기화
                            self.tracked_files_cache = None
                            self.tracked_files_cache_time = 0
                            self.file_state_cache.clear()
                            self.workspace.last_production_check_time = 0
                            self.add_log("프로젝트 설정 변경으로 캐시 초기화됨", "DEBUG")

                    except Exception as e:
                        self.add_log(f"프로젝트 편집 중 오류: {e}", "ERROR")

                    finally:
                        # Curses 재개
                        curses.reset_prog_mode()
                        self.stdscr.refresh()
                        self.needs_redraw = True

                    # 목록 갱신 (display_name도 변경될 수 있음)
                    registered_projects = project_manager._get_registered_projects()
                    # 현재 프로젝트 정보 다시 가져오기
                    for pc, pn, wd, t, cd in registered_projects:
                        if pc == project_count:
                            project_name = pn
                            work_dir = wd
                            tag = t
                            if t:
                                display_name = f"{pn}({t})"
                            else:
                                display_name = pn
                            break

                    # 내부 루프 계속 -> 같은 프로젝트의 action 메뉴로 돌아감
                    continue

                elif action == 2:  # 프로젝트 삭제
                    # 삭제 옵션 선택
                    delete_option = self.show_choice_dialog(
                        f"프로젝트 삭제 확인\n\n프로젝트: {display_name}\n작업 경로: {work_dir}",
                        ["삭제(항목만 삭제, 파일 유지)", "삭제(전체 삭제)", "취소"]
                    )

                    if delete_option == 0:  # 항목만 삭제 (파일 유지)
                        self.add_log(f"프로젝트 항목 삭제 시도: {project_count} ({project_name})", "DEBUG")
                        self.add_log(f"삭제할 경로: ~/.cccopy/project/{project_count:04d}/", "DEBUG")

                        # 프로젝트 번호를 4자리로 패딩하여 삭제 시도
                        padded_project_count = f"{project_count:04d}"
                        delete_result = project_manager._delete_project(padded_project_count)
                        if delete_result:
                            self.add_log(f"프로젝트 항목 삭제 성공: {project_count}", "INFO")
                            self.show_info_dialog(f"프로젝트 '{project_name}' 항목이 삭제되었습니다.\n(작업 파일은 유지됩니다)")
                            # 목록 갱신
                            registered_projects = project_manager._get_registered_projects()
                            if not registered_projects:
                                self.show_info_dialog("더 이상 등록된 프로젝트가 없습니다.")
                                return False
                            # 메뉴 항목 재구성 (원래 포맷과 동일하게)
                            menu_items = []
                            for project_count, proj_name, proj_dir, tag, create_date in registered_projects:
                                current_marker = " [현재]" if str(project_count) == current_project else ""
                                if tag:
                                    display_name = f"{proj_name}({tag})"
                                else:
                                    display_name = proj_name
                                menu_items.append(f"{display_name} ({proj_dir}){current_marker}")
                            # 삭제 후 처음부터 다시 시작
                            continue
                        else:
                            # 삭제 실패 (예외 발생 등)
                            actual_path = f"~/.cccopy/project/{project_count:04d}"
                            self.add_log(f"프로젝트 삭제 실패: {project_count}", "ERROR")
                            self.add_log(f"삭제 실패 경로: {actual_path}", "ERROR")
                            self.show_error_dialog(f"프로젝트 삭제 중 오류가 발생했습니다.")

                    elif delete_option == 1:  # 전체 삭제 (파일 포함)
                        # 전체 삭제 최종 확인
                        final_confirm = self.show_choice_dialog(
                            f"전체 삭제 최종 확인\n\n프로젝트: {display_name}\n작업 경로: {work_dir}\n\n경고: 작업 디렉토리의 모든 파일이 삭제됩니다!\n정말로 전체 삭제하시겠습니까?",
                            ["Yes (전체 삭제)", "No (취소)"]
                        )

                        if final_confirm == 0:  # Yes, 전체 삭제 실행
                            self.add_log(f"프로젝트 전체 삭제 시도: {project_count} ({project_name})", "DEBUG")
                            self.add_log(f"삭제할 설정 경로: ~/.cccopy/project/{project_count:04d}/", "DEBUG")
                            self.add_log(f"삭제할 작업 경로: {work_dir}", "DEBUG")

                            import shutil
                            import os

                            try:
                                # 1. 작업 디렉토리 삭제
                                if os.path.exists(work_dir):
                                    shutil.rmtree(work_dir)
                                    self.add_log(f"작업 디렉토리 삭제 완료: {work_dir}", "INFO")
                                else:
                                    self.add_log(f"작업 디렉토리가 이미 존재하지 않음: {work_dir}", "INFO")

                                # 2. 프로젝트 설정 삭제
                                padded_project_count = f"{project_count:04d}"
                                delete_result = project_manager._delete_project(padded_project_count)

                                if delete_result:
                                    self.add_log(f"프로젝트 전체 삭제 성공: {project_count}", "INFO")
                                    self.show_info_dialog(f"프로젝트 '{project_name}'가 완전히 삭제되었습니다.\n(설정 및 작업 파일 모두 삭제됨)")
                                    # 목록 갱신
                                    registered_projects = project_manager._get_registered_projects()
                                    if not registered_projects:
                                        self.show_info_dialog("더 이상 등록된 프로젝트가 없습니다.")
                                        return False
                                    # 메뉴 항목 재구성 (원래 포맷과 동일하게)
                                    menu_items = []
                                    for project_count, proj_name, proj_dir, tag, create_date in registered_projects:
                                        current_marker = " [현재]" if str(project_count) == current_project else ""
                                        if tag:
                                            display_name = f"{proj_name}({tag})"
                                        else:
                                            display_name = proj_name
                                        menu_items.append(f"{display_name} ({proj_dir}){current_marker}")
                                    # 삭제 후 처음부터 다시 시작
                                    continue
                                else:
                                    self.add_log(f"프로젝트 설정 삭제 실패: {project_count}", "ERROR")
                                    self.show_error_dialog(f"프로젝트 설정 삭제 중 오류가 발생했습니다.")

                            except Exception as e:
                                self.add_log(f"전체 삭제 중 오류 발생: {e}", "ERROR")
                                self.show_error_dialog(f"전체 삭제 중 오류가 발생했습니다:\n{e}")

                    # delete_option == 2는 취소이므로 continue
                    # 삭제 후 외부 루프로
                    break

                elif action == 3:  # 프로젝트 복제
                    self.add_log(f"프로젝트 복제 시도: {project_count} ({project_name})", "DEBUG")

                    # 새 작업 디렉토리 입력
                    while True:
                        new_work_dir = self.show_input_dialog(
                            "프로젝트 복제",
                            "새 프로젝트의 작업 디렉토리 경로를 입력하세요:\n(예: /tmp/cccopy/my_work_clone)",
                            ""
                        )

                        if new_work_dir is None:  # 취소
                            break

                        if not new_work_dir:
                            self.show_error_dialog("경로를 입력해야 합니다.")
                            continue

                        # 중복 경로 체크
                        if project_manager._is_path_already_used(new_work_dir):
                            self.show_error_dialog(f"이미 등록된 경로입니다:\n{new_work_dir}\n\n다른 경로를 입력하세요.")
                            continue

                        # 새 TAG 입력 (기본값: 원본 TAG + " (복제됨)")
                        default_tag = f"{tag} (복제됨)" if tag else "(복제됨)"
                        new_tag = self.show_input_dialog(
                            "프로젝트 복제",
                            f"새 프로젝트의 TAG를 입력하세요:",
                            default_tag
                        )

                        if new_tag is None:  # 취소
                            break

                        if not new_tag:
                            new_tag = default_tag

                        # 복제 실행
                        padded_project_count = f"{project_count:04d}"
                        self.add_log(f"복제 실행: {padded_project_count} → {new_work_dir} (TAG: {new_tag})", "INFO")

                        if project_manager.clone_project(padded_project_count, new_work_dir, new_tag):
                            self.add_log(f"프로젝트 복제 성공", "INFO")
                            self.show_info_dialog(f"프로젝트가 복제되었습니다!\n\n원본: {display_name}\n새 작업 경로: {new_work_dir}\n새 TAG: {new_tag}")

                            # 자동 Download 실행
                            self.add_log("프로젝트 복제 후 자동 Download를 시작합니다...", "HIGH")
                            try:
                                self.workspace.download()
                                self.add_log("Download 완료", "HIGH")
                                # Full Refresh: Download 후 정확한 상태 반영
                                self.refresh_tree(full_refresh=True)
                            except Exception as e:
                                self.add_log(f"Download 실패: {e}", "ERROR")
                                self.show_error_dialog(f"Download 중 오류가 발생했습니다:\n{e}")

                            # 목록 갱신
                            registered_projects = project_manager._get_registered_projects()
                            # 메뉴 항목 재구성
                            menu_items = []
                            for proj_count, proj_name, proj_dir, proj_tag, create_date in registered_projects:
                                current_marker = " [현재]" if str(proj_count) == current_project else ""
                                if proj_tag:
                                    proj_display_name = f"{proj_name}({proj_tag})"
                                else:
                                    proj_display_name = proj_name
                                menu_items.append(f"{proj_display_name} ({proj_dir}){current_marker}")

                            # 복제 후 처음부터 다시 시작
                            break
                        else:
                            self.add_log(f"프로젝트 복제 실패", "ERROR")
                            self.show_error_dialog("프로젝트 복제에 실패했습니다.")
                            break

    def show_log_file_selector(self):
        """로그 파일 선택 다이얼로그"""
        try:
            # 로그 파일 목록 가져오기 (최신순)
            log_files = []
            for filename in os.listdir(LOG_DIR):
                if filename.endswith('.log'):
                    filepath = os.path.join(LOG_DIR, filename)
                    # 파일 수정 시간 기준으로 정렬
                    log_files.append((os.path.getmtime(filepath), filepath, filename))

            if not log_files:
                self.add_log("로그 파일이 없습니다", "INFO")
                return

            # 수정 시간 기준 내림차순 정렬 (최신이 위로)
            log_files.sort(reverse=True)

            # 현재 로그 파일 경로 (실행 중인 로그)
            current_log_path = self.current_log_file_path if hasattr(self, 'current_log_file_path') else None

            # 메뉴 아이템 생성 (현재 로그 파일은 제외)
            items = []
            filtered_log_files = []
            for mtime, filepath, filename in log_files:
                # 현재 실행 중인 로그 파일은 목록에서 제외
                if filepath == current_log_path:
                    continue
                import datetime
                time_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                items.append(f"{filename} ({time_str})")
                filtered_log_files.append((mtime, filepath, filename))

            # 현재 로그 추가 (맨 위)
            items.insert(0, "[ Current Log ]")
            filtered_log_files.insert(0, (0, None, "current"))

            # 다이얼로그 표시
            selected = self.show_menu_dialog("로그 파일 선택", items, "↑↓: 선택, Enter: 확인, ESC: 취소")

            if selected >= 0:
                mtime, filepath, filename = filtered_log_files[selected]
                if filepath is None:
                    # 현재 로그로 돌아가기
                    self.viewing_log_file = None
                    self.add_log("현재 로그로 돌아갑니다", "INFO")
                else:
                    # 선택한 로그 파일 표시
                    self.viewing_log_file = filepath
                    self.add_log(f"로그 파일 선택: {filename}", "INFO")

                # 로그 뷰어 상태 초기화
                self.log_selected_index = 0
                self.log_scroll_offset = 0
                self.log_viewer_first_time = True

        except Exception as e:
            self.add_log(f"로그 파일 목록 읽기 실패: {e}", "ERROR")

    def show_menu_dialog(self, title, items, help_text=None):
        """메뉴 선택 다이얼로그 - 스크롤 지원"""
        if not hasattr(self, 'stdscr') or not self.stdscr:
            return -1

        stdscr = self.stdscr
        height, width = stdscr.getmaxyx()

        # 다이얼로그 크기 계산 - 한글 너비 고려
        title_display_width = self.get_display_width(title)
        max_item_display_width = max(self.get_display_width(item) for item in items) if items else 0
        if help_text is None:
            help_text = "↑↓: 선택, Enter: 확인, ESC: 취소"
        help_display_width = self.get_display_width(help_text)

        # 충분한 여백을 고려한 다이얼로그 너비 계산
        min_width = max(title_display_width + 6, max_item_display_width + 10, help_display_width + 6, 45)
        dialog_width = min(min_width, width - 6)

        # 최대 표시 가능한 항목 수 계산 (화면 높이 고려)
        # 구조: 상단테두리(1) + 제목(1) + 구분선(1) + 항목들(N) + 구분선(1) + 도움말(1) + 하단테두리(1) = N + 6
        max_visible_items = max(5, height - 10)  # 최소 5개, 최대 화면 높이에서 여유 공간 제외
        visible_items = min(len(items), max_visible_items)
        dialog_height = visible_items + 6

        start_y = max(0, (height - dialog_height) // 2)
        start_x = max(0, (width - dialog_width) // 2)

        selected_index = 0
        scroll_offset = 0
        needs_full_redraw = True
        last_selected_index = -1
        last_scroll_offset = -1

        # 메뉴 항목 라인들을 미리 계산 (한 번만)
        menu_lines = []
        content_width = dialog_width - 2
        for i, item in enumerate(items):
            # 선택되지 않은 상태의 라인
            prefix = "   "
            available_width = content_width - len(prefix) - 1
            item_truncated = self.truncate_text(item, available_width)
            item_actual_width = self.get_display_width(item_truncated)
            suffix = " " * (available_width - item_actual_width) + " "
            normal_line = "│" + prefix + item_truncated + suffix + "│"

            # 선택된 상태의 라인
            prefix = " ▶ "
            available_width = content_width - len(prefix) - 1
            item_truncated = self.truncate_text(item, available_width)
            item_actual_width = self.get_display_width(item_truncated)
            suffix = " " * (available_width - item_actual_width) + " "
            selected_line = "│" + prefix + item_truncated + suffix + "│"

            menu_lines.append((normal_line, selected_line))

        while True:
            try:
                # 스크롤 처리: 선택된 항목이 화면에 보이도록 조정
                if selected_index < scroll_offset:
                    scroll_offset = selected_index
                elif selected_index >= scroll_offset + visible_items:
                    scroll_offset = selected_index - visible_items + 1

                # 전체 재그리기 또는 스크롤 변경
                if needs_full_redraw or last_scroll_offset != scroll_offset:
                    # 상단 테두리
                    stdscr.addstr(start_y, start_x, "┌" + "─" * (dialog_width - 2) + "┐")

                    # 제목 - 중앙 정렬 (한글 너비 고려)
                    content_width = dialog_width - 2
                    title_truncated = self.truncate_text(title, content_width)
                    title_actual_width = self.get_display_width(title_truncated)
                    title_padding = (content_width - title_actual_width) // 2
                    title_line = "│" + " " * title_padding + title_truncated + " " * (content_width - title_padding - title_actual_width) + "│"
                    stdscr.addstr(start_y + 1, start_x, title_line)

                    # 구분선
                    stdscr.addstr(start_y + 2, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                    # 보이는 항목들만 그리기 (스크롤 적용)
                    for display_idx in range(visible_items):
                        item_idx = scroll_offset + display_idx
                        row = start_y + 3 + display_idx

                        if item_idx < len(items):
                            normal_line, selected_line = menu_lines[item_idx]
                            if item_idx == selected_index:
                                color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                                stdscr.addstr(row, start_x, selected_line, color)
                            else:
                                stdscr.addstr(row, start_x, normal_line)
                        else:
                            # 빈 줄
                            empty_line = "│" + " " * (dialog_width - 2) + "│"
                            stdscr.addstr(row, start_x, empty_line)

                    # 하단 구분선
                    help_row = start_y + visible_items + 3
                    stdscr.addstr(help_row, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                    # 도움말 - 중앙 정렬 (한글 너비 고려)
                    content_width = dialog_width - 2
                    help_truncated = self.truncate_text(help_text, content_width)
                    help_actual_width = self.get_display_width(help_truncated)
                    help_padding = (content_width - help_actual_width) // 2
                    help_line = "│" + " " * help_padding + help_truncated + " " * (content_width - help_padding - help_actual_width) + "│"
                    stdscr.addstr(help_row + 1, start_x, help_line)

                    # 하단 테두리
                    stdscr.addstr(help_row + 2, start_x, "└" + "─" * (dialog_width - 2) + "┘")

                    needs_full_redraw = False
                    last_selected_index = selected_index
                    last_scroll_offset = scroll_offset

                # 선택 상태만 변경된 경우 (스크롤 없이)
                elif last_selected_index != selected_index:
                    # 이전 선택 항목 언하이라이트
                    if last_selected_index >= scroll_offset and last_selected_index < scroll_offset + visible_items:
                        display_idx = last_selected_index - scroll_offset
                        row = start_y + 3 + display_idx
                        normal_line, _ = menu_lines[last_selected_index]
                        stdscr.addstr(row, start_x, normal_line)

                    # 새 선택 항목 하이라이트
                    if selected_index >= scroll_offset and selected_index < scroll_offset + visible_items:
                        display_idx = selected_index - scroll_offset
                        row = start_y + 3 + display_idx
                        _, selected_line = menu_lines[selected_index]
                        color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                        stdscr.addstr(row, start_x, selected_line, color)

                    last_selected_index = selected_index

                stdscr.refresh()

                key = stdscr.getch()

                if key == curses.KEY_UP and selected_index > 0:
                    selected_index -= 1
                elif key == curses.KEY_DOWN and selected_index < len(items) - 1:
                    selected_index += 1
                elif key == curses.KEY_PPAGE:  # Page Up
                    selected_index = max(0, selected_index - visible_items)
                elif key == curses.KEY_NPAGE:  # Page Down
                    selected_index = min(len(items) - 1, selected_index + visible_items)
                elif key == curses.KEY_HOME:
                    selected_index = 0
                elif key == curses.KEY_END:
                    selected_index = len(items) - 1
                elif key == 10 or key == 13:  # Enter
                    return selected_index
                elif key == 27 or key == ord('q') or key == ord('Q'):  # ESC
                    return -1

            except curses.error as e:
                # 디버깅을 위해 로그 추가
                self.add_log(f"Dialog drawing error: {e}", "ERROR")
                return -1

    def show_input_dialog(self, title, message, default_value=""):
        """입력 다이얼로그 - 한글 입력 지원"""
        import unicodedata

        if not hasattr(self, 'stdscr') or not self.stdscr:
            return None

        stdscr = self.stdscr

        # 기존 커서 상태 저장
        try:
            old_cursor_state = curses.curs_set(0)
            stdscr.refresh()
        except curses.error:
            old_cursor_state = 0

        height, width = stdscr.getmaxyx()

        # 메시지를 줄 단위로 분할
        message_lines = message.split('\n')

        # 다이얼로그 크기 계산 (한글 폭 고려, 화면 크기 제한)
        max_message_width = max(self.get_display_width(line) for line in message_lines) if message_lines else 0
        dialog_width = min(max(self.get_display_width(title) + 4, max_message_width + 4, 50), width - 4)

        # 메시지 라인 수 제한 (화면 높이 고려)
        max_message_lines = max(1, height - 12)  # 최소 1줄, 최대 화면 높이에서 여유 공간 제외
        if len(message_lines) > max_message_lines:
            message_lines = message_lines[:max_message_lines]
            message_lines.append("...")

        dialog_height = len(message_lines) + 9

        start_y = max(0, (height - dialog_height) // 2)
        start_x = max(0, (width - dialog_width) // 2)

        # 다이얼로그가 화면을 벗어나지 않도록 조정
        if start_y + dialog_height >= height:
            start_y = max(0, height - dialog_height - 1)
        if start_x + dialog_width >= width:
            start_x = max(0, width - dialog_width - 1)

        # 한글 입력을 위한 변수들
        text = list(default_value)  # 문자 리스트로 관리
        cursor_pos = len(text)
        multibyte_buffer = []  # UTF-8 멀티바이트 버퍼
        scroll_offset = 0  # 스크롤 오프셋 (문자 인덱스)

        def get_char_width(char):
            """문자의 화면 표시 폭 계산 (한글은 2, 영문은 1)"""
            if ord(char) <= 127:  # ASCII
                return 1
            # 한글 및 동아시아 문자 폭 계산
            eaw = unicodedata.east_asian_width(char)
            if eaw in ('F', 'W'):  # Full-width, Wide
                return 2
            else:
                return 1

        try:
            while True:
                # 다이얼로그 영역 지우기
                for y in range(start_y, start_y + dialog_height):
                    for x in range(start_x, start_x + dialog_width):
                        try:
                            stdscr.addch(y, x, ' ')
                        except curses.error:
                            pass

                # 다이얼로그 배경
                stdscr.addstr(start_y, start_x, "┌" + "─" * (dialog_width - 2) + "┐")

                # 제목 중앙 정렬 (한글 폭 고려)
                title_line = self.create_dialog_line(title, dialog_width, 'center')
                stdscr.addstr(start_y + 1, start_x, title_line)
                stdscr.addstr(start_y + 2, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                # 메시지 표시 (한글 폭 고려)
                for i, line in enumerate(message_lines):
                    line_padded = self.create_dialog_line(line, dialog_width, 'left')
                    stdscr.addstr(start_y + 3 + i, start_x, line_padded)

                # 입력 필드 구분선
                separator_row = start_y + 3 + len(message_lines)
                stdscr.addstr(separator_row, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                # 입력 필드
                input_row = start_y + 3 + len(message_lines) + 1
                input_field_width = dialog_width - 6

                # 스크롤 오프셋 자동 조정 (커서가 항상 보이도록)
                input_text = ''.join(text)

                # 커서 위치의 화면 표시 폭 계산
                cursor_display_pos = self.get_display_width(input_text[:cursor_pos])
                scroll_display_pos = self.get_display_width(input_text[:scroll_offset])

                # 커서가 화면 오른쪽을 벗어나면 스크롤 오른쪽으로
                visible_width = input_field_width - 2  # "> " 제외
                if cursor_display_pos - scroll_display_pos >= visible_width:
                    # 커서가 보이도록 스크롤 오프셋 증가
                    while scroll_offset < len(text) and cursor_display_pos - self.get_display_width(input_text[:scroll_offset]) >= visible_width:
                        scroll_offset += 1

                # 커서가 화면 왼쪽을 벗어나면 스크롤 왼쪽으로
                if cursor_display_pos < scroll_display_pos:
                    scroll_offset = cursor_pos

                # 스크롤된 텍스트 표시 (한글 폭 고려)
                scrolled_text = input_text[scroll_offset:]
                display_text = self.truncate_text(scrolled_text, visible_width)

                # 입력 필드 직접 그리기 (create_dialog_line 사용하지 않음)
                # 왼쪽 경계
                stdscr.addstr(input_row, start_x, "│ ")
                # 프롬프트와 입력 텍스트
                stdscr.addstr(input_row, start_x + 2, "> ")
                stdscr.addstr(input_row, start_x + 4, display_text)
                # 나머지 공백 채우기
                text_display_width = self.get_display_width(display_text)
                remaining_width = dialog_width - 6 - text_display_width  # "│ > " + "│" 제외
                if remaining_width > 0:
                    stdscr.addstr(input_row, start_x + 4 + text_display_width, " " * remaining_width)
                # 오른쪽 경계
                stdscr.addstr(input_row, start_x + dialog_width - 2, " │")

                # 커서 위치 계산 (스크롤 고려)
                scroll_display_width = self.get_display_width(input_text[:scroll_offset])
                cursor_relative_pos = cursor_display_pos - scroll_display_width
                cursor_x = start_x + 4 + cursor_relative_pos  # "│ > " 고려
                cursor_y = input_row

                # 하단 경계 및 도움말
                help_row = start_y + 3 + len(message_lines) + 2
                stdscr.addstr(help_row, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                help_text = "Enter: 확인, ESC: 취소"
                help_line = self.create_dialog_line(help_text, dialog_width, 'center')
                stdscr.addstr(help_row + 1, start_x, help_line)

                stdscr.addstr(help_row + 2, start_x, "└" + "─" * (dialog_width - 2) + "┘")

                # 화면 업데이트 및 커서 위치 설정
                stdscr.refresh()
                try:
                    max_y, max_x = stdscr.getmaxyx()
                    if 0 <= cursor_y < max_y and 0 <= cursor_x < max_x:
                        stdscr.move(cursor_y, cursor_x)
                        curses.curs_set(1)
                        stdscr.refresh()
                except curses.error:
                    pass

                # 키 입력 처리
                key = stdscr.getch()

                if key == 10 or key == 13:  # Enter
                    result = ''.join(text)
                    break
                elif key == 27:  # ESC
                    result = None
                    break
                elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:
                    if cursor_pos > 0:
                        text.pop(cursor_pos - 1)
                        cursor_pos -= 1
                elif key == curses.KEY_DC:  # Delete 키
                    if cursor_pos < len(text):
                        text.pop(cursor_pos)
                elif key == curses.KEY_LEFT:
                    cursor_pos = max(0, cursor_pos - 1)
                elif key == curses.KEY_RIGHT:
                    cursor_pos = min(len(text), cursor_pos + 1)
                elif key == curses.KEY_HOME:
                    cursor_pos = 0
                elif key == curses.KEY_END:
                    cursor_pos = len(text)
                elif key >= 32:  # 모든 인쇄 가능한 문자 (한글 포함)
                    # UTF-8 멀티바이트 처리
                    if key >= 128:  # 멀티바이트 문자
                        multibyte_buffer.append(key)
                        try:
                            # 바이트 배열을 UTF-8로 디코딩 시도
                            char_bytes = bytes(multibyte_buffer)
                            char = char_bytes.decode('utf-8')
                            # 성공하면 문자 삽입
                            text.insert(cursor_pos, char)
                            cursor_pos += 1
                            multibyte_buffer = []
                        except UnicodeDecodeError:
                            # 아직 불완전한 멀티바이트 시퀀스
                            continue
                    else:  # ASCII 문자
                        multibyte_buffer = []  # 버퍼 초기화
                        char = chr(key)
                        text.insert(cursor_pos, char)
                        cursor_pos += 1

            # 커서 상태 복원
            try:
                curses.curs_set(old_cursor_state)
            except curses.error:
                pass

            return result

        except curses.error:
            # 커서 상태 복원 후 반환
            try:
                curses.curs_set(old_cursor_state)
            except curses.error:
                pass
            return None

    def show_choice_dialog(self, message, choices):
        """선택 다이얼로그 (한글 폭 처리 적용, 스크롤 지원)"""
        if not hasattr(self, 'stdscr') or not self.stdscr:
            return -1

        stdscr = self.stdscr
        height, width = stdscr.getmaxyx()

        # 메시지를 줄 단위로 분할
        message_lines = message.split('\n')

        # 다이얼로그 크기 계산 (한글 폭 고려)
        max_message_width = max(self.get_display_width(line) for line in message_lines) if message_lines else 0
        max_choice_width = max(self.get_display_width(choice) for choice in choices) if choices else 0
        help_text = "↑↓: 선택, Enter: 확인, ESC: 취소"
        help_width = self.get_display_width(help_text)

        min_width = max(max_message_width + 4, max_choice_width + 6, help_width + 4, 40)
        dialog_width = min(min_width, width - 4)

        # 최대 표시 가능한 선택지 수 계산
        # 구조: 상단테두리(1) + 메시지(N) + 구분선(1) + 선택지들(M) + 구분선(1) + 도움말(1) + 하단테두리(1)
        max_visible_choices = max(3, height - len(message_lines) - 8)
        visible_choices = min(len(choices), max_visible_choices)
        dialog_height = len(message_lines) + visible_choices + 6

        start_y = max(0, (height - dialog_height) // 2)
        start_x = max(0, (width - dialog_width) // 2)

        selected_index = 0
        scroll_offset = 0

        while True:
            try:
                # 스크롤 처리
                if selected_index < scroll_offset:
                    scroll_offset = selected_index
                elif selected_index >= scroll_offset + visible_choices:
                    scroll_offset = selected_index - visible_choices + 1

                # 상단 테두리
                stdscr.addstr(start_y, start_x, "┌" + "─" * (dialog_width - 2) + "┐")

                # 메시지 표시 (한글 안전 방식)
                for i, line in enumerate(message_lines):
                    row = start_y + 1 + i
                    dialog_line = self.create_dialog_line(line, dialog_width, 'left')
                    stdscr.addstr(row, start_x, dialog_line)

                # 구분선
                separator_row = start_y + 1 + len(message_lines)
                stdscr.addstr(separator_row, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                # 선택지들 (스크롤 적용, 한글 안전 방식)
                for display_idx in range(visible_choices):
                    choice_idx = scroll_offset + display_idx
                    row = separator_row + 1 + display_idx

                    if choice_idx < len(choices):
                        choice = choices[choice_idx]
                        if choice_idx == selected_index:
                            prefix = " ▶ "
                            color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                        else:
                            prefix = "   "
                            color = None

                        content_width = dialog_width - 4  # "│ " + " │" 제외
                        available_width = content_width - len(prefix)
                        choice_truncated = self.truncate_text(choice, available_width)
                        choice_actual_width = self.get_display_width(choice_truncated)
                        suffix = " " * (available_width - choice_actual_width)

                        dialog_line = "│ " + prefix + choice_truncated + suffix + " │"

                        if color:
                            stdscr.addstr(row, start_x, dialog_line, color)
                        else:
                            stdscr.addstr(row, start_x, dialog_line)
                    else:
                        # 빈 줄
                        empty_line = "│" + " " * (dialog_width - 2) + "│"
                        stdscr.addstr(row, start_x, empty_line)

                # 하단 구분선
                help_row = separator_row + 1 + visible_choices
                stdscr.addstr(help_row, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                # 도움말 (한글 안전 중앙 정렬)
                help_dialog_line = self.create_dialog_line(help_text, dialog_width, 'center')
                stdscr.addstr(help_row + 1, start_x, help_dialog_line)

                # 하단 테두리
                stdscr.addstr(help_row + 2, start_x, "└" + "─" * (dialog_width - 2) + "┘")

                stdscr.refresh()

                key = stdscr.getch()

                if key == curses.KEY_UP and selected_index > 0:
                    selected_index -= 1
                elif key == curses.KEY_DOWN and selected_index < len(choices) - 1:
                    selected_index += 1
                elif key == curses.KEY_PPAGE:  # Page Up
                    selected_index = max(0, selected_index - visible_choices)
                elif key == curses.KEY_NPAGE:  # Page Down
                    selected_index = min(len(choices) - 1, selected_index + visible_choices)
                elif key == curses.KEY_HOME:
                    selected_index = 0
                elif key == curses.KEY_END:
                    selected_index = len(choices) - 1
                elif key == 10 or key == 13:  # Enter
                    return selected_index
                elif key == 27:  # ESC
                    return -1

            except curses.error:
                return -1

    def show_info_dialog(self, message):
        """정보 다이얼로그"""
        self.show_message_dialog("정보", message)

    def show_error_dialog(self, message):
        """오류 다이얼로그"""
        self.show_message_dialog("오류", message)

    def show_message_dialog(self, title, message):
        """메시지 다이얼로그 - 스크롤 지원"""
        if not hasattr(self, 'stdscr') or not self.stdscr:
            return

        stdscr = self.stdscr
        height, width = stdscr.getmaxyx()

        # 메시지를 줄 단위로 분할
        message_lines = message.split('\n')

        # 다이얼로그 크기 계산 (한글 폭 고려)
        title_width = self.get_display_width(title) + 4
        max_message_width = max(self.get_display_width(line) for line in message_lines) + 4 if message_lines else 30
        dialog_width = min(max(title_width, max_message_width, 30), width - 4)

        # 최대 표시 가능한 메시지 라인 수
        max_visible_lines = max(3, height - 10)
        visible_lines = min(len(message_lines), max_visible_lines)
        dialog_height = visible_lines + 6

        start_y = max(0, (height - dialog_height) // 2)
        start_x = max(0, (width - dialog_width) // 2)

        scroll_offset = 0

        while True:
            try:
                # 다이얼로그 영역 지우기 (배경 겹침 방지)
                for row in range(start_y, start_y + dialog_height):
                    if row < height and start_x < width:
                        clear_line = " " * min(dialog_width, width - start_x)
                        stdscr.addstr(row, start_x, clear_line)

                # 다이얼로그 배경
                stdscr.addstr(start_y, start_x, "┌" + "─" * (dialog_width - 2) + "┐")
                stdscr.addstr(start_y + 1, start_x, self.create_dialog_line(title, dialog_width, 'center'))
                stdscr.addstr(start_y + 2, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                # 메시지 표시 (스크롤 적용)
                for display_idx in range(visible_lines):
                    line_idx = scroll_offset + display_idx
                    if line_idx < len(message_lines):
                        line = message_lines[line_idx]
                        stdscr.addstr(start_y + 3 + display_idx, start_x, self.create_dialog_line(line, dialog_width, 'left'))
                    else:
                        # 빈 줄
                        empty_line = "│" + " " * (dialog_width - 2) + "│"
                        stdscr.addstr(start_y + 3 + display_idx, start_x, empty_line)

                # 하단 경계 및 도움말
                help_row = start_y + 3 + visible_lines
                stdscr.addstr(help_row, start_x, "├" + "─" * (dialog_width - 2) + "┤")

                # 스크롤 가능 여부에 따라 도움말 변경
                if len(message_lines) > visible_lines:
                    help_msg = "↑↓: 스크롤, Enter: 확인"
                else:
                    help_msg = "Enter: 확인"
                stdscr.addstr(help_row + 1, start_x, self.create_dialog_line(help_msg, dialog_width, 'center'))
                stdscr.addstr(help_row + 2, start_x, "└" + "─" * (dialog_width - 2) + "┘")

                stdscr.refresh()

                key = stdscr.getch()

                if key == 10 or key == 13 or key == 27:  # Enter or ESC
                    break
                elif key == curses.KEY_UP and scroll_offset > 0:
                    scroll_offset -= 1
                elif key == curses.KEY_DOWN and scroll_offset < len(message_lines) - visible_lines:
                    scroll_offset += 1
                elif key == curses.KEY_PPAGE:  # Page Up
                    scroll_offset = max(0, scroll_offset - visible_lines)
                elif key == curses.KEY_NPAGE:  # Page Down
                    scroll_offset = min(len(message_lines) - visible_lines, scroll_offset + visible_lines)
                elif key == curses.KEY_HOME:
                    scroll_offset = 0
                elif key == curses.KEY_END:
                    scroll_offset = max(0, len(message_lines) - visible_lines)

            except curses.error:
                break

    def show_simple_project_management(self):
        """텍스트 모드에서 간단한 프로젝트 관리"""
        self.add_log("=== 프로젝트 관리 ===", "INFO")

        # 현재 프로젝트 정보
        current_project = self.workspace.get_current_project_name()
        self.add_log(f"현재 프로젝트: {current_project}", "INFO")

        # 사용 가능한 템플릿 목록
        template_projects = list(self.workspace.project_configs.keys())
        self.add_log(f"사용 가능한 템플릿: {', '.join(template_projects)}", "INFO")

        # 등록된 프로젝트 목록
        from ..utils.config import ProjectSelectionManager

        project_manager = ProjectSelectionManager(self.workspace)
        registered_projects = project_manager._get_registered_projects()

        if registered_projects:
            self.add_log("등록된 프로젝트:", "INFO")
            for i, (project_count, name, path, tag, create_date) in enumerate(registered_projects, 1):
                current_marker = " [현재]" if name == current_project else ""
                self.add_log(f"  {i}. {name} ({path}){current_marker}", "INFO")
        else:
            self.add_log("등록된 프로젝트가 없습니다.", "INFO")

        self.add_log("프로젝트 관리는 별도 터미널에서 'CCCOPY_FORCE_TEXT_MODE=1 python3 cccopy.py'로 실행하세요.", "INFO")
        self.add_log("=== 프로젝트 관리 완료 ===", "INFO")

    def input_dialog(self, stdscr, title, default_value=""):
        """입력 다이얼로그 표시"""
        height, width = stdscr.getmaxyx()

        # 다이얼로그 크기 계산
        dialog_width = min(60, width - 4)
        dialog_height = 6
        dialog_y = (height - dialog_height) // 2
        dialog_x = (width - dialog_width) // 2

        # 다이얼로그 창 생성
        dialog_win = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        dialog_win.box()

        # 제목 표시
        title_text = title[:dialog_width-4]
        dialog_win.addstr(1, 2, title_text)

        # 입력 필드 표시
        input_y = 3
        input_x = 2
        input_width = dialog_width - 4

        # 기본값 설정
        current_text = default_value
        cursor_pos = len(current_text)

        while True:
            # 다이얼로그 다시 그리기
            dialog_win.erase()
            dialog_win.box()
            dialog_win.addstr(1, 2, title_text)

            # 입력 필드 그리기
            display_text = current_text[:input_width-2]
            dialog_win.addstr(input_y, input_x, display_text)

            # 커서 표시
            if cursor_pos < len(display_text):
                try:
                    dialog_win.addch(input_y, input_x + cursor_pos, ord(display_text[cursor_pos]), curses.A_REVERSE)
                except:
                    pass
            else:
                try:
                    dialog_win.addch(input_y, input_x + len(display_text), ord(' '), curses.A_REVERSE)
                except:
                    pass

            # 도움말 표시
            dialog_win.addstr(dialog_height-2, 2, "Enter: 확인  ESC: 취소")

            dialog_win.refresh()

            # 키 입력 처리
            key = dialog_win.getch()

            if key == 27:  # ESC
                return None
            elif key in [10, 13]:  # Enter
                return current_text
            elif key == curses.KEY_BACKSPACE or key == 8 or key == 127:
                if cursor_pos > 0:
                    current_text = current_text[:cursor_pos-1] + current_text[cursor_pos:]
                    cursor_pos -= 1
            elif key == curses.KEY_LEFT:
                cursor_pos = max(0, cursor_pos - 1)
            elif key == curses.KEY_RIGHT:
                cursor_pos = min(len(current_text), cursor_pos + 1)
            elif key == curses.KEY_HOME:
                cursor_pos = 0
            elif key == curses.KEY_END:
                cursor_pos = len(current_text)
            elif 32 <= key <= 126:  # 인쇄 가능한 ASCII 문자
                current_text = current_text[:cursor_pos] + chr(key) + current_text[cursor_pos:]
                cursor_pos += 1

    def show_file_list(self):
        """파일 목록 표시 (텍스트 모드용)"""
        print(f"\n=== {self.mode.value} 파일 목록 ===")

        if not self.tree.flat_nodes:
            print("파일이 없습니다.")
            return

        for i, node in enumerate(self.tree.flat_nodes[:20]):  # 최대 20개만 표시
            depth = self.tree.get_depth(node)
            prefix = "  " * depth

            if node.is_dir:
                expand_symbol = "[-]" if node.expanded else "[+]"
                # 디렉토리는 파란색 + 볼드로 표시
                color = self.get_ansi_color_for_folder()
                reset = self.ansi_colors.get('reset', '') if hasattr(self, 'ansi_colors') else ''
                print(f"{i+1:3}. {prefix}{color}{node.name}/{reset} {expand_symbol}")
            else:
                state_symbol = self.get_state_symbol(node.state)
                size_text = self.format_size(node.size)
                # 파일 상태에 따른 색상 적용
                color = self.get_ansi_color_for_state(node.state)
                reset = self.ansi_colors.get('reset', '') if hasattr(self, 'ansi_colors') else ''
                print(f"{i+1:3}. {prefix}{color}{node.name} [{state_symbol}]{reset} {size_text}")

        if len(self.tree.flat_nodes) > 20:
            print(f"... 외 {len(self.tree.flat_nodes) - 20}개 더")

        print(f"\n총 {len(self.tree.flat_nodes)}개 항목")

    def open_app_viewer(self):
        """App 목록 화면 열기"""
        try:
            from cccopy.apps import get_available_apps
            self.app_list = get_available_apps()

            if not self.app_list:
                self.add_log("사용 가능한 앱이 없습니다", "WARN")
                return

            self.app_viewer_mode = True
            self.app_selected_index = 0
            self.app_scroll_offset = 0
            self.add_log(f"앱 목록 로드 완료 ({len(self.app_list)}개)", "INFO")
            self.needs_redraw = True
        except Exception as e:
            self.add_log(f"앱 목록 로드 실패: {str(e)}", "ERROR")

    def handle_app_viewer_key(self, key):
        """App 뷰어 키 입력 처리"""
        if key == curses.KEY_UP:
            if self.app_selected_index > 0:
                self.app_selected_index -= 1
                self.needs_redraw = True
        elif key == curses.KEY_DOWN:
            if self.app_selected_index < len(self.app_list) - 1:
                self.app_selected_index += 1
                self.needs_redraw = True
        elif key == curses.KEY_HOME:
            self.app_selected_index = 0
            self.needs_redraw = True
        elif key == curses.KEY_END:
            self.app_selected_index = len(self.app_list) - 1
            self.needs_redraw = True
        elif key == curses.KEY_PPAGE:
            self.app_selected_index = max(0, self.app_selected_index - 10)
            self.needs_redraw = True
        elif key == curses.KEY_NPAGE:
            self.app_selected_index = min(len(self.app_list) - 1, self.app_selected_index + 10)
            self.needs_redraw = True
        elif key == ord('\n') or key == 10 or key == 13 or key == curses.KEY_ENTER:
            if 0 <= self.app_selected_index < len(self.app_list):
                self.run_selected_app()

        return True

    def run_selected_app(self):
        """선택된 앱 실행"""
        if not self.app_list or self.app_selected_index >= len(self.app_list):
            return

        app = self.app_list[self.app_selected_index]
        app_name = app['name']

        self.add_log(f"{app_name} 실행 중...", "INFO")

        try:
            app['main'](ui_handler=self)
            self.add_log(f"{app_name} 실행 완료", "INFO")
        except Exception as e:
            self.add_log(f"{app_name} 실행 오류: {str(e)}", "ERROR")
            import traceback
            self.add_log(traceback.format_exc(), "DEBUG")

        self.needs_redraw = True

    def draw_app_viewer(self, stdscr):
        """App 뷰어 화면 그리기"""
        height, width = stdscr.getmaxyx()

        for attempt in range(2):
            try:
                stdscr.addstr(0, 0, "┌" + "─" * (width - 2) + "┐")

                # Header: "이름"(2글자=4칸) + 공백으로 20칸 맞추기 + "설명"
                name_header = "이름"
                desc_header = "설명"
                name_width = 20

                # 이름 헤더의 실제 표시 폭 계산
                name_header_display_width = self.get_display_width(name_header)
                name_header_padding = name_width - name_header_display_width

                # Header 라인 직접 출력
                stdscr.addch(1, 0, '│')
                stdscr.addch(1, 1, ' ')
                col = 2
                # "이름" 출력
                for ch in name_header:
                    stdscr.addch(1, col, ch)
                    ch_width = 2 if ord(ch) >= 0xAC00 and ord(ch) <= 0xD7AF else 1
                    col += ch_width
                # 이름 영역 패딩
                for _ in range(name_header_padding):
                    stdscr.addch(1, col, ' ')
                    col += 1
                # "설명" 출력
                for ch in desc_header:
                    stdscr.addch(1, col, ch)
                    ch_width = 2 if ord(ch) >= 0xAC00 and ord(ch) <= 0xD7AF else 1
                    col += ch_width
                # 나머지 공백
                while col < width - 1:
                    stdscr.addch(1, col, ' ')
                    col += 1
                # 오른쪽 │
                stdscr.addch(1, width - 1, '│')

                stdscr.move(2, 0)
                for col in range(width):
                    stdscr.addch(2, col, ' ')
                stdscr.addstr(2, 0, "├" + "─" * (width - 2) + "┤")
                break
            except curses.error:
                if attempt == 0:
                    height, width = stdscr.getmaxyx()
                    if height < 3 or width < 10:
                        break
                else:
                    try:
                        stdscr.addstr(0, 0, "APP VIEWER")
                        stdscr.addstr(1, 0, f"{len(self.app_list)} apps")
                    except curses.error:
                        pass

        app_start_row = 3
        app_end_row = height - 3
        visible_lines = app_end_row - app_start_row

        if len(self.app_list) > visible_lines:
            max_scroll_offset = len(self.app_list) - visible_lines
            self.app_scroll_offset = min(self.app_scroll_offset, max_scroll_offset)
            self.app_scroll_offset = max(0, self.app_scroll_offset)

            if self.app_selected_index < self.app_scroll_offset:
                self.app_scroll_offset = self.app_selected_index
            elif self.app_selected_index >= self.app_scroll_offset + visible_lines:
                self.app_scroll_offset = self.app_selected_index - visible_lines + 1
        else:
            self.app_scroll_offset = 0

        for i in range(visible_lines):
            row = app_start_row + i
            app_index = self.app_scroll_offset + i

            try:
                if app_index < len(self.app_list):
                    app = self.app_list[app_index]
                    name = app['name']
                    description = app['description']

                    # 한글 폭을 고려한 이름 처리
                    name_width = 20
                    name_display_width = self.get_display_width(name)

                    if name_display_width > name_width:
                        # 잘라야 함
                        truncated_name = ""
                        current_width = 0
                        for ch in name:
                            ch_width = 2 if ord(ch) >= 0xAC00 and ord(ch) <= 0xD7AF else 1
                            if current_width + ch_width <= name_width - 2:
                                truncated_name += ch
                                current_width += ch_width
                            else:
                                break
                        name = truncated_name + ".."
                        name_display_width = self.get_display_width(name)

                    # 이름 뒤에 공백 추가 (실제 표시 폭 기준)
                    name_padding = name_width - name_display_width
                    name_with_padding = name + (' ' * name_padding)

                    # 설명 영역 크기 계산 (한글 폭 고려)
                    inner_width = width - 2  # 양쪽 │ 제외
                    available_desc_width = inner_width - 1 - name_width  # 공백 1개 제외

                    if available_desc_width > 0:
                        desc_display_width = self.get_display_width(description)
                        if desc_display_width > available_desc_width:
                            # 설명 잘라야 함
                            truncated_desc = ""
                            current_width = 0
                            for ch in description:
                                ch_width = 2 if ord(ch) >= 0xAC00 and ord(ch) <= 0xD7AF else 1
                                if current_width + ch_width <= available_desc_width - 3:
                                    truncated_desc += ch
                                    current_width += ch_width
                                else:
                                    break
                            description = truncated_desc + "..."
                    else:
                        description = ""

                    line_text = name_with_padding + description

                    # 전체 라인 구성: │ + 공백 + 텍스트 + 공백 + │
                    # 총 width 길이를 맞춰야 함
                    content_width = width - 2  # 양쪽 │ 제외

                    if app_index == self.app_selected_index:
                        color = getattr(self, 'colors', {}).get('selected', curses.A_REVERSE)
                        # 첫 번째 '│'는 일반 색상
                        stdscr.addch(row, 0, '│')
                        # 공백 1개
                        stdscr.addch(row, 1, ' ')
                        # 텍스트 출력 (하이라이트) - 한글 폭 고려
                        col = 2
                        for ch in line_text:
                            if col < width - 1:
                                stdscr.addch(row, col, ch, color)
                                # 한글은 2칸 차지
                                ch_width = 2 if ord(ch) >= 0xAC00 and ord(ch) <= 0xD7AF else 1
                                col += ch_width
                        # 나머지 공백 (하이라이트)
                        while col < width - 1:
                            stdscr.addch(row, col, ' ', color)
                            col += 1
                        # 마지막 '│'는 일반 색상
                        stdscr.addch(row, width - 1, '│')
                    else:
                        # 일반 출력
                        stdscr.addch(row, 0, '│')
                        stdscr.addch(row, 1, ' ')
                        col = 2
                        for ch in line_text:
                            if col < width - 1:
                                stdscr.addch(row, col, ch)
                                # 한글은 2칸 차지
                                ch_width = 2 if ord(ch) >= 0xAC00 and ord(ch) <= 0xD7AF else 1
                                col += ch_width
                        # 나머지 공백
                        while col < width - 1:
                            stdscr.addch(row, col, ' ')
                            col += 1
                        stdscr.addch(row, width - 1, '│')
                else:
                    # 빈 줄: │ + 공백 + │
                    stdscr.addch(row, 0, '│')
                    for col in range(1, width - 1):
                        stdscr.addch(row, col, ' ')
                    stdscr.addch(row, width - 1, '│')
            except curses.error:
                pass

        try:
            stdscr.move(height - 3, 0)
            for col in range(width):
                stdscr.addch(height - 3, col, ' ')
            stdscr.addstr(height - 3, 0, "├" + "─" * (width - 2) + "┤")

            help_text = "[Enter]Run [ESC]Exit"

            # Help 라인 출력 - 한글 폭 고려 및 노란색 키 표시
            yellow_color = getattr(self, 'colors', {}).get('log_warning', curses.A_BOLD)

            stdscr.addch(height - 2, 0, '│')
            stdscr.addch(height - 2, 1, ' ')

            col = 2
            i = 0
            while i < len(help_text):
                if help_text[i] == '[':
                    # [ ] 안의 텍스트는 노란색
                    end_bracket = help_text.find(']', i)
                    if end_bracket != -1:
                        for ch in help_text[i:end_bracket+1]:
                            if col < width - 1:
                                stdscr.addch(height - 2, col, ch, yellow_color)
                                col += 1
                        i = end_bracket + 1
                    else:
                        if col < width - 1:
                            stdscr.addch(height - 2, col, help_text[i])
                            col += 1
                        i += 1
                else:
                    # 일반 텍스트
                    if col < width - 1:
                        stdscr.addch(height - 2, col, help_text[i])
                        col += 1
                    i += 1

            # 나머지 공백
            while col < width - 1:
                stdscr.addch(height - 2, col, ' ')
                col += 1

            # 오른쪽 │
            stdscr.addch(height - 2, width - 1, '│')

            # Bottom frame (맨 아랫줄)
            try:
                bottom_frame = "└" + "─" * (width - 2) + "┘"
                stdscr.addstr(height - 1, 0, bottom_frame)
            except curses.error:
                pass
        except curses.error:
            pass

    def _show_startup_fortune(self):
        """앱 시작 시 운세 표시 (다이얼로그)"""
        try:
            import datetime
            from ..apps.fortune.main import calculate_fortune_index, d_f

            # APP.FORTUNE.STARTUP_SHOW 설정 확인
            show_fortune = self.preference.get('', 'APP.FORTUNE.STARTUP_SHOW')
            if show_fortune != 'ON':
                return  # OFF이면 운세 표시 안 함

            # 오늘 날짜 가져오기 (yyyymmdd 형식)
            today = datetime.datetime.now().strftime('%Y%m%d')

            # APP.FORTUNE.STARTUP_TODAY 확인 (마지막으로 운세를 표시한 날짜)
            last_shown = self.preference.get('', 'APP.FORTUNE.STARTUP_TODAY')

            if last_shown == today:
                # 오늘 이미 운세를 표시했으면 무시
                return

            # 생년월일시 가져오기
            birth = self.preference.get('', 'APP.FORTUNE.BIRTH')
            if not birth or len(birth) != 10:
                self.add_log("운세를 표시하려면 APP.FORTUNE.BIRTH를 설정하세요 (yyyymmddhh 형식)", "INFO")
                return

            # 운세 계산
            fortune_index = calculate_fortune_index(birth, today)
            fortune_data = d_f()

            if not fortune_data or fortune_index >= len(fortune_data):
                self.add_log("운세 데이터를 불러올 수 없습니다", "DEBUG")
                return

            result = fortune_data[fortune_index]

            # 운세 다이얼로그 표시
            self.messagebox(
                result,
                "오늘의 운세",
                "info",
                "ok"
            )

            # 오늘 날짜를 APP.FORTUNE.STARTUP_TODAY에 저장
            self.preference.set('', 'APP.FORTUNE.STARTUP_TODAY', today)
            self.preference.save()

        except Exception as e:
            # 운세 표시 실패는 무시 (프로그램 진행에 영향 없음)
            self.add_log(f"운세 표시 실패: {e}", "DEBUG")


# main() 함수 제거됨 - cccopy.py에서 직접 실행

# 테스트용 main 함수 추가
if __name__ == "__main__":
    print("CCCopy TUI 테스트 모드")
    print("실제 실행을 위해서는 python3 cccopy.py를 사용하세요.")

    # 간단한 기능 테스트
    try:
        import curses
        print("✓ curses 모듈 사용 가능")
    except ImportError:
        print("✗ curses 모듈 사용 불가")

    print("히스토리 뷰어 기능이 추가되었습니다:")
    print("- H 키: 히스토리 목록 보기")
    print("- ↑↓ 키: 네비게이션")
    print("- Enter: 상세 보기")
    print("- ESC: 뒤로 가기/종료")