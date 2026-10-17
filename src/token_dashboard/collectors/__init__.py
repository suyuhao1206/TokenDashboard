"""采集器注册表。

- HARNESS：把事件写入 usage_events（harness 维度）
- CHANNEL：把渠道汇总写入 channel_daily（渠道维度，独立口径）
"""

from . import cc_switch, claude_code, codex, dsh, workbuddy

ALL = {m.SOURCE: m for m in (claude_code, codex, dsh, workbuddy)}
CHANNEL = {m.SOURCE: m for m in (cc_switch,)}
