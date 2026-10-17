"""TokenWidget.exe 的入口：双击即启动桌面小组件；也支持传参（如 --interval 30）。"""

import sys

from token_dashboard.cli import main

argv = sys.argv[1:]
if not argv or argv[0].startswith("-"):
    argv = ["widget"] + argv
sys.argv = [sys.argv[0]] + argv
raise SystemExit(main())
