"""전역 환경설정 관리 모듈"""
import os
import configparser
from cccopy.utils.ui_handler import display_message
from cccopy.core import LockManager


class PreferenceManager:
    """전역 환경설정 관리 클래스

    환경설정 파일 위치: ~/.cccopy/preference/cccopy.ini
    """

    def __init__(self):
        """PreferenceManager 초기화"""
        self.preference_dir = os.path.expanduser('~/.cccopy/preference')
        self.preference_file = os.path.join(self.preference_dir, 'cccopy.ini')
        # RawConfigParser 사용: 키를 소문자로 변환하지 않음
        # allow_no_value=True: 값이 없는 키도 허용
        # ConfigParser는 섹션이 필요하므로 DEFAULT 섹션 사용
        self.config = configparser.RawConfigParser(allow_no_value=True)
        # 키 이름을 대소문자 그대로 유지
        self.config.optionxform = str

        # 환경설정 디렉토리 생성
        os.makedirs(self.preference_dir, exist_ok=True)

        # LockManager 초기화 (preference 디렉토리에 락 생성)
        lock_dir = os.path.join(self.preference_dir, '.lock')
        os.makedirs(lock_dir, exist_ok=True)
        lock_file = os.path.join(lock_dir, 'preference_lock')
        self.lock_manager = LockManager(lock_file, timeout=60, max_stale_time=300)

        # 환경설정 파일 초기화
        self._initialize_preference_file()

    def _initialize_preference_file(self):
        """환경설정 파일 초기화 (없으면 기본값으로 생성)"""
        if not os.path.exists(self.preference_file):
            self._create_default_preference_file()
        else:
            # 기존 파일 읽기 (섹션이 없는 경우 DEFAULT 섹션으로 읽기)
            try:
                # ConfigParser는 섹션이 필요하므로, 파일에 섹션이 없으면 [DEFAULT] 추가
                with open(self.preference_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                # [DEFAULT] 섹션이 없으면 추가
                if '[DEFAULT]' not in content and '[' not in content:
                    # 섹션이 전혀 없는 경우
                    content_with_section = '[DEFAULT]\n' + content
                    self.config.read_string(content_with_section)
                else:
                    # 이미 섹션이 있는 경우
                    self.config.read(self.preference_file, encoding='utf-8')
            except Exception as e:
                display_message(f"환경설정 파일 읽기 실패: {e}", "ERROR")
                display_message("기본값으로 재생성합니다.", "INFO")
                self._create_default_preference_file()

    def _create_default_preference_file(self):
        """기본 환경설정 파일 생성 (헤더만)"""
        # 헤더만 작성 (개별 항목은 get() 호출시 자동 추가됨)
        default_content = """; ===================================================================
; CCCopy 전역 환경설정 파일
; ===================================================================
;
; 이 파일은 CCCopy의 전역 환경설정을 관리합니다.
;
; 설정 형식:
;   키=값
;
; 주의사항:
;   - 세미콜론(;)으로 시작하는 줄은 주석입니다.
;   - 대소문자를 구분합니다.
;
; ===================================================================
"""

        try:
            with open(self.preference_file, 'w', encoding='utf-8') as f:
                f.write(default_content)
            display_message(f"기본 환경설정 파일 생성: {self.preference_file}", "INFO")

            # config 다시 로드
            self.config = configparser.RawConfigParser(allow_no_value=True)
            self.config.optionxform = str  # 대소문자 유지
            # 섹션이 없으므로 [DEFAULT] 추가하여 로드
            content_with_section = '[DEFAULT]\n' + default_content
            self.config.read_string(content_with_section)

        except Exception as e:
            display_message(f"환경설정 파일 생성 실패: {e}", "ERROR")

    def _get_item_data(self, key):
        """키에 대한 기본값과 커멘트 반환

        Args:
            key: 환경설정 키명

        Returns:
            [기본값, 커멘트] 또는 None
        """
        # 하드코드된 기본값과 커멘트 정의
        item_data = {
            'TUTORIAL.STARTUP_SHOW': ['ON', '프로그램 시작시 튜토리얼을 출력합니다.\n출력 : ON(기본값), 무시 : OFF'],
            'APP.FORTUNE.BIRTH'    : ['',   '오늘의 운세에서 사용할 생년월일시를 yyyymmddhh(예 2000101023) 형식으로 입력하세요.\n태어난시를 모르면 00(2000101000)으로 입력하세요.'],
            'APP.FORTUNE.STARTUP_SHOW': ['ON', '프로그램 시작시 운세를 출력합니다.\n출력 : ON(기본값), 무시(OFF)'],
            'APP.FORTUNE.STARTUP_TODAY': ['', ''],
        }

        return item_data.get(key, None)

    def _append_item_to_file(self, key, default_value, comment):
        """환경설정 파일에 커멘트와 키=값 추가

        Args:
            key: 키명
            default_value: 기본값
            comment: 커멘트 (여러 줄 가능, \n으로 구분, 빈 문자열이면 커멘트 생략)
        """
        try:
            with open(self.preference_file, 'a', encoding='utf-8') as f:
                f.write('\n')
                # 커멘트 추가 (빈 문자열이 아닐 때만)
                if comment:
                    for comment_line in comment.split('\n'):
                        f.write(f'; {comment_line}\n')
                # 키=값 추가
                f.write(f'{key}={default_value}\n')

            display_message(f"환경설정 파일에 기본값 추가: {key}={default_value}", "INFO")
        except Exception as e:
            display_message(f"환경설정 파일 쓰기 실패: {e}", "ERROR")

    def get(self, section, key):
        """환경설정 값 가져오기

        Args:
            section: 섹션명 (빈 문자열이면 DEFAULT 섹션)
            key: 키명

        Returns:
            설정값 또는 하드코드된 기본값
        """
        with self.lock_manager:
            try:
                # 섹션명이 빈 문자열이면 DEFAULT 섹션에서 가져옴
                if not section:
                    section = configparser.DEFAULTSECT

                # 설정값 조회 시도
                value = self.config.get(section, key, fallback=None)

                # 값이 없으면 기본값 사용 및 파일에 추가
                if value is None:
                    item_data = self._get_item_data(key)
                    if item_data:
                        default_value, comment = item_data
                        # 파일에 커멘트와 기본값 추가
                        self._append_item_to_file(key, default_value, comment)
                        # config에도 반영
                        self._set_without_lock(section, key, default_value)
                        return default_value
                    else:
                        # 정의되지 않은 키
                        return None

                return value
            except Exception as e:
                display_message(f"환경설정 값 읽기 실패: {e}", "ERROR")
                # 오류 발생시에도 기본값 반환 시도
                item_data = self._get_item_data(key)
                if item_data:
                    return item_data[0]  # 기본값만 반환
                return None

    def _set_without_lock(self, section, key, value):
        """환경설정 값 설정 (락 없이, 내부용)

        Args:
            section: 섹션명 (빈 문자열이면 DEFAULT 섹션)
            key: 키명
            value: 설정값
        """
        # 섹션명이 빈 문자열이면 DEFAULT 섹션에 저장
        if not section:
            section = configparser.DEFAULTSECT

        if section != configparser.DEFAULTSECT and not self.config.has_section(section):
            self.config.add_section(section)

        self.config.set(section, key, str(value))

    def set(self, section, key, value):
        """환경설정 값 설정

        Args:
            section: 섹션명 (빈 문자열이면 DEFAULT 섹션)
            key: 키명
            value: 설정값
        """
        with self.lock_manager:
            self._set_without_lock(section, key, value)

    def save(self):
        """환경설정 파일 저장 (주석 보존, 해당 키-값만 업데이트)"""
        with self.lock_manager:
            try:
                # 기존 파일 읽기
                if not os.path.exists(self.preference_file):
                    # 파일이 없으면 새로 생성
                    self._create_default_preference_file()
                    return True

                with open(self.preference_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                # ConfigParser에서 현재 설정값 가져오기
                new_values = {}
                for section in self.config.sections():
                    for key, value in self.config.items(section):
                        new_values[key] = value
                # DEFAULT 섹션도 포함
                for key, value in self.config.items(configparser.DEFAULTSECT):
                    new_values[key] = value

                # 기존 파일 라인별로 처리하여 주석 보존
                updated_lines = []
                for line in lines:
                    stripped = line.strip()

                    # 주석, 빈 줄, 섹션 헤더는 그대로 유지
                    if stripped.startswith(';') or stripped.startswith('#') or not stripped or stripped.startswith('['):
                        updated_lines.append(line.rstrip('\n'))
                        continue

                    # 키=값 라인인 경우
                    if '=' in line:
                        key = line.split('=', 1)[0].strip()

                        # 이 키가 config에 있으면 새 값으로 업데이트
                        if key in new_values:
                            # 기존 라인의 인덴트 유지
                            indent = line[:len(line) - len(line.lstrip())]
                            updated_lines.append(f"{indent}{key}={new_values[key]}")
                            # 처리된 키는 제거 (나중에 추가할 새 키 구분용)
                            del new_values[key]
                        else:
                            # config에 없는 키는 그대로 유지 (사용자 임의 추가 키)
                            updated_lines.append(line.rstrip('\n'))
                    else:
                        # 형식이 맞지 않는 라인은 그대로 유지
                        updated_lines.append(line.rstrip('\n'))

                # config에는 있지만 파일에 없는 새 키 추가 (파일 끝에 추가)
                if new_values:
                    updated_lines.append("")  # 빈 줄 추가
                    for key, value in new_values.items():
                        updated_lines.append(f"{key}={value}")

                # 파일에 저장
                with open(self.preference_file, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(updated_lines) + '\n')

                return True
            except Exception as e:
                display_message(f"환경설정 파일 저장 실패: {e}", "ERROR")
                return False

    def edit(self):
        """텍스트 에디터로 환경설정 파일 편집 (파일 변경 감지)"""
        from .helpers import launch_text_editor
        import time

        display_message("환경설정 파일을 편집합니다.", "INFO")
        display_message(f"파일 경로: {self.preference_file}", "INFO")

        # 파일 수정 시간 기록
        mtime_before = os.path.getmtime(self.preference_file) if os.path.exists(self.preference_file) else 0

        success = launch_text_editor(self.preference_file)

        if success:
            # 파일 수정 시간 확인
            mtime_after = os.path.getmtime(self.preference_file) if os.path.exists(self.preference_file) else 0

            # 파일이 변경되었으면 다시 로드
            if mtime_after > mtime_before:
                try:
                    # 섹션이 없는 파일을 읽기 위해 [DEFAULT] 섹션 추가
                    with open(self.preference_file, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # [DEFAULT] 섹션이 없으면 추가
                    if '[DEFAULT]' not in content and '[' not in content:
                        content_with_section = '[DEFAULT]\n' + content
                        self.config.read_string(content_with_section)
                    else:
                        self.config.read(self.preference_file, encoding='utf-8')

                    display_message("환경설정이 갱신되었습니다.", "INFO")
                except Exception as e:
                    display_message(f"환경설정 파일 읽기 실패: {e}", "ERROR")
            else:
                display_message("환경설정 파일이 변경되지 않았습니다.", "INFO")

        return success

    def reset(self):
        """환경설정 파일 초기화 (기본값으로 재생성)"""
        display_message("환경설정을 기본값으로 초기화합니다.", "INFO")
        self._create_default_preference_file()
