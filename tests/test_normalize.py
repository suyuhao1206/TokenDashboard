"""C1-1：normalize_output() 的四种口径。

output_tokens 一律为「含推理的总输出」，下游不再加 reasoning_tokens。
"""

from __future__ import annotations

import pytest

from token_dashboard.normalize import normalize_output


@pytest.mark.parametrize(
    ("output", "reasoning", "total", "prompt", "expected", "why"),
    [
        # OpenAI / Codex(gpt-*)：total == prompt + output，推理已含在 output 内
        (100, 40, 1100, 1000, 100, "OpenAI 口径：推理是 output 的子集，不能再加"),
        # 第三方（gemini 等）：total == prompt + output + reasoning，需要相加
        (100, 40, 1140, 1000, 140, "独立上报推理：必须相加"),
        # total 缺失 → 启发式
        (100, 40, None, 1000, 100, "reasoning < output：判定已包含"),
        (100, 400, None, 1000, 500, "reasoning > output：判定未包含"),
        # reasoning = 0：直接返回 output（不参与任何判断）
        (100, 0, 1500, 1000, 100, "reasoning=0 时 total 不可信也不影响结果"),
        # 完全空的输出
        (0, 0, None, 0, 0, "空事件"),
    ],
)
def test_normalize_output(output, reasoning, total, prompt, expected, why):
    assert normalize_output(output, reasoning, total, prompt) == expected, why


def test_normalize_output_tolerates_off_by_one_total():
    """total 与 prompt+output+reasoning 差 1 时仍按「未包含」处理（供应商取整）。"""
    assert normalize_output(100, 40, 1141, 1000) == 140
