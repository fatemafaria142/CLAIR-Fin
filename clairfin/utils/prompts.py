from __future__ import annotations

from functools import lru_cache

from configs.settings import get_settings


@lru_cache
def load_prompt(name: str) -> str:
    """`name` is the path under `prompts/` without extension, e.g. `"agents/planner_orchestrator"`
    or `"tools/entailment_judge"`."""
    path = get_settings().paths.prompts_dir / f"{name}.md"
    return path.read_text(encoding="utf-8").strip()
