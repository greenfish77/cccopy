"""
파일 처리 유틸리티 모듈
충돌 처리, Git 히스토리, diff 표시 등 파일 관련 기능
"""

import os
import shutil
import subprocess
import tempfile

from ..core.git_helper import GitHelper
from .helpers import find_vscode_command, safe_input
from .ui_handler import display_message, messagebox


def handle_conflict(production_file, work_file, rel_path, production_perm=None):
    """충돌 파일 처리 - 사용자 선택 메뉴"""
    display_message(f"충돌이 감지되었습니다: {rel_path}", "WARNING")
    display_message(f"Production: {production_file}", "INFO")
    display_message(f"Work: {work_file}", "INFO")

    # 충돌 해결 메뉴를 messagebox에 포함
    conflict_message = f"""충돌이 감지되었습니다: {rel_path}

Production: {production_file}
Work: {work_file}

충돌 해결 방법을 선택하세요:

1. VS Code diff로 수동 병합
2. Production 사용 (Work 내용 포기)
3. Work 사용 (Production으로 업로드. 1번 이후 실행)
4. 건너뛰기 (나중에 처리)

선택하세요 (1-4):"""

    choice = messagebox(conflict_message, "충돌 해결", "warn", "input", "")

    if choice == '1':
        # VS Code diff 실행 - 편집만 하고 다시 메뉴로 돌아감
        try:
            display_message(f"\nVS Code diff를 실행합니다...", "INFO")
            display_message("편집 완료 후 VS Code를 닫으면 다시 충돌 해결 메뉴로 돌아갑니다.", "INFO")

            # Production 파일을 읽기 전용 임시 파일로 복사
            with tempfile.NamedTemporaryFile(mode='w', delete=False,
                                           prefix=f'production_{os.path.basename(rel_path)}_',
                                           suffix='.readonly') as temp_file:
                with open(production_file, 'r') as prod_f:
                    temp_file.write(prod_f.read())
                temp_production_file = temp_file.name

            try:
                # 임시 파일을 읽기 전용으로 설정
                os.chmod(temp_production_file, 0o444)

                display_message("좌측: Production (읽기 전용), 우측: Work (편집 가능)", "INFO")
                # 통합된 VS Code diff 함수 사용
                if run_vscode_diff(temp_production_file, work_file, f"충돌 파일 {rel_path}"):
                    display_message("VS Code 편집이 완료되었습니다.", "INFO")
                    display_message("다시 충돌 해결 방법을 선택하세요.", "INFO")
                    # VS Code 종료 후 다시 메뉴로 돌아가기 위해 재귀 호출
                    return handle_conflict(production_file, work_file, rel_path, production_perm)
                else:
                    # VS Code 실행 실패시 gvimdiff로 fallback
                    display_message("gvimdiff를 사용합니다...", "INFO")
                    # TUI 모드에서는 출력 캡처하여 화면 깨짐 방지
                    from .ui_handler import _ui_handler
                    if _ui_handler and hasattr(_ui_handler, 'messagebox'):
                        subprocess.run(['gvimdiff', production_file, work_file], check=True, capture_output=True)
                    else:
                        subprocess.run(['gvimdiff', production_file, work_file], check=True)
                    display_message("편집이 완료되었습니다.", "INFO")
                    display_message("다시 충돌 해결 방법을 선택하세요.", "INFO")
                    # gvimdiff 종료 후 다시 메뉴로 돌아가기 위해 재귀 호출
                    return handle_conflict(production_file, work_file, rel_path, production_perm)
            finally:
                # 임시 파일 정리
                try:
                    os.unlink(temp_production_file)
                except OSError:
                    pass

        except (FileNotFoundError, subprocess.CalledProcessError):
            display_message("diff 도구를 실행할 수 없습니다.", "ERROR")
            display_message("2. Production 버전 사용하시겠습니까? (y/N): ", "INFO")
            fallback = safe_input().lower()
            if fallback in ('y', 'yes'):
                shutil.copy2(production_file, work_file)
                display_message("Production 버전으로 Work 파일을 업데이트했습니다.", "INFO")
                return True
            else:
                display_message("Work 파일을 그대로 유지합니다.", "INFO")
                return False

    elif choice == '2':
        # Production 버전 사용
        shutil.copy2(production_file, work_file)
        display_message("Production 버전으로 Work 파일을 업데이트했습니다.", "INFO")
        return True

    elif choice == '3':
        # Work 버전 사용 - Production으로 업로드
        display_message("Work 버전을 Production에 업로드합니다...", "INFO")

        if not production_perm:
            display_message("권한 관리자가 없어 업로드할 수 없습니다.", "ERROR")
            return False

        try:
            import shlex

            # Production Git 디렉토리 찾기
            production_dir = os.path.dirname(production_file)
            while production_dir and not os.path.exists(os.path.join(production_dir, '.git')):
                production_dir = os.path.dirname(production_dir)

            if not production_dir or not os.path.exists(os.path.join(production_dir, '.git')):
                display_message("Production Git 저장소를 찾을 수 없습니다.", "ERROR")
                return False

            # 1. sg를 통한 파일 복사
            work_escaped = shlex.quote(work_file)
            prod_escaped = shlex.quote(production_file)
            cmd = f"cp -p {work_escaped} {prod_escaped}"
            production_perm.execute_sg_command(cmd, timeout=30, operation_desc="충돌 해결: Work → Production 복사")

            # 2. Git 명령 (production_perm 전달)
            GitHelper.run_git_command(['add', rel_path], cwd=production_dir, production_perm=production_perm)
            GitHelper.commit_all(production_dir, 'Resolve conflict: Use work version', production_perm=production_perm)

            display_message("Work 버전이 Production에 업로드되어 충돌이 해결되었습니다.", "INFO")
            return True

        except Exception as e:
            display_message(f"업로드 실패: {e}", "ERROR")
            return False

    elif choice == '4':
        # 건너뛰기
        display_message("건너뛰기 - 나중에 처리하세요.", "INFO")
        return False

    else:
        display_message("잘못된 선택입니다. 건너뛰기로 처리합니다.", "ERROR")
        return False


def update_work_git_after_merge(work_file, rel_path):
    """수동 병합 완료 후 Work Git에 변경사항 반영"""
    work_dir = os.path.dirname(work_file)
    while work_dir and not os.path.exists(os.path.join(work_dir, '.git')):
        work_dir = os.path.dirname(work_dir)

    if work_dir and os.path.exists(os.path.join(work_dir, '.git')):
        try:
            GitHelper.run_git_command(['add', rel_path], cwd=work_dir)
            display_message(f"Git에 변경사항 반영: {rel_path}", "INFO")
        except:
            display_message(f"Git 반영 실패: {rel_path}", "ERROR")


def print_commit_table(commits, start_index=0):
    """커밋 정보를 표 형식으로 출력"""
    if not commits:
        print("커밋 히스토리가 없습니다.")
        return

    # 표 헤더
    print()
    print("=" * 80)
    print(f"{'No':<4} {'Hash':<8} {'Date':<12} {'Author':<15} {'Message'}")
    print("=" * 80)

    # 커밋 정보 출력
    for i, commit in enumerate(commits[start_index:start_index + 10], start_index + 1):
        message = commit['message']
        if len(message) > 35:
            message = message[:32] + "..."

        print(f"{i:<4} {commit['hash']:<8} {commit['date']:<12} {commit['author']:<15} {message}")

    print("=" * 80)


def print_commit_table_with_menu(commits, start_index=0):
    """커밋 정보를 표 형식으로 출력하고 메뉴 표시"""
    print_commit_table(commits, start_index)

    # DETAIL 메뉴 옵션 추가
    print()
    print("메뉴:")
    print("  1. DETAIL       (세부 정보와 업데이트된 파일 목록)")
    print("  0. BACK         (메인 메뉴로 돌아가기)")
    print()


def show_commit_detail(directory, title, commit, commit_number, use_permission=None):
    """특정 커밋의 세부 정보 표시 (파일 목록 포함)"""
    print(f"=== {title} - DETAIL [{commit_number}] ===")
    print()

    def get_files_for_commit(commit_hash):
        return GitHelper.get_commit_files(directory, commit_hash)

    # 상세 날짜 정보를 직접 가져오기 (읽기 작업이므로 권한 불필요)
    try:
        # Git 1.8 호환을 위해 iso 형식으로 가져온 후 변환
        cmd = ['show', '--pretty=format:%ad', '--date=iso', '-s', commit['hash']]
        result = GitHelper.run_git_command(cmd, cwd=directory, capture_output=True)
        detailed_date = None
        if result:
            # ISO 형식 (2025-09-20 00:32:16 +0900)에서 YYYY/MM/DD HH:MM:SS로 변환
            import re
            match = re.match(r'(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})', result.strip())
            if match:
                year, month, day, hour, minute, second = match.groups()
                detailed_date = f"{year}/{month}/{day} {hour}:{minute}:{second}"
            else:
                detailed_date = result.strip()

        # 결과 정리
        if detailed_date:
            detailed_date = detailed_date.strip()

        # 빈 문자열이면 None으로 처리
        if not detailed_date:
            detailed_date = None

    except Exception as e:
        # display_message(f"날짜 가져오기 실패: {e}", "DEBUG")
        detailed_date = None

    print(f"커밋: {commit['hash']}")
    print(f"작성자: {commit['author']}")
    print(f"날짜: {detailed_date if detailed_date else commit['date']}")
    print(f"메시지: {commit['message']}")
    print()

    # 변경된 파일 목록 가져오기 (읽기 작업이므로 권한 불필요)
    files = get_files_for_commit(commit['hash'])

    if files:
        print("변경된 파일:")
        for j, file_info in enumerate(files, 1):
            print(f"{j:>2}. [{file_info['status']}] {file_info['filename']}")

        print()
        print("옵션:")
        print("  [파일번호] DIFF 보기 (VS Code)")
        print("  0. 돌아가기")
        print()

        while True:
            choice = messagebox("파일 번호를 선택하세요 (1-{0}, 0):".format(len(files)), "파일 선택", "info", "input", "0")

            if choice == "0":
                break

            try:
                file_number = int(choice)
                if 1 <= file_number <= len(files):
                    selected_file = files[file_number - 1]
                    show_file_diff(directory, commit['hash'], selected_file, use_permission)
                else:
                    print(f"잘못된 번호입니다. 1부터 {len(files)} 사이의 번호를 입력하세요.")
                    continue
            except ValueError:
                print("숫자를 입력하세요.")
                continue
    else:
        print("변경된 파일: 없음")

    print()
    print("-" * 80)


def run_vscode_diff(before_file_path, after_file_path, description="파일 비교"):
    """통합된 VS Code diff 실행 함수 - 향상된 경로 탐색"""
    # VS Code 명령어 찾기 (향상된 탐색 로직)
    vscode_cmd = find_vscode_command()

    if vscode_cmd:
        try:
            display_message(f"{description}를 VS Code로 표시합니다...", "INFO")
            # --new-window 옵션 포함 (기존 코드와 동일)
            # --no-sandbox 옵션 추가 (회사 환경 대응)
            # TUI 모드에서는 출력 캡처하여 화면 깨짐 방지
            from .ui_handler import _ui_handler
            if _ui_handler and hasattr(_ui_handler, 'messagebox'):
                subprocess.run([vscode_cmd, '--no-sandbox', '--new-window', '--wait', '--diff', before_file_path, after_file_path],
                              check=True, capture_output=True)
            else:
                subprocess.run([vscode_cmd, '--no-sandbox', '--new-window', '--wait', '--diff', before_file_path, after_file_path], check=True)
            display_message("VS Code diff 완료", "INFO")
            return True
        except subprocess.CalledProcessError:
            display_message(f"VS Code 실행 실패. 시스템에 {vscode_cmd} 명령이 설치되어 있는지 확인하세요.", "ERROR")
            return False
        except FileNotFoundError:
            display_message(f"VS Code를 찾을 수 없습니다. 시스템에 {vscode_cmd}가 설치되어 있는지 확인하세요.", "ERROR")
            return False
    else:
        display_message("VS Code를 찾을 수 없습니다. 다음을 확인하세요:\n"
                       "  1. config.ini에 [VSCODE] PATH=/path/to/code 설정\n"
                       "  2. 환경변수 VSCODE_PATH 설정\n"
                       "  3. code 또는 vscode 명령 설치 확인", "ERROR")
        return False


def show_file_diff(directory, commit_hash, file_info, use_permission=None):
    """VS Code를 사용하여 파일의 diff 표시"""
    filename = file_info['filename']
    status = file_info['status']

    try:
        def get_file_content_before():
            """커밋 이전 파일 내용 가져오기"""
            try:
                cmd = ['show', f'{commit_hash}^:{filename}']
                return GitHelper.run_git_command(cmd, cwd=directory, capture_output=True)
            except:
                return ""

        def get_file_content_after():
            """커밋 이후 파일 내용 가져오기"""
            try:
                cmd = ['show', f'{commit_hash}:{filename}']
                return GitHelper.run_git_command(cmd, cwd=directory, capture_output=True)
            except:
                return ""

        # 파일 내용 가져오기 (읽기 작업이므로 권한 불필요)
        before_content = get_file_content_before()
        after_content = get_file_content_after()

        # 임시 파일 생성
        with tempfile.NamedTemporaryFile(mode='w', suffix=f'_before_{filename.replace("/", "_")}', delete=False) as before_file:
            before_file.write(before_content or "")
            before_file_path = before_file.name

        with tempfile.NamedTemporaryFile(mode='w', suffix=f'_after_{filename.replace("/", "_")}', delete=False) as after_file:
            after_file.write(after_content or "")
            after_file_path = after_file.name

        # 통합된 VS Code diff 실행
        run_vscode_diff(before_file_path, after_file_path, f"[{status}] {filename} 변경 내용")

        # 임시 파일 정리
        try:
            os.unlink(before_file_path)
            os.unlink(after_file_path)
        except:
            pass

    except Exception as e:
        print(f"Diff 표시 중 오류 발생: {e}")


def show_git_history(directory, title, use_permission=None):
    """Git 히스토리 표시 (페이징 포함)"""
    print(f"=== {title} ===")
    print()

    if not GitHelper.is_git_repo(directory):
        print(f"Git 저장소가 없습니다: {directory}")
        return

    # Git 로그 가져오기 (읽기 작업이므로 권한 불필요)
    commits = GitHelper.get_git_log(directory)

    if not commits:
        print("커밋 히스토리가 없습니다.")
        return

    start_index = 0
    while True:
        # 10개씩 표시하고 더 있으면 MORE 옵션 제공
        remaining = len(commits) - (start_index + 10)
        if remaining > 0:
            print_commit_table(commits, start_index)
            print(f"\n더 {remaining}개의 커밋이 있습니다.")
            result = messagebox("더 보시겠습니까?", "히스토리", "info", "yesno", "no")
            choice = result
            if choice in ('y', 'yes'):
                start_index += 10
                continue

        break

    # 히스토리 테이블과 메뉴를 함께 표시하는 통합 함수 사용
    print_commit_table_with_menu(commits, start_index)

    while True:
        choice = messagebox("선택하세요 (1, 0):", "히스토리", "info", "input", "0")

        if choice == "1":
            # 커밋 번호 입력받기
            print()
            commit_choice = messagebox("몇번 커밋을 자세히 보시겠습니까? (1-{0}):".format(len(commits)), "커밋 선택", "info", "input", "")

            try:
                commit_number = int(commit_choice)
                if 1 <= commit_number <= len(commits):
                    selected_commit = commits[commit_number - 1]
                    show_commit_detail(directory, title, selected_commit, commit_number, use_permission)

                    # 세부 로그 출력 후 다시 히스토리 테이블과 메뉴 표시
                    print()
                    print_commit_table_with_menu(commits, start_index)
                else:
                    print(f"잘못된 번호입니다. 1부터 {len(commits)} 사이의 번호를 입력하세요.")
                    continue
            except ValueError:
                print("숫자를 입력하세요.")
                continue

            # DETAIL 표시 후 다시 메뉴로
            print()
            continue
        elif choice == "0":
            break
        else:
            print(f"잘못된 선택: {choice}")
            print("1 또는 0을 입력하세요.")
