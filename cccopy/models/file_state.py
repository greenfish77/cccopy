"""파일 상태 관리 모듈"""


class FileState:
    """파일 상태 열거형"""
    SAME = "same"
    MODIFIED = "modified"
    UPDATED = "updated"
    CONFLICTED = "conflicted"
    PENDING = "pending"  # 상태 확인 중 (thread 처리 중)
