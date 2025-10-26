#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLI Mode Implementation for CCCopy
CLI 모드 구현 모듈
"""

from ..utils.ui_handler import display_message, messagebox


def run_cli_mode(workspace):
    """CLI 모드 실행 (기존 메뉴 방식)"""
    while True:
        display_message("", "INFO")
        display_message("=" * 50, "INFO")
        display_message("CCCOPY - Git-based Team Collaboration Tool", "INFO")
        display_message("=" * 50, "INFO")
        display_message("", "INFO")
        display_message("PRODUCTION", "INFO")
        display_message("  1. DOWNLOAD     (production -> work)", "INFO")
        display_message("  2. UPLOAD       (work -> production)", "INFO")
        display_message("  3. HISTORY      (production git log)", "INFO")
        display_message("", "INFO")
        display_message("WORK", "INFO")
        display_message("  4. SAVE         (commit changes)", "INFO")
        display_message("  5. HISTORY      (work git log)", "INFO")
        display_message("", "INFO")
        display_message("  0. EXIT", "INFO")
        display_message("", "INFO")

        choice = messagebox("선택하세요 (1-5, 0):", "메인 메뉴", "info", "input", "0")

        if choice == "1" or choice.lower() == "download":
            workspace.download()
        elif choice == "2" or choice.lower() == "upload":
            workspace.upload()
        elif choice == "3":
            workspace.production_history()
        elif choice == "4" or choice.lower() == "save":
            workspace.save()
        elif choice == "5":
            workspace.work_history()
        elif choice == "0" or choice.lower() == "exit":
            display_message("cccopy를 종료합니다.", "INFO")
            break
        else:
            display_message(f"잘못된 선택: {choice}", "ERROR")
            display_message("1, 2, 3, 4, 5, 또는 0을 입력하세요.", "INFO")
