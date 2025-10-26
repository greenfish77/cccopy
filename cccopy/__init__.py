#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CCCopy - Git 기반 팀 협업 도구

Production과 Work 두 개의 Git 저장소를 관리하며,
NFS 환경에서의 안전한 동시 접근을 보장합니다.
"""

# Models (no dependencies)
from .models import FileState

# Core modules (depends on models)
from .core import CCCopyError, LockManager

# Utils - UI Handler (no internal dependencies)
from .utils.ui_handler import (
    set_ui_handler,
    set_tui_initializing,
    display_message,
    messagebox,
    _cli_messagebox
)

# Core - GitHelper (depends on ui_handler)
from .core import GitHelper

# Utils - Helpers
from .utils.helpers import (
    check_command_exists,
    find_vscode_command,
    safe_input,
    launch_text_editor,
    launch_terminal,
    get_parent_terminal,
    get_parent_shell
)

# Utils - Permissions (depends on core)
from .utils.permissions import AtomicProductionPermission

# Utils - Preference Management (depends on helpers, ui_handler)
from .utils.preference import PreferenceManager

# Utils - Config & Project Management (depends on core and ui_handler)
from .utils.config import (
    ProductionTagManager,
    ProjectSelectionManager,
    ProjectManager
)

# Utils - File Operations (depends on core, helpers, ui_handler)
from .utils.file_utils import (
    handle_conflict,
    update_work_git_after_merge,
    print_commit_table,
    print_commit_table_with_menu,
    show_commit_detail,
    run_vscode_diff,
    show_file_diff,
    show_git_history
)

# UI - CLI (depends on utils)
from .ui import run_cli_mode

__version__ = '1.0.0'

__all__ = [
    # Core
    'CCCopyError',
    'LockManager',
    'GitHelper',

    # Models
    'FileState',

    # UI Handler
    'set_ui_handler',
    'set_tui_initializing',
    'display_message',
    'messagebox',
    '_cli_messagebox',

    # Helpers
    'check_command_exists',
    'find_vscode_command',
    'safe_input',
    'launch_text_editor',
    'launch_terminal',
    'get_parent_terminal',
    'get_parent_shell',

    # Permissions
    'AtomicProductionPermission',

    # Preference Management
    'PreferenceManager',

    # Config & Project Management
    'ProductionTagManager',
    'ProjectSelectionManager',
    'ProjectManager',

    # File Operations
    'handle_conflict',
    'update_work_git_after_merge',
    'print_commit_table',
    'print_commit_table_with_menu',
    'show_commit_detail',
    'run_vscode_diff',
    'show_file_diff',
    'show_git_history',

    # UI
    'run_cli_mode',

    # Version
    '__version__'
]
