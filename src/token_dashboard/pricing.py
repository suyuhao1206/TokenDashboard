"""价格表与成本计算（USD / 1M tokens）。

加载优先级：
1. config/pricing.overrides.yaml（用户覆盖，最高）
2. data/pricing/litellm.json（`token-dashboard update-pricing` 下载的 LiteLLM 快照）
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

PRICING_URL = (
    "https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/model_prices_and_context_window.json"
)


def _rates_from_litellm(entry: dict) -> dict | None:
    def per_million(key: str) -> float | None:
        v = entry.get(key)
        return v * 1_000_000 if isinstance(v, (int, float)) else None

    r = {
        "input": per_million("input_cost_per_token"),
        "output": per_million("output_cost_per_token"),
        "cache_read": per_million("cache_read_input_token_cost"),
        "cache_write": per_million("cache_creation_input_token_cost"),
    }
    if r["input"] is None and r["output"] is None:
        return None
    return r


class Pricing:
    def __init__(self, root: Path, overrides_file: str | None = None):
        self.root = Path(root)
        self.table: dict[str, dict] = {}
        self._load_overrides(overrides_file)
        self._load_litellm()

    def _load_overrides(self, overrides_file: str | None) -> None:
        path = (
            Path(overrides_file)
            if overrides_file
            else self.root / "config" / "pricing.overrides.yaml"
        )
        if not path.exists():
            return
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for model, rates in (data.get("models") or {}).items():
            self.table[str(model).lower()] = {
                "input": rates.get("input"),
                "output": rates.get("output"),
                "cache_read": rates.get("cache_read"),
                "cache_write": rates.get("cache_write"),
            }

    def _load_litellm(self) -> None:
        candidates = [self.root / "data" / "pricing" / "litellm.json"]
        try:
            from .config import resource_dir

            candidates.append(resource_dir() / "assets" / "litellm.json")  # 打包时内置的快照
        except Exception:
            pass
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for model, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            r = _rates_from_litellm(entry)
            if r:
                self.table.setdefault(model.lower(), r)

    def update_from_network(self) -> str:
        """下载 LiteLLM 价格表快照到 data/pricing/litellm.json。"""
        import urllib.request

        dest = self.root / "data" / "pricing" / "litellm.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(PRICING_URL, timeout=90) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return str(dest)

    def cost(
        self,
        model: str | None,
        input_tokens: int,
        cache_read: int,
        cache_write: int,
        output_tokens: int,
        reasoning: int = 0,
    ) -> float | None:
        """按价格表算成本。

        `output_tokens` 必须已经是**含推理**的总输出（见 UsageEvent 的注释）：
        OpenAI 系 `completion_tokens` 本就把推理算在里面，再叠加 reasoning 会双算
        （实测 2026-05-25 Codex：多算 $0.2045，正好等于推理量 × 输出费率）。
        `reasoning` 参数保留只为调用方兼容，不参与计算。
        """
        if not model:
            return None
        rates = self.table.get(model.lower())
        if not rates:
            return None

        total = 0.0
        missing = False
        for key, tokens in (
            ("input", input_tokens),
            ("cache_read", cache_read),
            ("cache_write", cache_write),
            ("output", output_tokens),
        ):
            rate = rates.get(key)
            if rate is None:
                if tokens:
                    missing = True
                continue
            total += tokens / 1_000_000 * rate
        return None if missing else total
