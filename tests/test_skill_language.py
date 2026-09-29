from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOTS = (
    ROOT / ".agents" / "skills",
    ROOT / ".claude" / "skills",
    ROOT / ".hermes-skills",
)

ENGLISH_PROSE_MARKERS = (
    "description: Use when",
    "description: Extract ",
    "description: Fill ",
    "description: Process ",
    "description: Monitor ",
    "description: AI chip ",
    "You are ",
    "This skill ",
    "## Overview",
    "## When to",
    "## Quick Reference",
    "## Workflow",
    "## Input Contract",
    "## Output Contract",
    "## Common Mistakes",
    "## Completion",
    "### Step ",
)


def skill_files() -> list[Path]:
    return sorted(
        path for root in SKILL_ROOTS for path in root.glob("*/SKILL.md")
        if path.parent.name != "brainstorming"  # 用户安装的第三方 Skill，非项目产物
    )


@pytest.mark.parametrize("path", skill_files(), ids=lambda path: str(path.relative_to(ROOT)))
def test_project_skill_human_facing_prose_is_chinese(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    description = next(
        (line.removeprefix("description:").strip() for line in text.splitlines()
         if line.startswith("description:")),
        "",
    )

    assert re.search(r"[\u4e00-\u9fff]", description), "description 应使用中文"
    for marker in ENGLISH_PROSE_MARKERS:
        assert marker not in text, f"仍存在英文说明：{marker}"


@pytest.mark.parametrize(
    "skill_name", ("chip-catalog", "chip-enrich", "chip-recommend-cli", "chip-selector-agent")
)
def test_agents_and_claude_share_the_same_chinese_skill(skill_name: str) -> None:
    agents = (ROOT / ".agents" / "skills" / skill_name / "SKILL.md").read_text(
        encoding="utf-8"
    )
    claude = (ROOT / ".claude" / "skills" / skill_name / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert agents == claude
