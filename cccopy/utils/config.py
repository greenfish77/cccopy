"""설정 및 프로젝트 관리 모듈"""
import os
import configparser
import fnmatch
import time
import shutil
import getpass
import datetime
import shlex
import glob

from ..core import GitHelper, CCCopyError, LockManager
from .ui_handler import display_message, messagebox
from .permissions import AtomicProductionPermission
from .file_utils import handle_conflict, update_work_git_after_merge, show_git_history
from .helpers import expand_path
from ..models import FileState


class ProductionTagManager:
    """Production tag 관리 (Enhanced Tag with SOURCES hash)"""

    def __init__(self, working_dir, project_manager=None):
        self.working_dir = working_dir
        self.tag_file = os.path.join(working_dir, '.cccopy', 'status', 'production.tag')
        self.project_manager = project_manager  # SOURCES hash 계산용

    def save_production_tag(self, production_dir, include_sources_hash=True):
        """현재 Production HEAD를 tag로 저장 (선택적으로 SOURCES hash 포함)

        Args:
            production_dir: Production 디렉토리 경로
            include_sources_hash: True이면 "commit:sources_hash" 형식, False이면 "commit" 형식

        Tag 형식:
            - 신규 (Enhanced): "abc123def456:7f8a9b2c"
            - 구버전 (호환):  "abc123def456"
        """
        try:
            os.makedirs(os.path.dirname(self.tag_file), exist_ok=True)
            head_commit = GitHelper.get_current_head_commit(production_dir)
            if head_commit:
                if include_sources_hash and self.project_manager:
                    # Enhanced Tag: commit:sources_hash
                    sources_hash = self.project_manager._compute_sources_hash()
                    tag_content = f"{head_commit}:{sources_hash}"
                    display_message(f"Production tag 저장 중: {tag_content}", "DEBUG")
                else:
                    # 구버전 호환: commit만
                    tag_content = head_commit
                    display_message(f"Production tag 저장 중 (구버전): {tag_content}", "DEBUG")

                with open(self.tag_file, 'w') as f:
                    f.write(tag_content)
                display_message(f"Production tag 저장 성공: {self.tag_file}", "DEBUG")
                return True
            else:
                display_message("Production tag 저장 실패: HEAD commit을 가져올 수 없음", "WARN")
                return False
        except Exception as e:
            display_message(f"Production tag 저장 오류: {e}", "ERROR")
            import traceback
            display_message(f"Traceback: {traceback.format_exc()}", "DEBUG")
            return False

    def get_production_tag(self):
        """저장된 Production tag 가져오기 (전체 문자열)"""
        try:
            if os.path.exists(self.tag_file):
                with open(self.tag_file, 'r') as f:
                    return f.read().strip()
            return None
        except:
            return None

    def get_production_tag_parts(self):
        """Production tag를 commit과 sources_hash로 분리

        Returns:
            tuple: (commit_hash, sources_hash or None)
                - 신규 Tag: ("abc123", "7f8a9b2c")
                - 구버전 Tag: ("abc123", None)
                - Tag 없음: (None, None)
        """
        tag = self.get_production_tag()
        if not tag:
            return (None, None)

        if ':' in tag:
            # Enhanced Tag 형식: "commit:sources_hash"
            parts = tag.split(':', 1)
            return (parts[0], parts[1])
        else:
            # 구버전 Tag 형식: "commit"
            return (tag, None)

    def has_production_tag(self):
        """Production tag가 존재하는지 확인"""
        return os.path.exists(self.tag_file) and self.get_production_tag() is not None


class ProjectSelectionManager:
    """프로젝트 선택 및 관리를 위한 클래스"""

    def __init__(self, workspace):
        self.workspace = workspace
        self.personal_config_dir = os.path.expanduser("~/.cccopy/project")
        self.personal_config_file = os.path.join(self.personal_config_dir, "config.ini")

    def show_project_management_menu(self):
        """프로젝트 관리 메인 메뉴 표시"""

        # 화면 지우기 (텍스트 모드에서 깔끔한 표시를 위해)
        os.system('clear' if os.name == 'posix' else 'cls')

        while True:
            print("\n" + "=" * 50)
            print("           프로젝트 관리")
            print("=" * 50)
            print("  1. 신규 프로젝트 생성")
            print("  2. 현재 프로젝트 변경")
            print()
            print("  [ESC/0] 메인 메뉴로 돌아가기")
            print("-" * 50)

            try:
                choice = input("\n선택하세요 (1-2, 0): ").strip()

                if choice == '0' or choice.upper() == 'ESC' or choice == '':
                    break
                elif choice == '1':
                    if self.show_new_project_creation():
                        break  # 프로젝트 생성 성공시 메인으로
                elif choice == '2':
                    if self.show_project_switching():
                        break  # 프로젝트 변경 성공시 메인으로
                else:
                    print("잘못된 선택입니다.")

            except (EOFError, KeyboardInterrupt):
                print("\n취소되었습니다.")
                break

    def show_new_project_creation(self):
        """신규 프로젝트 생성 화면"""
        # project/*.ini 파일 스캔
        template_projects = list(self.workspace.project_configs.keys())

        if not template_projects:
            print("\n[ERROR] 사용 가능한 프로젝트 템플릿이 없습니다.")
            print("project/ 디렉토리에 *.ini 파일을 확인하세요.")
            input("Enter 키를 누르면 돌아갑니다...")
            return False

        while True:
            print("\n" + "=" * 50)
            print("       신규 프로젝트 생성")
            print("=" * 50)
            print("사용 가능한 템플릿:")

            for i, project in enumerate(template_projects, 1):
                print(f"  {i}  {project}")

            print()
            print("  [ESC/0] 뒤로가기")
            print("-" * 50)

            try:
                choice = input(f"\n템플릿을 선택하세요 (1-{len(template_projects)}, 0): ").strip()

                if choice == '0' or choice.upper() == 'ESC' or choice == '':
                    return False

                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(template_projects):
                        selected_template = template_projects[idx]
                        print(f"\n✓ 선택된 템플릿: {selected_template}")

                        # 작업 디렉토리 입력
                        while True:
                            print(f"\n{selected_template} 프로젝트의 작업 디렉토리 경로를 입력하세요:")
                            print("(예: /tmp/cccopy/my_work)")
                            custom_dir = input("경로: ").strip()

                            if not custom_dir:
                                print("경로를 입력해야 합니다.")
                                continue

                            # 중복 경로 체크
                            if self._is_path_already_used(custom_dir):
                                print(f"\n[ERROR] 이미 등록된 경로입니다: {custom_dir}")
                                print("다른 경로를 입력하세요.")
                                continue

                            # TAG 입력 받기
                            print(f"\n{selected_template} 프로젝트의 TAG를 입력하세요 (옵션):")
                            print("빈 입력시 TAG 없이 생성됩니다.")
                            tag = input("TAG: ").strip()

                            # 프로젝트 추가 설정 변경 선택
                            use_custom_settings = False
                            temp_ini_file = None
                            settings_confirmed = False

                            while not settings_confirmed:
                                print("\n" + "=" * 50)
                                print("       프로젝트 추가 설정 변경")
                                print("=" * 50)
                                print("  1. 템플릿 기본값 사용 (권장) [기본값]")
                                print("  2. 변경 진행 (SOURCES 편집)")
                                print()
                                print("  [ESC/0] 뒤로가기")
                                print("-" * 50)

                                settings_choice = input("\n선택하세요 (1-2, Enter=1): ").strip()

                                if settings_choice == '0' or settings_choice.upper() == 'ESC':
                                    # 뒤로가기 - 작업 디렉토리 입력 단계로 돌아감
                                    print("\n취소되었습니다. 작업 디렉토리 입력으로 돌아갑니다.")
                                    break
                                elif settings_choice == '1' or settings_choice == '':
                                    # 기본값 사용
                                    print("\n템플릿 기본값을 사용합니다.")
                                    settings_confirmed = True
                                elif settings_choice == '2':
                                    # 변경 진행 - gedit로 SOURCES 편집
                                    temp_ini_file = self._create_sources_edit_file(selected_template)
                                    if temp_ini_file:
                                        from .helpers import launch_text_editor
                                        if launch_text_editor(temp_ini_file):
                                            use_custom_settings = True
                                            print("\n[OK] SOURCES 편집이 완료되었습니다.")
                                            settings_confirmed = True
                                        else:
                                            print("\n[ERROR] gedit 실행에 실패했습니다.")
                                            if os.path.exists(temp_ini_file):
                                                os.remove(temp_ini_file)
                                            temp_ini_file = None
                                            # 다시 메뉴로 돌아감
                                    else:
                                        print("\n[ERROR] 임시 파일 생성에 실패했습니다.")
                                        # 다시 메뉴로 돌아감
                                else:
                                    print("잘못된 선택입니다.")

                            # 뒤로가기를 선택한 경우 작업 디렉토리 입력으로 돌아감
                            if not settings_confirmed:
                                continue

                            # 프로젝트 등록
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

                                print(f"\n[OK] 프로젝트가 성공적으로 생성되었습니다!")
                                print(f"   템플릿: {selected_template}")
                                if tag:
                                    print(f"   TAG: {tag}")
                                print(f"   작업 경로: {custom_dir}")
                                if use_custom_settings:
                                    print(f"   SOURCES 커스터마이징 적용됨")
                                print(f"   새 프로젝트로 자동 전환되었습니다.")

                                # 자동 Download 실행
                                print(f"\n프로젝트 생성 후 자동 Download를 시작합니다...")
                                try:
                                    self.workspace.download()
                                    print(f"[OK] Download 완료")
                                except Exception as e:
                                    print(f"\n[ERROR] Download 실패: {e}")

                                input("\nEnter 키를 누르면 메인 메뉴로 돌아갑니다...")
                                return True
                            except Exception as e:
                                # 오류 발생시 임시 파일 정리
                                if temp_ini_file and os.path.exists(temp_ini_file):
                                    os.remove(temp_ini_file)
                                print(f"\n[ERROR] 프로젝트 생성 실패: {e}")
                                input("Enter 키를 누르면 계속합니다...")
                                return False
                    else:
                        print("잘못된 번호입니다.")
                except ValueError:
                    print("숫자를 입력하세요.")

            except (EOFError, KeyboardInterrupt):
                print("\n취소되었습니다.")
                return False

    def show_project_switching(self):
        """현재 프로젝트 변경 화면"""
        registered_projects = self._get_registered_projects()

        if not registered_projects:
            print("\n[ERROR] 등록된 프로젝트가 없습니다.")
            print("먼저 신규 프로젝트를 생성하세요.")
            input("Enter 키를 누르면 돌아갑니다...")
            return False

        current_project = self.workspace.get_current_project_name()

        while True:
            print("\n" + "=" * 70)
            print("              현재 프로젝트 변경")
            print("=" * 70)

            for i, (project_count, project_name, work_dir, tag, _) in enumerate(registered_projects, 1):
                current_marker = " [현재]" if str(project_count) == current_project else ""
                prefix = "*" if str(project_count) == current_project else " "

                # 디스플레이 이름 생성
                if tag:
                    display_name = f"{project_name}({tag})"
                else:
                    display_name = project_name

                print(f"  {prefix}{i}  {display_name} ({work_dir}){current_marker}")

            print()
            print("  [Enter] 선택, [E] 편집, [D] 삭제, [C] 복제, [ESC/0] 뒤로가기")
            print("-" * 70)

            try:
                choice = input(f"\n선택하세요 (1-{len(registered_projects)}, E+번호, D+번호, C+번호, 0): ").strip().upper()

                if choice == '0' or choice == 'ESC' or choice == '':
                    return False

                # 편집 명령 처리 (E1, E2 등)
                if choice.startswith('E') and len(choice) > 1:
                    try:
                        idx = int(choice[1:]) - 1
                        if 0 <= idx < len(registered_projects):
                            project_count, project_name, work_dir, tag, _ = registered_projects[idx]
                            padded_project_count = f"{project_count:04d}"

                            # 프로젝트 편집
                            if self.edit_project(padded_project_count, project_name, tag):
                                # 편집된 프로젝트가 현재 프로젝트인 경우 다시 로드
                                if str(project_count) == self.workspace.current_project_number:
                                    display_message("현재 프로젝트 설정이 변경되었습니다. 프로젝트를 다시 로드합니다...", "INFO")
                                    self.workspace._apply_final_config()
                                    display_message("프로젝트 로드 완료", "INFO")

                                # 목록 갱신
                                registered_projects = self._get_registered_projects()

                            continue
                        else:
                            print("잘못된 번호입니다.")
                            continue
                    except ValueError:
                        print("올바른 형식으로 입력하세요. (예: E1, E2)")
                        continue

                # 복제 명령 처리 (C1, C2 등)
                if choice.startswith('C') and len(choice) > 1:
                    try:
                        idx = int(choice[1:]) - 1
                        if 0 <= idx < len(registered_projects):
                            project_count, project_name, work_dir, tag, _ = registered_projects[idx]
                            display_name = f"{project_name}({tag})" if tag else project_name

                            # 복제 확인
                            print(f"\n프로젝트 복제")
                            print(f"   원본 프로젝트: {display_name}")
                            print(f"   원본 작업 경로: {work_dir}")
                            print()

                            # 새 작업 디렉토리 입력
                            while True:
                                print("새 프로젝트의 작업 디렉토리 경로를 입력하세요:")
                                print("(예: /tmp/cccopy/my_work_clone)")
                                new_work_dir = input("경로: ").strip()

                                if not new_work_dir:
                                    print("경로를 입력해야 합니다.")
                                    continue

                                # 중복 경로 체크
                                if self._is_path_already_used(new_work_dir):
                                    print(f"\n[ERROR] 이미 등록된 경로입니다: {new_work_dir}")
                                    print("다른 경로를 입력하세요.")
                                    continue

                                break

                            # 새 TAG 입력 (기본값: 원본 TAG + " (복제됨)")
                            default_tag = f"{tag} (복제됨)" if tag else "(복제됨)"
                            print(f"\n새 프로젝트의 TAG를 입력하세요 (기본값: {default_tag}):")
                            new_tag = input("TAG: ").strip()
                            if not new_tag:
                                new_tag = default_tag

                            # 복제 실행
                            padded_project_count = f"{project_count:04d}"
                            if self.clone_project(padded_project_count, new_work_dir, new_tag):
                                print(f"\n[OK] 프로젝트가 복제되었습니다!")
                                print(f"   원본: {display_name}")
                                print(f"   새 작업 경로: {new_work_dir}")
                                print(f"   새 TAG: {new_tag}")

                                # 자동 Download 실행
                                print(f"\n프로젝트 복제 후 자동 Download를 시작합니다...")
                                try:
                                    self.workspace.download()
                                    print(f"[OK] Download 완료")
                                except Exception as e:
                                    print(f"\n[ERROR] Download 실패: {e}")

                                input("\nEnter 키를 누르면 계속합니다...")
                                # 목록 갱신
                                registered_projects = self._get_registered_projects()
                            else:
                                print(f"\n[ERROR] 프로젝트 복제에 실패했습니다.")
                                input("Enter 키를 누르면 계속합니다...")
                            continue
                        else:
                            print("잘못된 번호입니다.")
                            continue
                    except ValueError:
                        print("올바른 형식으로 입력하세요. (예: C1, C2)")
                        continue

                # 삭제 명령 처리 (D1, D2 등)
                if choice.startswith('D') and len(choice) > 1:
                    try:
                        idx = int(choice[1:]) - 1
                        if 0 <= idx < len(registered_projects):
                            project_count, project_name, work_dir, tag, _ = registered_projects[idx]
                            display_name = f"{project_name}({tag})" if tag else project_name
                            if self._confirm_project_deletion(display_name, work_dir):
                                if self._delete_project(str(project_count)):
                                    print(f"\n[OK] 프로젝트 '{display_name}'가 삭제되었습니다.")
                                    # 목록 갱신
                                    registered_projects = self._get_registered_projects()
                                    if not registered_projects:
                                        print("더 이상 등록된 프로젝트가 없습니다.")
                                        input("Enter 키를 누르면 돌아갑니다...")
                                        return False
                                else:
                                    print(f"\n[ERROR] 프로젝트 삭제에 실패했습니다.")
                            continue
                        else:
                            print("잘못된 번호입니다.")
                            continue
                    except ValueError:
                        print("올바른 형식으로 입력하세요. (예: D1, D2)")
                        continue

                # 프로젝트 선택 처리
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(registered_projects):
                        project_count, project_name, work_dir, tag, _ = registered_projects[idx]
                        display_name = f"{project_name}({tag})" if tag else project_name

                        if str(project_count) == current_project:
                            print(f"\n이미 현재 프로젝트입니다: {display_name}")
                            continue

                        # 프로젝트 변경
                        try:
                            self.workspace._load_project(project_name)
                            # 4자리 패딩된 프로젝트 번호 생성
                            padded_project_number = f"{project_count:04d}"
                            self.workspace.current_project_number = padded_project_number  # 현재 프로젝트 번호 저장
                            self.workspace._apply_final_config()  # 설정 적용하여 working_dir 업데이트
                            self._update_last_project(padded_project_number)
                            print(f"\n[OK] 프로젝트가 변경되었습니다: {display_name}")
                            print(f"   작업 경로: {self.workspace.working_dir}")  # 실제 working_dir 표시
                            input("\nEnter 키를 누르면 메인 메뉴로 돌아갑니다...")
                            return True
                        except Exception as e:
                            print(f"\n[ERROR] 프로젝트 변경 실패: {e}")
                            continue
                    else:
                        print("잘못된 번호입니다.")
                except ValueError:
                    print("숫자를 입력하세요.")

            except (EOFError, KeyboardInterrupt):
                print("\n취소되었습니다.")
                return False

    def _get_registered_projects(self):
        """등록된 프로젝트 목록 조회 (숫자 기반 디렉토리)"""
        projects = []

        if not os.path.exists(self.personal_config_dir):
            return projects

        try:
            for item in os.listdir(self.personal_config_dir):
                project_dir = os.path.join(self.personal_config_dir, item)
                if os.path.isdir(project_dir) and item.isdigit():
                    project_config_file = os.path.join(project_dir, "config.ini")
                    if os.path.exists(project_config_file):
                        config = configparser.ConfigParser()
                        config.read(project_config_file)

                        if (config.has_section('CONFIG') and config.has_option('CONFIG', 'WORKING_BASE_DIR') and
                            config.has_section('INFO') and config.has_option('INFO', 'PROJECT_NAME')):
                            work_dir = config.get('CONFIG', 'WORKING_BASE_DIR')
                            project_name = config.get('INFO', 'PROJECT_NAME')
                            tag = config.get('INFO', 'TAG', fallback='')
                            create_date = config.get('INFO', 'CREATE_DATE', fallback='')
                            projects.append((int(item), project_name, work_dir, tag, create_date))

            # 숫자 순서로 정렬
            projects.sort(key=lambda x: x[0])

        except Exception as e:
            # TUI에서 오류 처리하므로 여기서는 제거 (curses 화면 깨짐 방지)
            # display_message(f"프로젝트 목록 조회 중 오류: {e}", "ERROR")
            pass

        return projects

    def _is_path_already_used(self, path):
        """경로가 이미 사용중인지 확인"""
        registered_projects = self._get_registered_projects()
        return any(work_dir == path for _, _, work_dir, _, _ in registered_projects)

    def _confirm_project_deletion(self, project_name, work_dir):
        """프로젝트 삭제 확인"""
        print(f"\n[WARNING] 프로젝트 삭제 확인")
        print(f"   프로젝트: {project_name}")
        print(f"   작업 경로: {work_dir}")
        print("\n정말로 이 프로젝트를 삭제하시겠습니까?")
        print("(프로젝트 설정만 제거되며, 실제 파일은 그대로 남습니다)")

        while True:
            confirm = input("\n삭제하시겠습니까? (y/N): ").strip().lower()
            if confirm in ['y', 'yes']:
                return True
            elif confirm in ['n', 'no', '']:
                return False
            else:
                print("y 또는 n을 입력하세요.")

    def _delete_project(self, project_name):
        """프로젝트 삭제"""
        try:
            project_dir = os.path.join(self.personal_config_dir, project_name)

            if os.path.exists(project_dir):
                import shutil
                shutil.rmtree(project_dir)
                display_message(f"프로젝트 설정 디렉토리 삭제 완료: {project_dir}", "INFO")
                # LAST_PROJECT 갱신
                self._update_last_project_after_deletion(project_name)
                return True  # 실제 삭제 성공
            else:
                # 경로가 존재하지 않지만 삭제 성공으로 처리 (이미 삭제된 상태)
                display_message(f"프로젝트 설정 디렉토리가 이미 존재하지 않음: {project_dir}", "INFO")
                # LAST_PROJECT 갱신
                self._update_last_project_after_deletion(project_name)
                return True  # 이미 없으므로 삭제 성공으로 간주

        except Exception as e:
            display_message(f"프로젝트 삭제 중 오류: {e}", "ERROR")
            return False

    def edit_project(self, project_number, project_name, tag):
        """프로젝트 설정 편집

        Args:
            project_number: 편집할 프로젝트 번호 (4자리 문자열)
            project_name: 프로젝트 이름
            tag: 프로젝트 TAG

        Returns:
            bool: 편집 성공 여부 (파일이 변경되었는지 여부)
        """
        from .helpers import launch_text_editor

        try:
            # 프로젝트 설정 파일 경로
            project_dir = os.path.join(self.personal_config_dir, project_number)
            config_file = os.path.join(project_dir, "config.ini")

            if not os.path.exists(config_file):
                display_message(f"프로젝트 설정 파일을 찾을 수 없습니다: {config_file}", "ERROR")
                return False

            # 프로젝트 정보 로깅
            display_name = f"{project_name}({tag})" if tag else project_name
            display_message(f"프로젝트 편집 시작: {display_name}", "INFO")
            display_message(f"설정 파일: {config_file}", "DEBUG")

            # 파일 수정 시간 기록
            mtime_before = os.path.getmtime(config_file)

            # 텍스트 에디터 실행
            success = launch_text_editor(config_file)

            if success:
                # 파일 수정 시간 확인
                mtime_after = os.path.getmtime(config_file)

                # 파일이 변경되었는지 확인
                if mtime_after > mtime_before:
                    display_message(f"프로젝트 설정이 변경되었습니다: {display_name}", "INFO")
                    return True
                else:
                    display_message(f"프로젝트 설정이 변경되지 않았습니다: {display_name}", "INFO")
                    return False
            else:
                display_message("에디터 실행에 실패했습니다", "ERROR")
                return False

        except Exception as e:
            display_message(f"프로젝트 편집 중 오류: {e}", "ERROR")
            return False

    def clone_project(self, source_project_number, new_working_dir, new_tag):
        """프로젝트 복제

        Args:
            source_project_number: 복제할 원본 프로젝트 번호 (4자리 문자열)
            new_working_dir: 새 프로젝트의 작업 디렉토리
            new_tag: 새 프로젝트의 TAG

        Returns:
            bool: 복제 성공 여부
        """
        try:
            # 원본 프로젝트 설정 파일 경로
            source_project_dir = os.path.join(self.personal_config_dir, source_project_number)
            source_config_file = os.path.join(source_project_dir, "config.ini")

            if not os.path.exists(source_config_file):
                display_message(f"원본 프로젝트 설정을 찾을 수 없습니다: {source_project_number}", "ERROR")
                return False

            # 원본 프로젝트 설정 읽기
            source_config = configparser.ConfigParser()
            source_config.read(source_config_file, encoding='utf-8')

            if not source_config.has_section('INFO') or not source_config.has_option('INFO', 'PROJECT_NAME'):
                display_message("원본 프로젝트 설정이 올바르지 않습니다.", "ERROR")
                return False

            # 원본 프로젝트 정보 가져오기
            source_project_name = source_config.get('INFO', 'PROJECT_NAME')
            source_working_dir = source_config.get('CONFIG', 'WORKING_BASE_DIR')

            # 새 프로젝트 번호 발급
            new_project_number = self.workspace._get_next_project_number()
            new_project_dir = os.path.join(self.personal_config_dir, new_project_number)
            os.makedirs(new_project_dir, exist_ok=True)

            # 설정 파일 복사 (config.ini)
            new_config = configparser.ConfigParser()

            # 원본 설정의 모든 섹션 복사
            for section in source_config.sections():
                if section != 'INFO':  # INFO 섹션은 나중에 새로 작성
                    if not new_config.has_section(section):
                        new_config.add_section(section)
                    for key, value in source_config.items(section):
                        new_config.set(section, key, value)

            # WORKING_BASE_DIR 업데이트
            if not new_config.has_section('CONFIG'):
                new_config.add_section('CONFIG')
            new_config.set('CONFIG', 'WORKING_BASE_DIR', new_working_dir)

            # INFO 섹션 새로 작성
            if not new_config.has_section('INFO'):
                new_config.add_section('INFO')
            new_config.set('INFO', 'PROJECT_NAME', source_project_name)
            new_config.set('INFO', 'TAG', new_tag)
            new_config.set('INFO', 'CREATE_DATE', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

            # 새 설정 파일 저장
            new_config_file = os.path.join(new_project_dir, "config.ini")
            with open(new_config_file, 'w', encoding='utf-8') as f:
                new_config.write(f)

            # 원본 작업 디렉토리에서 파일 복사 (.git 제외)
            if os.path.exists(source_working_dir):
                display_message(f"작업 디렉토리 복사 중: {source_working_dir} → {new_working_dir}", "INFO")

                # 새 작업 디렉토리 생성
                os.makedirs(new_working_dir, exist_ok=True)

                # .git을 제외한 모든 파일/디렉토리 복사
                for item in os.listdir(source_working_dir):
                    if item == '.git':
                        continue

                    source_item = os.path.join(source_working_dir, item)
                    dest_item = os.path.join(new_working_dir, item)

                    if os.path.isdir(source_item):
                        shutil.copytree(source_item, dest_item)
                    else:
                        shutil.copy2(source_item, dest_item)

                display_message("작업 디렉토리 복사 완료", "INFO")
            else:
                display_message(f"원본 작업 디렉토리가 존재하지 않습니다: {source_working_dir}", "WARN")
                display_message("새 작업 디렉토리만 생성합니다.", "INFO")
                os.makedirs(new_working_dir, exist_ok=True)

            # LAST_PROJECT 업데이트
            self._update_last_project(new_project_number)

            display_message(f"프로젝트 복제 완료: {new_project_number}", "INFO")
            display_message(f"작업 디렉토리: {new_working_dir}", "INFO")
            display_message(f"TAG: {new_tag}", "INFO")

            return True

        except Exception as e:
            display_message(f"프로젝트 복제 중 오류: {e}", "ERROR")
            import traceback
            display_message(traceback.format_exc(), "DEBUG")
            return False

    def _update_last_project(self, project_name):
        """LAST_PROJECT 설정 업데이트"""
        try:
            os.makedirs(self.personal_config_dir, exist_ok=True)

            config = configparser.ConfigParser()
            if os.path.exists(self.personal_config_file):
                config.read(self.personal_config_file)

            if not config.has_section('CONFIG'):
                config.add_section('CONFIG')

            config.set('CONFIG', 'LAST_PROJECT', project_name)

            with open(self.personal_config_file, 'w') as f:
                config.write(f)

        except Exception as e:
            # TUI에서 오류 처리하므로 여기서는 제거 (curses 화면 깨짐 방지)
            pass

    def _update_last_project_after_deletion(self, deleted_project_name):
        """프로젝트 삭제 후 LAST_PROJECT 갱신"""
        try:
            # 등록된 프로젝트 목록 가져오기
            registered_projects = self._get_registered_projects()

            if not registered_projects:
                # 프로젝트가 하나도 없으면 LAST_PROJECT 제거
                if os.path.exists(self.personal_config_file):
                    config = configparser.ConfigParser()
                    config.read(self.personal_config_file)
                    if config.has_section('CONFIG') and config.has_option('CONFIG', 'LAST_PROJECT'):
                        config.remove_option('CONFIG', 'LAST_PROJECT')
                        with open(self.personal_config_file, 'w') as f:
                            config.write(f)
                        display_message("모든 프로젝트가 삭제되어 LAST_PROJECT 설정을 제거했습니다.", "INFO")
                return

            # 삭제된 프로젝트 번호 찾기
            deleted_number = int(deleted_project_name)

            # 삭제된 프로젝트보다 큰 번호 중 가장 작은 번호 찾기
            next_project = None
            for project_count, project_name, work_dir, tag, create_date in registered_projects:
                if project_count > deleted_number:
                    next_project = f"{project_count:04d}"
                    break

            # 못 찾으면 첫 번째 프로젝트 선택
            if not next_project:
                first_project = registered_projects[0]
                next_project = f"{first_project[0]:04d}"

            # LAST_PROJECT 업데이트
            self._update_last_project(next_project)
            display_message(f"LAST_PROJECT를 {next_project}로 업데이트했습니다.", "INFO")

        except Exception as e:
            display_message(f"LAST_PROJECT 갱신 중 오류: {e}", "WARN")

    def _create_sources_edit_file(self, project_name):
        """SOURCES 편집을 위한 임시 파일 생성"""
        import tempfile

        try:
            # 템플릿 프로젝트의 설정 파일 읽기
            if project_name not in self.workspace.project_configs:
                display_message(f"프로젝트를 찾을 수 없습니다: {project_name}", "ERROR")
                return None

            config_file, config = self.workspace.project_configs[project_name]

            # 임시 파일 생성
            fd, temp_path = tempfile.mkstemp(suffix='.ini', prefix='cccopy_sources_')
            os.close(fd)

            # 주석과 함께 SOURCES 섹션 작성
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write("; ===================================================================\n")
                f.write("; CCCopy 프로젝트 SOURCES 편집\n")
                f.write("; ===================================================================\n")
                f.write(";\n")
                f.write("; Production 경로에서 관리할 파일들의 목록을 구성하세요.\n")
                f.write(";\n")
                f.write("; 패턴 사용법:\n")
                f.write(";   AAA/**           - AAA에 있는 모든 파일 (하위 경로 포함)\n")
                f.write(";   AAA/*            - AAA에 있는 모든 파일 (하위 경로 미포함)\n")
                f.write(";   AAA/**/*.txt     - AAA에 있는 모든 *.txt 파일 (하위 경로 포함)\n")
                f.write(";   AAA/**/*.py      - AAA에 있는 모든 *.py 파일 (하위 경로 포함)\n")
                f.write(";   AAA/file.txt     - 특정 파일 하나만\n")
                f.write(";   AAA/B??/file.txt - 와일드카드 사용 (B로 시작하는 3글자 디렉토리)\n")
                f.write(";\n")
                f.write("; 주의사항:\n")
                f.write(";   - 번호는 00부터 시작합니다 (00, 01, 02, ...)\n")
                f.write(";   - 경로 구분자는 / 를 사용합니다 (Windows에서도 / 사용)\n")
                f.write(";   - 상대 경로로 작성합니다 (Production 디렉토리 기준)\n")
                f.write(";   - 대소문자를 구분합니다 (AAA/file.txt ≠ aaa/file.txt)\n")
                f.write(";\n")
                f.write("; [CONFIG] 섹션을 추가하면 다른 설정도 오버라이드할 수 있습니다.\n")
                f.write("; 예시:\n")
                f.write(";   [CONFIG]\n")
                f.write(";   PRODUCTION_DIR=/custom/path\n")
                f.write(";\n")
                f.write("; ===================================================================\n")
                f.write("\n")

                # 기존 SOURCES 섹션을 주석으로 표시 (참고용)
                f.write("; 템플릿의 [SOURCES] 패턴 (참고용 - 필요시 아래 주석을 해제하고 수정):\n")
                f.write("; [SOURCES]\n")
                if config.has_section('SOURCES'):
                    for key, value in config.items('SOURCES'):
                        f.write(f"; {key}={value}\n")
                else:
                    f.write("; 00=src/**\n")
                    f.write("; 01=docs/**\n")
                    f.write("; 02=config/*.ini\n")
                f.write(";\n")
                f.write("; [SOURCES] 섹션을 추가하려면:\n")
                f.write(";   1. 위의 '; [SOURCES]' 줄에서 ';'를 제거\n")
                f.write(";   2. 필요한 패턴 줄에서 ';'를 제거\n")
                f.write(";   3. 패턴을 원하는 대로 수정\n")
                f.write(";\n")
                f.write("; [SOURCES]를 추가하지 않으면 템플릿의 기본 패턴이 사용됩니다.\n")

            display_message(f"임시 편집 파일 생성: {temp_path}", "INFO")
            return temp_path

        except Exception as e:
            display_message(f"임시 파일 생성 실패: {e}", "ERROR")
            return None


class ProjectManager:
    """프로젝트 관리자 - 여러 프로젝트 지원"""

    def __init__(self, project_name=None, cache_timeout=300):
        """
        프로젝트 관리자 초기화
        project_name이 None인 경우 자동으로 선택 과정을 시작
        cache_timeout: Production 자동 커밋 체크 캐시 타임아웃 (초 단위, 기본 300초=5분)
        """
        self.project_configs = {}  # project_name -> (config_file_path, config)
        self.selected_project = None
        self.current_project_number = None  # 현재 프로젝트 번호 저장용
        self.config = None
        self.personal_config_dir = os.path.expanduser("~/.cccopy/project")
        self.personal_config_file = os.path.join(self.personal_config_dir, "config.ini")

        # Production 자동 커밋 체크 캐싱 (Partial Refresh 성능 개선)
        # main.py의 PARTIAL_REFRESH_CACHE_TIMEOUT 값 사용
        self.last_production_check_time = 0
        self.production_check_timeout = cache_timeout

        # 기존 프로젝트 마이그레이션 (숫자 기반으로 변경)
        self._migrate_old_projects()

        # 프로젝트 설정 스캔 및 검증
        self._scan_project_configs()

        if project_name:
            self._load_project(project_name)
        else:
            # 자동 선택 또는 다이얼로그 필요
            self._auto_select_or_setup_project()

        # 최종 설정 적용
        self._apply_final_config()

    def _scan_project_configs(self):
        """project/*.ini 파일들을 스캔하고 PROJECT_NAME 중복 검사"""
        # 환경 변수로 템플릿 디렉토리 경로 지정 가능
        project_dir = os.environ.get('CCCOPY_PROJECT_TEMPLATE_DIR')

        if project_dir:
            # 환경 변수로 지정된 경로 사용
            project_dir = os.path.abspath(os.path.expanduser(project_dir))
        else:
            # 기본 경로: 스크립트 파일 위치 기준으로 절대 경로 구성
            # config.py는 cccopy/utils/config.py 위치
            # project/는 cccopy 패키지의 2단계 상위 디렉토리
            script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            project_dir = os.path.join(script_dir, "project")

        if not os.path.exists(project_dir):
            raise CCCopyError(f"Project 디렉토리가 없습니다: {project_dir}")

        ini_files = []
        for file in os.listdir(project_dir):
            if file.endswith('.ini'):
                ini_files.append(os.path.join(project_dir, file))


        if not ini_files:
            raise CCCopyError("project/*.ini 파일이 없습니다")

        project_names = set()

        for config_file in ini_files:
            try:
                config = configparser.ConfigParser()
                config.read(config_file)

                if not config.has_section('CONFIG') or not config.has_option('CONFIG', 'PROJECT_NAME'):
                    display_message(f"경고: {config_file}에 PROJECT_NAME이 없습니다", "WARN")
                    continue

                project_name = config.get('CONFIG', 'PROJECT_NAME')

                if project_name in project_names:
                    raise CCCopyError(f"중복된 PROJECT_NAME '{project_name}' 발견: {config_file}")

                project_names.add(project_name)
                self.project_configs[project_name] = (config_file, config)

            except Exception as e:
                display_message(f"설정 파일 읽기 오류 {config_file}: {e}", "ERROR")


    def _auto_select_or_setup_project(self):
        """개인 설정을 읽어 자동 선택하거나 설정 필요 (숫자 기반 디렉토리 지원)"""
        if os.path.exists(self.personal_config_file):
            # 기존 설정 읽기
            personal_config = configparser.ConfigParser()
            personal_config.read(self.personal_config_file)

            if personal_config.has_section('CONFIG') and personal_config.has_option('CONFIG', 'LAST_PROJECT'):
                last_project_number = personal_config.get('CONFIG', 'LAST_PROJECT')

                # 숫자 기반 디렉토리에서 실제 프로젝트 이름 찾기
                project_dir = os.path.join(self.personal_config_dir, last_project_number)
                if os.path.isdir(project_dir):
                    project_config_file = os.path.join(project_dir, "config.ini")
                    if os.path.exists(project_config_file):
                        config = configparser.ConfigParser()
                        config.read(project_config_file)
                        if (config.has_section('INFO') and config.has_option('INFO', 'PROJECT_NAME')):
                            actual_project_name = config.get('INFO', 'PROJECT_NAME')
                            if actual_project_name in self.project_configs:
                                self.selected_project = actual_project_name  # 실제 프로젝트 이름으로 저장
                                self.current_project_number = last_project_number  # 프로젝트 번호 저장
                                display_message(f"마지막 프로젝트 자동 선택: {actual_project_name} ({last_project_number})")
                                return

                display_message(f"마지막 프로젝트 '{last_project_number}'를 찾을 수 없음", "WARN")

        # last_project가 없거나 찾을 수 없는 경우 첫 번째 프로젝트 자동 선택 시도
        self._try_auto_select_first_project()

    def _find_project_number_by_name(self, project_name):
        """프로젝트 이름으로 프로젝트 번호 찾기"""
        try:
            # 모든 숫자 디렉토리 스캔
            for entry in os.listdir(self.personal_config_dir):
                if entry.isdigit() or (len(entry) == 4 and entry.isdigit()):
                    project_dir = os.path.join(self.personal_config_dir, entry)
                    if os.path.isdir(project_dir):
                        config_file = os.path.join(project_dir, "config.ini")
                        if os.path.exists(config_file):
                            config = configparser.ConfigParser()
                            config.read(config_file)
                            if config.has_section('INFO') and config.has_option('INFO', 'PROJECT_NAME'):
                                if config.get('INFO', 'PROJECT_NAME') == project_name:
                                    return entry
        except Exception as e:
            display_message(f"프로젝트 번호 찾기 실패: {e}", "WARN")
        return None

    def _try_auto_select_first_project(self):
        """첫 번째 등록된 프로젝트를 자동으로 선택"""
        try:
            # ProjectSelectionManager를 사용하여 등록된 프로젝트 목록 가져오기
            project_selection_manager = ProjectSelectionManager(self)
            registered_projects = project_selection_manager._get_registered_projects()

            if registered_projects:
                # 첫 번째 프로젝트 선택
                first_project = registered_projects[0]
                project_count, project_name, work_dir, tag, create_date = first_project

                if project_name in self.project_configs:
                    self.selected_project = project_name
                    self.current_project_number = f"{project_count:04d}"

                    # LAST_PROJECT 설정 업데이트
                    project_selection_manager._update_last_project(self.current_project_number)

                    display_message(f"첫 번째 프로젝트 자동 선택: {project_name} ({self.current_project_number})")
                    return

            # 등록된 프로젝트가 없는 경우
            display_message("등록된 프로젝트가 없습니다. 프로젝트 선택이 필요합니다.", "INFO")
            self.selected_project = None

        except Exception as e:
            display_message(f"첫 번째 프로젝트 자동 선택 실패: {e}", "WARN")
            self.selected_project = None

    def _load_project(self, project_name):
        """특정 프로젝트 로드"""
        if project_name not in self.project_configs:
            raise CCCopyError(f"프로젝트를 찾을 수 없습니다: {project_name}")

        self.selected_project = project_name

        # 프로젝트 이름으로 current_project_number 찾기
        self.current_project_number = self._find_project_number_by_name(project_name)

        display_message(f"프로젝트 선택: {project_name}")

    def _apply_final_config(self):
        """최종 설정 적용 (프로젝트 설정 + 개인 오버라이드)"""
        if not self.selected_project:
            return

        _, base_config = self.project_configs[self.selected_project]

        # 기본 설정 복사
        self.config = configparser.ConfigParser()
        for section_name in base_config.sections():
            self.config.add_section(section_name)
            for key, value in base_config.items(section_name):
                self.config.set(section_name, key, value)

        # 개인 설정으로 오버라이드 (숫자 기반 디렉토리 지원)
        # current_project_number가 있으면 사용, 없으면 프로젝트 이름으로 찾기
        if hasattr(self, 'current_project_number') and self.current_project_number:
            project_dir = self.current_project_number
        else:
            # 기존 로직: 프로젝트 이름으로 찾기 (초기 로딩시)
            project_dir = self.selected_project

        # 프로젝트 개인 설정 디렉토리 저장 (production.tag 저장용)
        self.project_personal_dir = os.path.join(self.personal_config_dir, project_dir)

        personal_project_config = os.path.join(self.project_personal_dir, "config.ini")
        if os.path.exists(personal_project_config):
            personal_config = configparser.ConfigParser()
            personal_config.read(personal_project_config)

            # 섹션별 오버라이드 정책
            REPLACE_SECTIONS = ['SOURCES', 'EXCLUDES']  # 전체 교체할 섹션

            for section_name in personal_config.sections():
                # SOURCES, EXCLUDES는 전체 교체 (키 병합 방지)
                if section_name in REPLACE_SECTIONS:
                    # 기존 섹션 삭제 후 재생성
                    if self.config.has_section(section_name):
                        self.config.remove_section(section_name)
                    self.config.add_section(section_name)
                    display_message(f"개인 설정으로 [{section_name}] 섹션 전체 교체")
                elif not self.config.has_section(section_name):
                    self.config.add_section(section_name)

                # 개인 설정 값 적용
                for key, value in personal_config.items(section_name):
                    self.config.set(section_name, key, value)
                    display_message(f"개인 설정 적용: [{section_name}] {key} = {value}")

        # 작업 디렉토리 설정 (~ 와 환경변수 확장 지원)
        self.production_dir = expand_path(self.config.get('CONFIG', 'PRODUCTION_DIR'))
        self.working_dir = expand_path(self.config.get('CONFIG', 'WORKING_BASE_DIR'))

        # ProductionTagManager에 project_personal_dir 전달 (production.tag는 ~/.cccopy/{project_number}/에 저장)
        self.tag_manager = ProductionTagManager(self.project_personal_dir, project_manager=self)

        os.makedirs(self.working_dir, exist_ok=True)
        os.makedirs(os.path.join(self.working_dir, '.cccopy'), exist_ok=True)

    def get_available_projects(self):
        """사용 가능한 프로젝트 목록 반환"""
        return list(self.project_configs.keys())

    def needs_project_selection(self):
        """프로젝트 선택이 필요한지 확인"""
        return self.selected_project is None

    def select_project_and_setup(self, project_name, custom_working_dir=None, tag="", custom_ini_file=None):
        """프로젝트 선택 및 개인 설정 저장 (숫자 기반 디렉토리 지원)

        Args:
            project_name: 프로젝트명
            custom_working_dir: 커스텀 작업 디렉토리 (선택)
            tag: 프로젝트 TAG (선택)
            custom_ini_file: 커스텀 설정 파일 경로 (SOURCES 등 오버라이드, 선택)
        """
        if project_name not in self.project_configs:
            raise CCCopyError(f"프로젝트를 찾을 수 없습니다: {project_name}")

        self.selected_project = project_name

        # 다음 프로젝트 숫자 발급
        project_number = self._get_next_project_number()
        self.current_project_number = project_number  # 현재 프로젝트 번호 설정

        # 개인 설정 디렉토리 생성
        os.makedirs(self.personal_config_dir, exist_ok=True)
        project_personal_dir = os.path.join(self.personal_config_dir, project_number)
        os.makedirs(project_personal_dir, exist_ok=True)

        # 마지막 프로젝트 저장 (숫자로 저장)
        personal_config = configparser.ConfigParser()
        if os.path.exists(self.personal_config_file):
            personal_config.read(self.personal_config_file)
        if not personal_config.has_section('CONFIG'):
            personal_config.add_section('CONFIG')
        personal_config.set('CONFIG', 'LAST_PROJECT', project_number)

        with open(self.personal_config_file, 'w') as f:
            personal_config.write(f)

        # 프로젝트별 개인 설정 저장 (config.ini로 이름 변경)
        project_personal_config = os.path.join(project_personal_dir, "config.ini")
        project_config = configparser.ConfigParser()

        # CONFIG 섹션
        if not project_config.has_section('CONFIG'):
            project_config.add_section('CONFIG')

        # WORKING_BASE_DIR은 항상 저장 (custom_working_dir이 없으면 기본값 사용)
        if custom_working_dir:
            project_config.set('CONFIG', 'WORKING_BASE_DIR', custom_working_dir)
        else:
            # 기본 작업 디렉토리 사용
            _, base_config = self.project_configs[project_name]  # tuple에서 config 객체 추출
            default_working_dir = base_config.get('CONFIG', 'WORKING_BASE_DIR')
            project_config.set('CONFIG', 'WORKING_BASE_DIR', default_working_dir)

        # 커스텀 ini 파일이 있으면 내용을 읽어서 반영
        # SOURCES, EXCLUDES는 제외 (주석으로만 표시)
        if custom_ini_file and os.path.exists(custom_ini_file):
            try:
                custom_config = configparser.ConfigParser()
                custom_config.read(custom_ini_file, encoding='utf-8')

                # SOURCES, EXCLUDES를 제외한 섹션만 반영
                SKIP_SECTIONS = ['SOURCES', 'EXCLUDES']

                for section in custom_config.sections():
                    if section in SKIP_SECTIONS:
                        display_message(f"[{section}] 섹션은 템플릿 주석으로만 제공됩니다 (직접 편집 필요)", "INFO")
                        continue

                    if not project_config.has_section(section):
                        project_config.add_section(section)

                    for key, value in custom_config.items(section):
                        project_config.set(section, key, value)
                        display_message(f"커스텀 설정 적용: [{section}] {key} = {value}", "INFO")

            except Exception as e:
                display_message(f"커스텀 ini 파일 읽기 실패: {e}", "WARN")

        # INFO 섹션 추가 (커스텀 설정 후에 추가하여 덮어쓰기 방지)
        if not project_config.has_section('INFO'):
            project_config.add_section('INFO')
        project_config.set('INFO', 'PROJECT_NAME', project_name)
        project_config.set('INFO', 'TAG', tag if tag else "")

        from datetime import datetime
        project_config.set('INFO', 'CREATE_DATE', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        # config.ini 파일 작성 (SOURCES 템플릿 주석 포함)
        with open(project_personal_config, 'w') as f:
            # 템플릿의 SOURCES 섹션을 주석으로 추가
            _, base_config = self.project_configs[project_name]

            # 주석 헤더 작성
            f.write("# CCCopy 프로젝트 개인 설정 파일\n")
            f.write("# 이 파일은 템플릿 설정을 오버라이드합니다.\n")
            f.write("#\n")
            f.write("# [SOURCES] 섹션 사용법:\n")
            f.write("#   - 추적할 파일/디렉토리 패턴을 지정합니다\n")
            f.write("#   - glob 패턴 사용 가능 (예: AAA/**, *.cpp, **/test/*.h)\n")
            f.write("#   - 여러 패턴을 00, 01, 02... 형식으로 추가\n")
            f.write("#   - 이 파일에 [SOURCES]를 추가하면 템플릿의 SOURCES를 완전히 대체합니다\n")
            f.write("#\n")
            f.write("# [EXCLUDES] 섹션 사용법:\n")
            f.write("#   - 제외할 파일/디렉토리 패턴을 지정합니다\n")
            f.write("#   - 예: **/.git/ (모든 .git 디렉토리)\n")
            f.write("#   - 예: **/__pycache__/ (모든 Python 캐시)\n")
            f.write("#   - 예: **/backup/ (모든 backup 디렉토리)\n")
            f.write("#   - 예: **/*.log (모든 .log 파일)\n")
            f.write("#\n")

            # 템플릿의 SOURCES 섹션을 주석으로 추가
            if base_config.has_section('SOURCES'):
                f.write("# 템플릿의 [SOURCES] 패턴 (참고용):\n")
                f.write("# [SOURCES]\n")
                for key in sorted(base_config['SOURCES'].keys()):
                    value = base_config.get('SOURCES', key)
                    f.write(f"# {key}={value}\n")
                f.write("#\n")

            # 템플릿의 EXCLUDES 섹션도 주석으로 추가
            if base_config.has_section('EXCLUDES'):
                f.write("# 템플릿의 [EXCLUDES] 패턴 (참고용):\n")
                f.write("# [EXCLUDES]\n")
                for key in sorted(base_config['EXCLUDES'].keys()):
                    value = base_config.get('EXCLUDES', key)
                    f.write(f"# {key}={value}\n")
                f.write("#\n\n")

            # 실제 설정 작성
            project_config.write(f)

        # 최종 설정 적용
        self._apply_final_config()

        display_message(f"프로젝트 설정 완료: {project_name} (번호: {project_number})")
        if tag:
            display_message(f"TAG: {tag}")
        if custom_working_dir:
            display_message(f"작업 디렉토리: {custom_working_dir}")
        if custom_ini_file:
            display_message(f"커스텀 설정 적용됨")

    def get_current_project_name(self):
        """현재 선택된 프로젝트 이름 반환"""
        return self.selected_project

    def get_project_info(self, project_name):
        """프로젝트 정보 반환"""
        if project_name not in self.project_configs:
            return None
        config_file, config = self.project_configs[project_name]

        # 경로 확장 (~ 와 환경변수 지원)
        prod_dir = config.get('CONFIG', 'PRODUCTION_DIR', fallback='N/A')
        work_dir = config.get('CONFIG', 'WORKING_BASE_DIR', fallback='N/A')

        return {
            'name': project_name,
            'config_file': config_file,
            'production_dir': expand_path(prod_dir) if prod_dir != 'N/A' else prod_dir,
            'working_base_dir': expand_path(work_dir) if work_dir != 'N/A' else work_dir
        }

    def _migrate_old_projects(self):
        """기존 프로젝트를 숫자 기반 구조로 마이그레이션"""
        if not os.path.exists(self.personal_config_dir):
            return

        migrated_count = 0
        migration_log = []

        try:
            for item in os.listdir(self.personal_config_dir):
                project_dir = os.path.join(self.personal_config_dir, item)
                if os.path.isdir(project_dir) and not item.isdigit():  # 숫자가 아닌 기존 프로젝트
                    old_config_file = os.path.join(project_dir, "project.ini")
                    if os.path.exists(old_config_file):
                        # 새 프로젝트 숫자 발급
                        project_number = self._get_next_project_number()
                        new_project_dir = os.path.join(self.personal_config_dir, project_number)

                        try:
                            # 기존 설정 읽기
                            old_config = configparser.ConfigParser()
                            old_config.read(old_config_file)

                            # 새 디렉토리 생성
                            os.makedirs(new_project_dir, exist_ok=True)
                            new_config_file = os.path.join(new_project_dir, "config.ini")

                            # 새 설정 파일 생성
                            new_config = configparser.ConfigParser()

                            # CONFIG 섹션 복사
                            if old_config.has_section('CONFIG'):
                                new_config.add_section('CONFIG')
                                for key, value in old_config.items('CONFIG'):
                                    new_config.set('CONFIG', key, value)

                            # INFO 섹션 추가
                            if not new_config.has_section('INFO'):
                                new_config.add_section('INFO')
                            new_config.set('INFO', 'PROJECT_NAME', item)  # 기존 디렉토리명을 프로젝트명으로 사용
                            new_config.set('INFO', 'TAG', '')  # TAG는 빈 값으로 설정

                            from datetime import datetime
                            new_config.set('INFO', 'CREATE_DATE', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

                            # 새 파일에 저장
                            with open(new_config_file, 'w') as f:
                                new_config.write(f)

                            # 기존 디렉토리 삭제
                            import shutil
                            shutil.rmtree(project_dir)

                            migrated_count += 1
                            migration_log.append(f"프로젝트 '{item}' -> '{project_number}' 마이그레이션 완료")

                        except Exception as e:
                            migration_log.append(f"프로젝트 '{item}' 마이그레이션 실패: {e}")

        except Exception as e:
            migration_log.append(f"마이그레이션 오류: {e}")

        # 마이그레이션 결과 출력
        if migrated_count > 0:
            display_message(f"프로젝트 마이그레이션 완료: {migrated_count}개 프로젝트")
            for log in migration_log:
                display_message(log)

        # LAST_PROJECT 설정 업데이트 (기존 프로젝트명에서 숫자로 변경)
        if migrated_count > 0 and os.path.exists(self.personal_config_file):
            try:
                personal_config = configparser.ConfigParser()
                personal_config.read(self.personal_config_file)
                if (personal_config.has_section('CONFIG') and
                    personal_config.has_option('CONFIG', 'LAST_PROJECT')):
                    last_project = personal_config.get('CONFIG', 'LAST_PROJECT')

                    # 숫자가 아닌 경우, 마이그레이션된 프로젝트 숫자로 업데이트
                    if not last_project.isdigit():
                        # 마이그레이션 후 프로젝트 목록을 다시 스캔하여 숫자 찾기
                        registered_projects = self._get_registered_projects_from_personal_dir()
                        for project_count, project_name, _, _, _ in registered_projects:
                            if project_name == last_project:
                                personal_config.set('CONFIG', 'LAST_PROJECT', str(project_count))
                                with open(self.personal_config_file, 'w') as f:
                                    personal_config.write(f)
                                display_message(f"LAST_PROJECT 설정 업데이트: {last_project} -> {project_count}")
                                break
            except Exception as e:
                display_message(f"LAST_PROJECT 업데이트 오류: {e}", "WARN")

    def _get_next_project_number(self):
        """다음 프로젝트 숫자 반환 (4자리 이상)"""
        if not os.path.exists(self.personal_config_dir):
            return "0001"

        max_num = 0
        try:
            for item in os.listdir(self.personal_config_dir):
                if item.isdigit():
                    max_num = max(max_num, int(item))
        except Exception:
            pass

        next_num = max_num + 1

        # 4자리로 포매팅, 만약 4자리를 넘으면 그대로 사용
        if next_num < 10000:
            return f"{next_num:04d}"
        else:
            return str(next_num)

    def _get_registered_projects_from_personal_dir(self):
        """개인 디렉토리에서 등록된 프로젝트 목록 조회 (마이그레이션용)"""
        projects = []

        if not os.path.exists(self.personal_config_dir):
            return projects

        try:
            for item in os.listdir(self.personal_config_dir):
                project_dir = os.path.join(self.personal_config_dir, item)
                if os.path.isdir(project_dir) and item.isdigit():
                    project_config_file = os.path.join(project_dir, "config.ini")
                    if os.path.exists(project_config_file):
                        config = configparser.ConfigParser()
                        config.read(project_config_file)

                        if (config.has_section('CONFIG') and config.has_option('CONFIG', 'WORKING_BASE_DIR') and
                            config.has_section('INFO') and config.has_option('INFO', 'PROJECT_NAME')):
                            work_dir = config.get('CONFIG', 'WORKING_BASE_DIR')
                            project_name = config.get('INFO', 'PROJECT_NAME')
                            tag = config.get('INFO', 'TAG', fallback='')
                            create_date = config.get('INFO', 'CREATE_DATE', fallback='')
                            projects.append((int(item), project_name, work_dir, tag, create_date))

            # 숫자 순서로 정렬
            projects.sort(key=lambda x: x[0])

        except Exception as e:
            display_message(f"프로젝트 목록 조회 중 오류: {e}", "WARN")

        return projects

    def get_file_state(self, production_file, work_file, rel_path):
        """3-way 비교를 통한 파일 상태 판단"""

        if not self.tag_manager.has_production_tag():
            return FileState.UPDATED  # 첫 다운로드

        try:
            # Enhanced Tag에서 commit hash만 추출
            production_commit, _ = self.tag_manager.get_production_tag_parts()
            if not production_commit:
                return FileState.UPDATED  # Tag가 없으면 업데이트로 간주

            # Git hash 기반 빠른 비교
            work_hash = GitHelper.get_current_file_hash(self.working_dir, rel_path)
            production_head_hash = GitHelper.get_current_file_hash(self.production_dir, rel_path)
            production_base_hash = GitHelper.get_file_hash_from_commit(
                self.production_dir, production_commit, rel_path  # commit hash만 사용
            )

            # 파일이 없는 경우 처리
            if work_hash is None and not os.path.exists(work_file):
                work_hash = "MISSING"
            if production_head_hash is None and not os.path.exists(production_file):
                production_head_hash = "MISSING"
            if production_base_hash is None:
                production_base_hash = "MISSING"

            # 특별 케이스: work 파일이 없는데 production 파일이 있는 경우
            if work_hash == "MISSING" and production_head_hash != "MISSING":
                return FileState.UPDATED

            # 특별 케이스: 새로 생성된 파일 (work에만 있고 production에는 없는 경우)
            if work_hash != "MISSING" and production_head_hash == "MISSING":
                return FileState.MODIFIED

            # 3-way 비교
            work_eq_base = (work_hash == production_base_hash)
            work_eq_head = (work_hash == production_head_hash)
            base_eq_head = (production_base_hash == production_head_hash)

            if work_eq_base and work_eq_head and base_eq_head:
                return FileState.SAME
            elif work_eq_base and not base_eq_head:
                return FileState.UPDATED
            elif not work_eq_base and base_eq_head:
                return FileState.MODIFIED
            elif work_eq_head and not work_eq_base:
                return FileState.SAME
            else:
                return FileState.CONFLICTED

        except:
            # 실패시 안전하게 충돌로 처리
            return FileState.CONFLICTED

    def get_source_patterns(self):
        """소스 패턴 가져오기"""
        patterns = []
        if self.config.has_section('SOURCES'):
            for key in self.config['SOURCES']:
                value = self.config.get('SOURCES', key)
                # 주석 제거 (';' 또는 '#' 이후 무시)
                if ';' in value:
                    value = value.split(';')[0]
                elif '#' in value:
                    value = value.split('#')[0]
                # 공백 및 탭 제거
                pattern = value.strip()
                if pattern:
                    patterns.append(pattern)
        return patterns

    def get_exclude_patterns(self):
        """제외 패턴 가져오기"""
        patterns = []
        if self.config.has_section('EXCLUDES'):
            for key in self.config['EXCLUDES']:
                value = self.config.get('EXCLUDES', key)
                # 주석 제거 (';' 또는 '#' 이후 무시)
                if ';' in value:
                    value = value.split(';')[0]
                elif '#' in value:
                    value = value.split('#')[0]
                # 공백 및 탭 제거
                pattern = value.strip()
                if pattern:
                    patterns.append(pattern)
        return patterns

    def _get_config_value_without_comment(self, section, key):
        """설정값에서 주석을 제거하고 반환"""
        if not self.config.has_option(section, key):
            return None

        value = self.config.get(section, key)
        # 주석 제거 (';' 또는 '#' 이후 무시)
        if ';' in value:
            value = value.split(';')[0]
        elif '#' in value:
            value = value.split('#')[0]
        # 공백 및 탭 제거
        return value.strip() if value.strip() else None

    def get_backup_count(self):
        """BACKUP_COUNT 설정값 가져오기"""
        try:
            backup_count_str = self._get_config_value_without_comment('UPLOAD', 'BACKUP_COUNT')
            if backup_count_str:
                return int(backup_count_str)
            return 0  # 기본값: 백업 안함
        except (ValueError, TypeError):
            return 0

    def _compute_sources_hash(self):
        """현재 프로젝트의 SOURCES 패턴들을 해시화

        다중 프로젝트 환경에서 각 프로젝트가 추적하는 파일 패턴을 식별하기 위해 사용.
        Production Tag에 포함되어 SOURCES 변경 감지에 활용됨.

        Returns:
            str: SOURCES 패턴의 CRC32 해시값 (8자리 16진수)
        """
        import zlib

        # SOURCES 패턴 가져오기
        source_patterns = self.get_source_patterns()

        if not source_patterns:
            return "00000000"  # SOURCES가 없으면 기본값

        # 정렬하여 순서 무관하게 동일한 해시 생성
        sorted_patterns = sorted(source_patterns)

        # 패턴들을 하나의 문자열로 결합
        patterns_str = '|'.join(sorted_patterns)

        # CRC32 해시 계산
        hash_value = zlib.crc32(patterns_str.encode('utf-8')) & 0xffffffff

        return f"{hash_value:08x}"

    def _sync_gitignore_from_production(self, production_perm):
        """Production에서 Work로 .gitignore 동기화"""
        production_gitignore = os.path.join(self.production_dir, '.gitignore')
        work_gitignore = os.path.join(self.working_dir, '.gitignore')

        # Production .gitignore 읽기 (읽기는 권한 불필요)
        production_content = None
        if os.path.exists(production_gitignore):
            with open(production_gitignore, 'r') as f:
                production_content = f.read()

        if production_content is None:
            display_message("Production .gitignore가 존재하지 않습니다.", "INFO")
            return False

        # Work .gitignore가 있고 내용이 다른 경우 백업
        gitignore_changed = False
        if os.path.exists(work_gitignore):
            with open(work_gitignore, 'r') as f:
                work_content = f.read()

            if work_content != production_content:
                gitignore_changed = True
                # 사용자 수정 경고 및 백업
                backup_path = '/tmp/cccopy.gitignore.backup'
                shutil.copy2(work_gitignore, backup_path)
                display_message("경고: Work .gitignore가 수정되었습니다!", "WARNING")
                display_message("  .gitignore는 Production에서만 변경할 수 있습니다.", "WARNING")
                display_message(f"  기존 Work .gitignore를 {backup_path}에 백업했습니다.", "WARNING")
                display_message("  Production .gitignore로 복원합니다.", "WARNING")
        else:
            gitignore_changed = True
            display_message("Work .gitignore를 Production에서 복사합니다.", "INFO")

        # Production에서 Work로 복사
        with open(work_gitignore, 'w') as f:
            f.write(production_content)

        return gitignore_changed

    def _refresh_git_cache_if_needed(self, directory, gitignore_changed):
        """.gitignore 변경시 Git cache 갱신"""
        if gitignore_changed:
            display_message(f"  .gitignore 변경으로 인한 Git cache 갱신 중...", "INFO")
            try:
                # Git에 추적 중인 파일이 있는지 확인
                tracked_files = GitHelper.run_git_command(['ls-files'], cwd=directory, capture_output=True)

                if tracked_files and tracked_files.strip():
                    # Git cache 갱신 (staged 파일이 있으면 경고가 나올 수 있음)
                    try:
                        GitHelper.run_git_command(['rm', '-r', '--cached', '.'], cwd=directory)
                    except Exception as e:
                        # staged 파일과의 충돌은 정상 동작이므로 DEBUG 레벨로 표시
                        error_msg = str(e)
                        if 'has staged content different' in error_msg:
                            display_message(f"  (Staged 파일 감지, 안전하게 계속 진행)", "DEBUG")
                        else:
                            # 다른 오류는 표시
                            display_message(f"  Git cache 갱신 중 경고: {e}", "WARN")

                    GitHelper.run_git_command(['add', '.'], cwd=directory)
                    display_message(f"  Git cache 갱신 완료", "INFO")
                else:
                    # 추적 중인 파일이 없으면 add만 실행
                    GitHelper.run_git_command(['add', '.'], cwd=directory)
                    display_message(f"  Git cache 갱신 완료 (초기 상태)", "INFO")
                return True
            except Exception as e:
                display_message(f"  Git cache 갱신 실패: {e}", "ERROR")
                return False
        return False

    def _detect_gitignore_changes_in_commit(self, directory, production_perm=None):
        """.gitignore가 포함된 변경사항인지 확인"""
        try:
            # git status로 .gitignore 변경 확인 (읽기 작업이므로 권한 불필요)
            result = GitHelper.run_git_command(['status', '--porcelain'], cwd=directory, capture_output=True)
            if result:
                for line in result.split('\n'):
                    if line.strip().endswith('.gitignore'):
                        return True
            return False
        except:
            return False

    def _parse_gitignore_patterns(self, gitignore_path):
        """.gitignore 파일에서 패턴 파싱"""
        patterns = []
        if not os.path.exists(gitignore_path):
            return patterns

        try:
            with open(gitignore_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    # 빈 줄이나 주석 제외
                    if line and not line.startswith('#'):
                        patterns.append(line)
            return patterns
        except:
            return patterns

    def _is_ignored_by_gitignore(self, rel_path, gitignore_patterns):
        """.gitignore 패턴에 의해 제외되는지 확인"""
        import fnmatch

        for pattern in gitignore_patterns:
            # 패턴이 '/'로 끝나면 디렉토리만 매치
            if pattern.endswith('/'):
                dir_pattern = pattern[:-1]
                # 디렉토리 경로 체크
                path_parts = rel_path.split('/')
                for i in range(len(path_parts)):
                    dir_path = '/'.join(path_parts[:i+1])
                    if fnmatch.fnmatch(dir_path, dir_pattern):
                        return True
            else:
                # 파일 및 디렉토리 모두 매치
                if fnmatch.fnmatch(rel_path, pattern):
                    return True
                # 중간 디렉토리 매치도 체크
                path_parts = rel_path.split('/')
                for i in range(len(path_parts)):
                    partial_path = '/'.join(path_parts[:i+1])
                    if fnmatch.fnmatch(partial_path, pattern):
                        return True
        return False

    def create_backup_file(self, production_file):
        """파일 백업 생성"""
        backup_count = self.get_backup_count()
        if backup_count <= 0:
            return  # 백업 안함

        if not os.path.exists(production_file):
            return  # 백업할 파일이 없음

        # 백업 디렉토리 경로 생성
        file_dir = os.path.dirname(production_file)
        backup_dir = os.path.join(file_dir, 'backup')
        os.makedirs(backup_dir, exist_ok=True)

        # 백업 파일명 생성
        filename = os.path.basename(production_file)
        timestamp = datetime.datetime.now().strftime('%y%m%d%H%M')

        # 기존 백업 파일들 찾기
        backup_pattern = f"{filename}_cccopy_*"
        backup_files = []

        import glob
        for backup_file in glob.glob(os.path.join(backup_dir, backup_pattern)):
            if os.path.isfile(backup_file):
                # 파일명에서 인덱스 추출
                basename = os.path.basename(backup_file)
                try:
                    # filename_cccopy_index_timestamp 형식에서 index 추출
                    parts = basename.split('_cccopy_')
                    if len(parts) == 2:
                        index_timestamp = parts[1]
                        index_str = index_timestamp.split('_')[0]
                        index = int(index_str)
                        backup_files.append((backup_file, index))
                except (ValueError, IndexError):
                    continue

        # 백업 파일들을 인덱스 순으로 정렬 (오래된 순)
        backup_files.sort(key=lambda x: x[1])

        # 백업 개수가 limit를 초과하면 오래된 것들 삭제
        while len(backup_files) >= backup_count:
            old_file, _ = backup_files.pop(0)
            try:
                os.remove(old_file)
                display_message(f"  오래된 백업 파일 삭제: {os.path.basename(old_file)}", "INFO")
            except OSError:
                pass

        # 새 백업 파일의 인덱스 결정
        if backup_files:
            max_index = max(backup_files, key=lambda x: x[1])[1]
            new_index = max_index + 1
        else:
            new_index = 0

        # 새 백업 파일 생성
        backup_filename = f"{filename}_cccopy_{new_index:06d}_{timestamp}"
        backup_path = os.path.join(backup_dir, backup_filename)

        try:
            shutil.copy2(production_file, backup_path)
            display_message(f"  백업 생성: backup/{backup_filename}", "INFO")
        except Exception as e:
            display_message(f"  백업 실패: {e}", "ERROR")

    def _create_backup_command(self, production_file):
        """백업 명령 생성 (sg에서 실행할 shell 명령)"""
        backup_count = self.get_backup_count()
        if backup_count <= 0:
            return None  # 백업 안함

        # 파일 존재 여부는 sg 명령 안에서 확인
        file_dir = os.path.dirname(production_file)
        backup_dir = os.path.join(file_dir, 'backup')
        filename = os.path.basename(production_file)
        timestamp = datetime.datetime.now().strftime('%y%m%d%H%M')

        # 백업 파일명 생성 (인덱스는 0으로 시작, 실제 sg 명령에서 증가)
        backup_filename = f"{filename}_cccopy_000000_{timestamp}"
        backup_path = os.path.join(backup_dir, backup_filename)

        # Shell 명령 생성
        production_file_escaped = shlex.quote(production_file)
        backup_dir_escaped = shlex.quote(backup_dir)
        backup_path_escaped = shlex.quote(backup_path)

        cmd = f"[ -f {production_file_escaped} ] && mkdir -p {backup_dir_escaped} && cp -p {production_file_escaped} {backup_path_escaped} || true"
        return cmd

    def collect_files(self, use_gitignore=False, include_work_only=False):
        """패턴에 매치되는 파일 수집 (선택적으로 .gitignore 적용)"""
        import glob
        import fnmatch

        source_patterns = self.get_source_patterns()
        exclude_patterns = self.get_exclude_patterns()

        # .gitignore 패턴도 가져오기 (요청시)
        gitignore_patterns = []
        if use_gitignore:
            work_gitignore = os.path.join(self.working_dir, '.gitignore')
            gitignore_patterns = self._parse_gitignore_patterns(work_gitignore)

        matched_files = set()

        # Production에서 소스 패턴으로 파일 수집
        for pattern in source_patterns:
            full_pattern = os.path.join(self.production_dir, pattern)
            for path in glob.glob(full_pattern, recursive=True):
                if os.path.isfile(path):
                    matched_files.add(path)

        # Work에서도 파일 수집 (Upload용)
        if include_work_only:
            for pattern in source_patterns:
                full_pattern = os.path.join(self.working_dir, pattern)
                for path in glob.glob(full_pattern, recursive=True):
                    if os.path.isfile(path):
                        # Work 파일을 Production 경로로 변환하여 추가
                        rel_path = os.path.relpath(path, self.working_dir)
                        production_path = os.path.join(self.production_dir, rel_path)
                        matched_files.add(production_path)

        # 제외 패턴 적용
        filtered_files = []
        for file_path in matched_files:
            rel_path = os.path.relpath(file_path, self.production_dir)

            exclude = False

            # config.ini EXCLUDES 패턴 체크
            for exclude_pattern in exclude_patterns:
                if fnmatch.fnmatch(rel_path, exclude_pattern) or fnmatch.fnmatch(file_path, exclude_pattern):
                    exclude = True
                    break

            # .gitignore 패턴 체크 (요청시)
            if not exclude and use_gitignore and gitignore_patterns:
                if self._is_ignored_by_gitignore(rel_path, gitignore_patterns):
                    exclude = True

            if not exclude:
                filtered_files.append((file_path, rel_path))
        return filtered_files

    def auto_commit_production_changes(self, force=False):
        """Production 디렉토리의 직접 수정 내용을 자동 커밋 (TUI Refresh용)

        Args:
            force: True면 캐시 무시하고 강제 체크 (Upload/Download/Save시 사용)
                   False면 캐시 타임아웃 체크 (Partial Refresh시 사용)
        """
        try:
            if not os.path.exists(self.production_dir):
                return False

            if not GitHelper.is_git_repo(self.production_dir):
                return False

            # 캐시 타임아웃 체크 (force=False이고 타임아웃 이내면 skip)
            if not force:
                current_time = time.time()
                elapsed = int(current_time - self.last_production_check_time)
                if elapsed < self.production_check_timeout:
                    remaining = int(self.production_check_timeout - elapsed)
                    display_message(f"Production 변경 사항 체크 skip ({remaining}초 이후 갱신 진행)", "DEBUG")
                    return False
                # 캐시 타임아웃 지남 - 체크 수행 후 시간 업데이트
                self.last_production_check_time = current_time

            # 그룹 권한 설정
            group_name = self._get_config_value_without_comment('UPLOAD', 'GROUP')
            production_perm = AtomicProductionPermission(group_name)

            # 락 획득 (Production 작업 통합 락)
            lock_file_path = os.path.join(self.production_dir, '.cccopy', 'lock', 'production_lock')

            try:
                with LockManager(lock_file_path, timeout=5, max_stale_time=3600, permission_manager=production_perm):
                    # Production에서 직접 수정된 내용이 있는지 확인하고 자동 커밋
                    display_message("Production의 변경 사항을 체크하고 있습니다...", "INFO")

                    # git status --short로 변경사항 확인 (중복 제거)
                    status_output = GitHelper.run_git_command(['status', '--short'], cwd=self.production_dir, capture_output=True)

                    if status_output and status_output.strip():
                        # 변경된 파일 목록 출력
                        display_message("Production에서 직접 수정된 파일:", "INFO")
                        for line in status_output.strip().split('\n'):
                            if line.strip():
                                formatted_line = GitHelper.format_git_status_line(line)
                                display_message(f"  {formatted_line}", "INFO")

                        display_message("Production 변경 사항 자동 커밋 중...", "INFO")

                        # Option B: SOURCES 필터링 적용한 선택적 auto-commit
                        display_message("  현재 프로젝트 SOURCES 패턴에 매칭되는 파일만 커밋합니다...", "INFO")

                        # 변경된 파일 중 SOURCES 패턴에 매칭되는 파일만 추출
                        changed_files_in_sources = []
                        source_patterns = self.get_source_patterns()
                        import fnmatch

                        for line in status_output.strip().split('\n'):
                            if line.strip():
                                # git status --short 형식: "XY filename"
                                rel_path = line[3:].strip()  # 상태 코드 제거

                                # SOURCES 패턴 매칭 확인
                                for pattern in source_patterns:
                                    # glob 패턴 매칭
                                    if fnmatch.fnmatch(rel_path, pattern):
                                        changed_files_in_sources.append(rel_path)
                                        break
                                    # 디렉토리 패턴 (AAA/**)
                                    elif pattern.endswith('**'):
                                        dir_prefix = pattern.rstrip('*').rstrip('/')
                                        if rel_path.startswith(dir_prefix + '/') or rel_path == dir_prefix:
                                            changed_files_in_sources.append(rel_path)
                                            break

                        if changed_files_in_sources:
                            display_message(f"  SOURCES에 속한 변경 파일 {len(changed_files_in_sources)}개를 커밋합니다", "INFO")
                            GitHelper.add_files(self.production_dir, changed_files_in_sources, production_perm=production_perm)
                            GitHelper.commit_all(self.production_dir, "Auto-commit: Direct changes in production", production_perm=production_perm)
                            display_message("Production 직접 수정 내용 자동 커밋 완료", "INFO")
                        else:
                            display_message("  SOURCES에 속한 변경 파일이 없어 auto-commit을 skip합니다", "INFO")
                        return True
                    else:
                        display_message("Production 변경 사항 없음", "DEBUG")
                        return False

            except CCCopyError as e:
                display_message(f"Production 자동 커밋 실패: {e}", "ERROR")
                return False
        except Exception as e:
            display_message(f"Production 자동 커밋 오류: {e}", "ERROR")
            return False

    def download(self):
        """다운로드 (production -> work)"""
        display_message("=== DOWNLOAD (production -> work) ===", "INFO")

        # Git 버전 정보 출력
        try:
            git_version = GitHelper.run_git_command(['--version'], capture_output=True)
            display_message(f"Git 버전: {git_version}", "INFO")
        except Exception as e:
            display_message(f"Git 버전 확인 실패: {e}", "ERROR")

        display_message("Production 디렉토리 확인 중...", "INFO")
        if not os.path.exists(self.production_dir):
            display_message(f"오류: Production 디렉토리가 존재하지 않습니다: {self.production_dir}", "ERROR")
            return

        display_message(f"연결 완료: {self.production_dir}", "INFO")

        # 원자적 권한 관리자 생성 (락 획득 전에 필요)
        group_name = self._get_config_value_without_comment('UPLOAD', 'GROUP')
        production_perm = AtomicProductionPermission(group_name)

        # 락 획득 (Production 작업 통합 락)
        lock_file_path = os.path.join(self.production_dir, '.cccopy', 'lock', 'production_lock')

        display_message("Production 작업 락 획득 중...", "INFO")
        try:
            with LockManager(lock_file_path, timeout=5, max_stale_time=3600, permission_manager=production_perm):
                display_message("락 획득 완료", "INFO")

                # Option B: SOURCES 패턴 변경 검증
                saved_commit, saved_sources_hash = self.tag_manager.get_production_tag_parts()
                if saved_sources_hash is not None:
                    # Enhanced Tag 형식 (SOURCES hash 포함)
                    current_sources_hash = self._compute_sources_hash()
                    if saved_sources_hash != current_sources_hash:
                        display_message("=" * 60, "WARNING")
                        display_message("[경고] SOURCES 패턴이 변경되었습니다!", "WARNING")
                        display_message(f"  이전 hash: {saved_sources_hash}", "WARNING")
                        display_message(f"  현재 hash: {current_sources_hash}", "WARNING")
                        display_message("  다운로드되는 파일 목록이 달라질 수 있습니다.", "WARNING")
                        display_message("=" * 60, "WARNING")

                        # 사용자 확인
                        from ..utils.ui_handler import messagebox
                        result = messagebox(
                            "SOURCES 패턴이 변경되었습니다.\n계속 진행하시겠습니까?",
                            "SOURCES 변경 감지",
                            "warn",
                            "yesno"
                        )
                        if result != "yes":
                            display_message("사용자가 DOWNLOAD를 취소했습니다.", "INFO")
                            return

                # Production Git 초기화 (필요시)
                if not GitHelper.is_git_repo(self.production_dir):
                    display_message("Production Git 저장소 초기화 중...", "INFO")
                    GitHelper.init_repo(self.production_dir, production_perm=production_perm)
                    GitHelper.setup_user_config(self.production_dir, production_perm=production_perm, use_dummy=True)

                    # .gitignore 추가 (EXCLUDES 패턴 포함)
                    gitignore_path = os.path.join(self.production_dir, '.gitignore')
                    gitignore_content = "# cccopy internal directory\n.cccopy/\n\n"

                    # config.ini의 EXCLUDES 패턴을 .gitignore에 추가
                    exclude_patterns = self.get_exclude_patterns()
                    if exclude_patterns:
                        gitignore_content += "# Exclude patterns from config.ini [EXCLUDES]\n"
                        for pattern in exclude_patterns:
                            gitignore_content += f"{pattern}\n"

                    # Python cat을 사용한 .gitignore 생성 (sg를 통해)
                    gitignore_escaped = shlex.quote(gitignore_path)
                    content_escaped = shlex.quote(gitignore_content)
                    cmd = f"cat > {gitignore_escaped} << 'CCCOPY_EOF'\n{gitignore_content}CCCOPY_EOF"
                    production_perm.execute_sg_command(cmd, timeout=10, operation_desc="Production .gitignore 파일 생성")

                    # Option B: SOURCES 패턴에 매칭되는 파일만 선택적으로 git add
                    display_message("SOURCES 패턴에 매칭되는 파일만 Git에 추가 중...", "INFO")
                    filtered_files = self.collect_files(use_gitignore=True, include_work_only=False)

                    # 상대 경로 리스트 추출
                    rel_paths = [rel_path for _, rel_path in filtered_files]
                    display_message(f"  추가할 파일 개수: {len(rel_paths)}", "INFO")

                    # 선택적 git add (SOURCES 필터링 적용)
                    GitHelper.add_files(self.production_dir, rel_paths, production_perm=production_perm)
                    GitHelper.commit_all(self.production_dir, "Initial production repository", production_perm=production_perm)

                    # Initial commit 완료 - 이후 auto-commit skip (SOURCES 외 파일은 의도적으로 untracked 유지)
                    display_message("Initial commit 완료 - SOURCES 외 파일은 Git에서 제외됨", "INFO")
                else:
                    # Production에서 직접 수정된 내용이 있는지 확인하고 자동 커밋
                    display_message("Production의 변경 사항을 체크하고 있습니다...", "INFO")

                    # git status --short로 변경사항 확인 (중복 제거)
                    status_output = GitHelper.run_git_command(['status', '--short'], cwd=self.production_dir, capture_output=True)

                    if status_output and status_output.strip():
                        # 변경된 파일 목록 출력
                        display_message("Production에서 직접 수정된 파일:", "INFO")
                        for line in status_output.strip().split('\n'):
                            if line.strip():
                                formatted_line = GitHelper.format_git_status_line(line)
                                display_message(f"  {formatted_line}", "INFO")

                        display_message("Production 변경 사항 자동 커밋 중...", "INFO")

                        # Option B: SOURCES 필터링 적용한 선택적 auto-commit
                        # Production에서 직접 수정되었지만, 현재 프로젝트의 SOURCES에 속하는 파일만 커밋
                        display_message("  현재 프로젝트 SOURCES 패턴에 매칭되는 파일만 커밋합니다...", "INFO")

                        # 변경된 파일 중 SOURCES 패턴에 매칭되는 파일만 추출
                        changed_files_in_sources = []
                        source_patterns = self.get_source_patterns()
                        import fnmatch

                        for line in status_output.strip().split('\n'):
                            if line.strip():
                                # git status --short 형식: "XY filename"
                                rel_path = line[3:].strip()  # 상태 코드 제거

                                # SOURCES 패턴 매칭 확인
                                for pattern in source_patterns:
                                    # glob 패턴 매칭
                                    if fnmatch.fnmatch(rel_path, pattern):
                                        changed_files_in_sources.append(rel_path)
                                        break
                                    # 디렉토리 패턴 (AAA/**)
                                    elif pattern.endswith('**'):
                                        dir_prefix = pattern.rstrip('*').rstrip('/')
                                        if rel_path.startswith(dir_prefix + '/') or rel_path == dir_prefix:
                                            changed_files_in_sources.append(rel_path)
                                            break

                        if changed_files_in_sources:
                            display_message(f"  SOURCES에 속한 변경 파일 {len(changed_files_in_sources)}개를 커밋합니다", "INFO")
                            for f in changed_files_in_sources[:5]:  # 최대 5개만 표시
                                display_message(f"    - {f}", "DEBUG")
                            GitHelper.add_files(self.production_dir, changed_files_in_sources, production_perm=production_perm)
                            GitHelper.commit_all(self.production_dir, "Auto-commit: Direct changes in production", production_perm=production_perm)
                            display_message("Production 직접 수정 내용 자동 커밋 완료", "INFO")
                        else:
                            display_message("  SOURCES에 속한 변경 파일이 없어 auto-commit을 skip합니다", "INFO")
                            display_message("  (SOURCES 외 파일은 의도적으로 untracked 상태 유지)", "INFO")
                    else:
                        display_message("Production 변경 사항 없음", "DEBUG")

                # Work Git 초기화 (필요시)
                is_first_init = not GitHelper.is_git_repo(self.working_dir)
                if is_first_init:
                    display_message("Work Git 저장소 초기화 중...", "INFO")
                    GitHelper.init_repo(self.working_dir)
                    GitHelper.setup_user_config(self.working_dir)

                # Work .gitignore를 Production에서 복사 (항상 수행)
                gitignore_changed = self._sync_gitignore_from_production(production_perm)

                # .gitignore 변경시 Work Git cache 갱신
                if gitignore_changed:
                    self._refresh_git_cache_if_needed(self.working_dir, gitignore_changed)

                # 파일 수집 및 상태 확인 (.gitignore 패턴 적용)
                files = self.collect_files(use_gitignore=True)
                display_message(f"파일 검색 완료: {len(files)}개 (.gitignore 적용됨)", "INFO")

                if not files:
                    display_message("수집된 파일이 없습니다.", "WARNING")
                    display_message("설정 파일의 SOURCES 패턴을 확인하세요.", "INFO")
                    return

                # 파일별 상태 확인 및 처리
                modified_count = 0
                updated_count = 0
                same_count = 0
                unresolved_conflicts = False  # 미해결 충돌 추적
                newly_added_files = []  # 새로 추가된 파일 추적

                for production_file, rel_path in files:
                    work_file = os.path.join(self.working_dir, rel_path)
                    state = self.get_file_state(production_file, work_file, rel_path)

                    if state == FileState.UPDATED:
                        # Production -> Work 복사
                        os.makedirs(os.path.dirname(work_file), exist_ok=True)

                        # 새로 추가된 파일인지 확인 (Work에 없던 파일)
                        was_new_file = not os.path.exists(work_file)

                        shutil.copy2(production_file, work_file)
                        display_message(f"업데이트: {rel_path}", "INFO")
                        updated_count += 1

                        # 새로 추가된 파일이면 목록에 추가
                        if was_new_file:
                            newly_added_files.append(rel_path)

                    elif state == FileState.SAME:
                        same_count += 1
                    elif state == FileState.MODIFIED:
                        modified_count += 1
                    elif state == FileState.CONFLICTED:
                        # 충돌 처리 - 사용자 메뉴 호출
                        resolved = handle_conflict(production_file, work_file, rel_path, production_perm)
                        if resolved:
                            # 해결되면 Git에 변경사항 반영
                            update_work_git_after_merge(work_file, rel_path)
                            updated_count += 1
                        else:
                            # 건너뛰기한 경우 충돌 상태 유지
                            unresolved_conflicts = True
                            display_message(f"[INFO] {rel_path} 충돌 상태 유지됨 - 다음 DOWNLOAD에서 다시 처리 가능", "INFO")

                # Production tag 저장 (미해결 충돌이 없는 경우에만)
                if not unresolved_conflicts:
                    self.tag_manager.save_production_tag(self.production_dir, include_sources_hash=True)
                else:
                    display_message("[INFO] 미해결 충돌로 인해 Production tag 업데이트하지 않음", "INFO")

                # Work Git 선별적 자동 커밋
                try:
                    if is_first_init and updated_count > 0:
                        # 첫 다운로드시 자동 커밋
                        display_message("첫 다운로드 완료 - Work Git에 자동 커밋 중...", "INFO")
                        GitHelper.add_all(self.working_dir)
                        GitHelper.commit_all(self.working_dir, "Initial download from production")
                        display_message("초기 커밋 완료", "INFO")
                    elif not is_first_init and newly_added_files:
                        # 새로 추가된 파일만 선별적으로 커밋
                        display_message(f"새로 추가된 {len(newly_added_files)}개 파일을 선별적으로 커밋 중...", "INFO")
                        for file_path in newly_added_files:
                            display_message(f"  추가: {file_path}", "INFO")

                        GitHelper.add_and_commit_files(
                            self.working_dir,
                            newly_added_files,
                            f"Auto-commit: Added {len(newly_added_files)} new files from production"
                        )
                        display_message("새 파일 자동 커밋 완료", "INFO")
                        display_message("사용자 작업 중인 파일은 보호되어 커밋되지 않았습니다.", "INFO")
                    elif not is_first_init and updated_count > 0 and not newly_added_files:
                        # 기존 파일 업데이트만 있는 경우 (새 파일 없음)
                        if GitHelper.has_uncommitted_changes(self.working_dir):
                            display_message(f"Production에서 {updated_count}개 파일 다운로드 완료 (다른 파일 작업 중이므로 자동 커밋 생략)", "INFO")
                        else:
                            # 사용자 변경사항이 없는 경우에만 자동 커밋
                            display_message("업데이트된 변경사항을 Work Git에 자동 커밋 중...", "INFO")
                            GitHelper.add_all(self.working_dir)
                            GitHelper.commit_all(self.working_dir, f"Auto-commit: Downloaded {updated_count} updated files")
                            display_message("자동 커밋 완료", "INFO")
                    elif gitignore_changed:
                        display_message("[INFO] .gitignore 변경으로 인한 Git cache 갱신 완료", "INFO")
                        display_message("       새로 추적 가능한 파일들이 staged 상태입니다.", "INFO")
                        display_message("       필요시 'SAVE' 명령으로 수동 커밋하세요.", "INFO")
                except Exception as e:
                    display_message(f"자동 커밋 실패: {e}", "ERROR")

                display_message(f"다운로드 완료:", "INFO")
                display_message(f"  업데이트: {updated_count}개", "INFO")
                display_message(f"  수정중: {modified_count}개", "INFO")
                display_message(f"  동일: {same_count}개", "INFO")

        except CCCopyError as e:
            display_message(f"오류: {e}", "ERROR")

    def upload(self):
        """업로드 (work -> production)"""
        display_message("=== UPLOAD (work -> production) ===", "INFO")

        # Work Git 확인
        display_message("Work Git 저장소 확인 중...", "INFO")
        if not GitHelper.is_git_repo(self.working_dir):
            display_message("오류: Work Git 저장소가 없습니다. 먼저 DOWNLOAD를 실행하세요.", "ERROR")
            return

        display_message("Git 저장소 확인 완료", "INFO")

        # Production Git 확인 및 직접 수정 내용 자동 커밋
        display_message("Production 디렉토리 확인 중...", "INFO")
        if not os.path.exists(self.production_dir):
            display_message(f"오류: Production 디렉토리가 존재하지 않습니다: {self.production_dir}", "ERROR")
            return

        # 그룹 권한 설정 (락 획득 전에 필요)
        group_name = self._get_config_value_without_comment('UPLOAD', 'GROUP')
        production_perm = AtomicProductionPermission(group_name)

        # 락 획득 (Production 작업 통합 락)
        lock_file_path = os.path.join(self.production_dir, '.cccopy', 'lock', 'production_lock')

        display_message("Production 작업 락 획득 중...", "INFO")
        try:
            with LockManager(lock_file_path, timeout=5, max_stale_time=3600, permission_manager=production_perm):
                display_message("락 획득 완료", "INFO")

                # Production에서 직접 수정된 내용이 있는지 확인하고 자동 커밋
                if GitHelper.is_git_repo(self.production_dir):
                    display_message("Production의 변경 사항을 체크하고 있습니다...", "INFO")

                    # git status --short로 변경사항 확인 (중복 제거)
                    status_output = GitHelper.run_git_command(['status', '--short'], cwd=self.production_dir, capture_output=True)

                    if status_output and status_output.strip():
                        # 변경된 파일 목록 출력
                        display_message("Production에서 직접 수정된 파일:", "INFO")
                        for line in status_output.strip().split('\n'):
                            if line.strip():
                                formatted_line = GitHelper.format_git_status_line(line)
                                display_message(f"  {formatted_line}", "INFO")

                        display_message("Production 변경 사항 자동 커밋 중...", "INFO")

                        # Option B: SOURCES 필터링 적용한 선택적 auto-commit
                        display_message("  현재 프로젝트 SOURCES 패턴에 매칭되는 파일만 커밋합니다...", "INFO")

                        # 변경된 파일 중 SOURCES 패턴에 매칭되는 파일만 추출
                        changed_files_in_sources = []
                        source_patterns = self.get_source_patterns()
                        import fnmatch

                        for line in status_output.strip().split('\n'):
                            if line.strip():
                                # git status --short 형식: "XY filename"
                                rel_path = line[3:].strip()  # 상태 코드 제거

                                # SOURCES 패턴 매칭 확인
                                for pattern in source_patterns:
                                    # glob 패턴 매칭
                                    if fnmatch.fnmatch(rel_path, pattern):
                                        changed_files_in_sources.append(rel_path)
                                        break
                                    # 디렉토리 패턴 (AAA/**)
                                    elif pattern.endswith('**'):
                                        dir_prefix = pattern.rstrip('*').rstrip('/')
                                        if rel_path.startswith(dir_prefix + '/') or rel_path == dir_prefix:
                                            changed_files_in_sources.append(rel_path)
                                            break

                        if changed_files_in_sources:
                            display_message(f"  SOURCES에 속한 변경 파일 {len(changed_files_in_sources)}개를 커밋합니다", "INFO")
                            GitHelper.add_files(self.production_dir, changed_files_in_sources, production_perm=production_perm)
                            GitHelper.commit_all(self.production_dir, "Auto-commit: Direct changes in production", production_perm=production_perm)
                            display_message("Production 직접 수정 내용 자동 커밋 완료", "INFO")
                        else:
                            display_message("  SOURCES에 속한 변경 파일이 없어 auto-commit을 skip합니다", "INFO")
                    else:
                        display_message("Production 변경 사항 없음", "DEBUG")

                # 업로드 가능한 파일 검색 (.gitignore 패턴 적용, Work 전용 파일도 포함)
                display_message("업로드 가능한 파일 검색 중...", "INFO")
                files = self.collect_files(use_gitignore=True, include_work_only=True)

                modified_files = []
                conflicted_files = []

                for production_file, rel_path in files:
                    work_file = os.path.join(self.working_dir, rel_path)

                    # .gitignore는 업로드 대상에서 제외
                    if rel_path == '.gitignore':
                        continue

                    if os.path.exists(work_file):
                        state = self.get_file_state(production_file, work_file, rel_path)
                        if state == FileState.MODIFIED:
                            modified_files.append((production_file, work_file, rel_path))
                        elif state == FileState.CONFLICTED:
                            conflicted_files.append(rel_path)

                if conflicted_files:
                    display_message("충돌된 파일이 감지되었습니다:", "WARNING")
                    for file_path in conflicted_files:
                        display_message(f"  [충돌] {file_path}", "WARNING")
                    display_message("먼저 DOWNLOAD로 충돌을 해결한 후 업로드하세요.", "WARNING")
                    return

                if not modified_files:
                    display_message("업로드할 수 있는 modified 파일이 없습니다.", "INFO")
                    display_message("  - 업로드 가능한 파일은 사용자가 수정한 파일만입니다", "INFO")
                    return

                display_message(f"{len(modified_files)}개 업로드 가능 파일 발견", "INFO")
                display_message("업로드 대상 파일들:", "INFO")
                for _, _, rel_path in modified_files[:5]:
                    display_message(f"  - [M] {rel_path}", "INFO")
                if len(modified_files) > 5:
                    display_message(f"  ... 외 {len(modified_files)-5}개 더", "INFO")

                # 업로드 확인
                result = messagebox("이 파일들을 프로덕션에 업로드하시겠습니까?", "업로드 확인", "info", "yesno", "no")
                if result != "yes":
                    display_message("업로드를 취소했습니다.", "INFO")
                    return

                # 커밋 메시지 입력
                comment = messagebox("업로드 코멘트를 입력하세요:", "코멘트 입력", "info", "input", "Upload from work directory")
                if not comment:
                    comment = "파일 업로드"

                # 파일 업로드 (sg를 통한 실행)
                display_message("파일 업로드 중...", "INFO")
                uploaded_count = 0
                for production_file, work_file, rel_path in modified_files:
                    # 백업 생성 (sg 사용)
                    production_file_escaped = shlex.quote(production_file)
                    backup_cmd = self._create_backup_command(production_file)
                    if backup_cmd:
                        production_perm.execute_sg_command(backup_cmd, timeout=10, check=False, operation_desc=f"파일 백업 ({os.path.basename(production_file)})")

                    # 디렉토리 생성 및 파일 복사 (sg 사용)
                    work_file_escaped = shlex.quote(work_file)
                    dir_name = shlex.quote(os.path.dirname(production_file))
                    cmd = f"mkdir -p {dir_name} && cp -p {work_file_escaped} {production_file_escaped}"
                    production_perm.execute_sg_command(cmd, timeout=30, operation_desc=f"Production 파일 복사 ({rel_path})")

                    display_message(f"  업로드: {rel_path}", "INFO")
                    uploaded_count += 1

                display_message(f"{uploaded_count}개 파일 업로드 완료", "INFO")

                # Option B: Production에서 선택적 커밋 (SOURCES 필터링 적용)
                # 업로드된 파일들의 상대 경로만 git add
                uploaded_rel_paths = [rel_path for _, _, rel_path in modified_files]
                display_message(f"Git에 추가할 파일 개수: {len(uploaded_rel_paths)}", "INFO")

                GitHelper.add_files(self.production_dir, uploaded_rel_paths, production_perm=production_perm)
                GitHelper.commit_all(self.production_dir, comment, production_perm=production_perm)
                display_message(f"업로드 커밋 완료: {comment}", "INFO")

                # Production tag 업데이트 (Enhanced Tag with SOURCES hash)
                self.tag_manager.save_production_tag(self.production_dir, include_sources_hash=True)

                display_message("업로드가 성공적으로 완료되었습니다.", "INFO")

        except CCCopyError as e:
            display_message(f"오류: {e}", "ERROR")

    def save(self):
        """Work 저장소에 변경사항 커밋"""
        display_message("=== SAVE (Work 저장소 커밋) ===", "INFO")

        # Work Git 저장소 확인
        display_message("Work Git 저장소 확인 중...", "INFO")
        if not GitHelper.is_git_repo(self.working_dir):
            display_message("오류: Work Git 저장소가 없습니다. 먼저 DOWNLOAD를 실행하세요.", "ERROR")
            return

        display_message("Git 저장소 확인 완료", "INFO")

        # 변경사항 확인
        display_message("변경사항 확인 중...", "INFO")
        if not GitHelper.has_uncommitted_changes(self.working_dir):
            display_message("커밋할 변경사항이 없습니다.", "INFO")
            return

        # Git 상태 표시
        try:
            display_message("현재 Git 상태:", "INFO")
            status_output = GitHelper.run_git_command(['status', '--short'], cwd=self.working_dir, capture_output=True)
            if status_output:
                for line in status_output.strip().split('\n'):
                    if line.strip():
                        display_message(f"  {line}", "INFO")
        except Exception as e:
            display_message(f"Git 상태 확인 실패: {e}", "ERROR")

        # SOURCES 패턴에 매칭되는 변경 파일만 필터링
        display_message("SOURCES 패턴에 매칭되는 파일 확인 중...", "INFO")
        try:
            status_output = GitHelper.run_git_command(['status', '--short'], cwd=self.working_dir, capture_output=True)

            changed_files_in_sources = []
            changed_files_outside_sources = []
            source_patterns = self.get_source_patterns()
            import fnmatch

            for line in status_output.strip().split('\n'):
                if line.strip():
                    # git status --short 형식: "XY filename" (XY는 2글자 상태 코드)
                    rel_path = line[2:].strip()

                    # SOURCES 패턴 매칭 확인
                    match = False
                    for pattern in source_patterns:
                        # glob 패턴 매칭
                        if fnmatch.fnmatch(rel_path, pattern):
                            match = True
                            break
                        # 디렉토리 패턴 (AAA/**)
                        elif pattern.endswith('**'):
                            dir_prefix = pattern.rstrip('*').rstrip('/')
                            if rel_path.startswith(dir_prefix + '/') or rel_path == dir_prefix:
                                match = True
                                break
                        # **/ 패턴 처리
                        elif pattern.startswith('**/'):
                            tail = pattern[3:]
                            if fnmatch.fnmatch(rel_path, '*/' + tail) or fnmatch.fnmatch(rel_path, tail):
                                match = True
                                break

                    if match:
                        changed_files_in_sources.append(rel_path)
                    else:
                        changed_files_outside_sources.append(rel_path)

            # SOURCES 외부 파일이 있으면 경고
            if changed_files_outside_sources:
                display_message("=" * 60, "WARNING")
                display_message("[경고] SOURCES 패턴 외부에 변경된 파일이 있습니다:", "WARNING")
                for file_path in changed_files_outside_sources[:5]:
                    display_message(f"  - {file_path}", "WARNING")
                if len(changed_files_outside_sources) > 5:
                    display_message(f"  ... 외 {len(changed_files_outside_sources)-5}개 더", "WARNING")
                display_message("이 파일들은 SAVE에서 제외됩니다.", "WARNING")
                display_message("=" * 60, "WARNING")

            # SOURCES 내부 파일이 없으면 종료
            if not changed_files_in_sources:
                display_message("커밋할 변경사항이 없습니다 (SOURCES 패턴 내에서).", "INFO")
                return

            display_message(f"SOURCES 패턴에 매칭되는 변경 파일: {len(changed_files_in_sources)}개", "INFO")
            for file_path in changed_files_in_sources[:5]:
                display_message(f"  - {file_path}", "INFO")
            if len(changed_files_in_sources) > 5:
                display_message(f"  ... 외 {len(changed_files_in_sources)-5}개 더", "INFO")

        except Exception as e:
            display_message(f"파일 필터링 실패: {e}", "ERROR")
            return

        # 커밋 메시지 입력
        commit_message = messagebox("커밋 메시지를 입력하세요:", "커밋 메시지", "info", "input", "Work changes")
        if commit_message is None:
            display_message("SAVE가 취소되었습니다.", "INFO")
            return
        if not commit_message.strip():
            commit_message = "Work changes"

        # Work 저장소에 커밋 (SOURCES 패턴 파일만)
        try:
            display_message("변경사항을 Work Git에 커밋 중...", "INFO")
            # SOURCES 패턴에 매칭되는 파일만 add
            GitHelper.add_files(self.working_dir, changed_files_in_sources)
            GitHelper.commit_all(self.working_dir, commit_message)
            display_message(f"커밋 완료: {commit_message}", "INFO")
            display_message(f"  커밋된 파일: {len(changed_files_in_sources)}개", "INFO")
            display_message("SAVE가 성공적으로 완료되었습니다.", "INFO")
        except Exception as e:
            display_message(f"커밋 실패: {e}", "ERROR")

    def production_history(self):
        """Production 저장소 히스토리 조회"""
        display_message("Production 디렉토리 확인 중...", "INFO")
        if not os.path.exists(self.production_dir):
            display_message(f"오류: Production 디렉토리가 존재하지 않습니다: {self.production_dir}", "ERROR")
            return

        # 원자적 권한 관리자 생성 (락 획득 전에 필요)
        group_name = self._get_config_value_without_comment('UPLOAD', 'GROUP')
        production_perm = AtomicProductionPermission(group_name)

        # 락 획득 (Production 작업 통합 락)
        lock_file_path = os.path.join(self.production_dir, '.cccopy', 'lock', 'production_lock')

        display_message("Production 작업 락 획득 중...", "INFO")
        try:
            with LockManager(lock_file_path, timeout=5, max_stale_time=3600, permission_manager=production_perm):
                display_message("락 획득 완료", "INFO")

                # Production 히스토리 표시 (권한 상승)
                show_git_history(self.production_dir, "PRODUCTION HISTORY", production_perm)

        except CCCopyError as e:
            display_message(f"오류: {e}", "ERROR")

    def work_history(self):
        """Work 저장소 히스토리 조회"""
        display_message("Work Git 저장소 확인 중...", "INFO")
        if not GitHelper.is_git_repo(self.working_dir):
            display_message("오류: Work Git 저장소가 없습니다. 먼저 DOWNLOAD를 실행하세요.", "ERROR")
            return

        display_message("Git 저장소 확인 완료", "INFO")

        # Work 히스토리 표시 (권한 상승 없음)
        show_git_history(self.working_dir, "WORK HISTORY")


