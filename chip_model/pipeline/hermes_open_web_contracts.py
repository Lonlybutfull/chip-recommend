"""Strict JSON contracts shared by Hermes and the local open-web tool service."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)


HERMES_OPEN_WEB_SKILLS = (
    "chip-identity",
    "chip-specs",
    "chip-compute",
    "chip-compatibility",
    "chip-benchmark",
    "chip-deployment",
)

SKILL_LABELS = {
    "chip-identity": "芯片型号",
    "chip-specs": "基础参数",
    "chip-compute": "算力指标",
    "chip-compatibility": "兼容信息",
    "chip-benchmark": "实测数据",
    "chip-deployment": "部署资料",
}

SkillName = Literal[
    "chip-identity",
    "chip-specs",
    "chip-compute",
    "chip-compatibility",
    "chip-benchmark",
    "chip-deployment",
]
CategoryName = Literal[
    "芯片型号", "基础参数", "算力指标", "兼容信息", "实测数据", "部署资料"
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,80}$")
CANDIDATE_ID_PATTERN = re.compile(r"^candidate-[a-f0-9]{12,64}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchQuery(StrictModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=300)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class SearchRequest(StrictModel):
    run_id: NonEmptyText
    skill: SkillName
    target_chip: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    queries: Annotated[list[SearchQuery], Field(min_length=10, max_length=10)]

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        if not RUN_ID_PATTERN.fullmatch(value):
            raise ValueError("运行 ID 格式无效。")
        return value

    @model_validator(mode="after")
    def validate_unique_queries(self) -> "SearchRequest":
        normalized = [" ".join(item.query.split()).casefold() for item in self.queries]
        if len(set(normalized)) != len(normalized):
            raise ValueError("10 条搜索词规范化后不得重复。")
        return self


class PreviewRequest(StrictModel):
    run_id: NonEmptyText
    candidate_ids: Annotated[list[str], Field(min_length=1, max_length=100)]

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        if not RUN_ID_PATTERN.fullmatch(value):
            raise ValueError("运行 ID 格式无效。")
        return value

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("候选 ID 不得重复。")
        if not all(CANDIDATE_ID_PATTERN.fullmatch(value) for value in cleaned):
            raise ValueError("候选 ID 格式无效。")
        return cleaned


class UrlDecision(StrictModel):
    candidate_id: NonEmptyText
    selected: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=1000)]
    matched_categories: list[CategoryName] = Field(default_factory=list, max_length=6)
    suggested_skills: list[SkillName] = Field(default_factory=list, max_length=6)

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        if not CANDIDATE_ID_PATTERN.fullmatch(value):
            raise ValueError("候选 ID 格式无效。")
        return value

    @field_validator("matched_categories", "suggested_skills")
    @classmethod
    def validate_unique_values(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("类别或建议 Skill 不得重复。")
        return values


class SelectionRequest(StrictModel):
    run_id: NonEmptyText
    skill: SkillName
    decisions: Annotated[list[UrlDecision], Field(min_length=1, max_length=100)]

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        if not RUN_ID_PATTERN.fullmatch(value):
            raise ValueError("运行 ID 格式无效。")
        return value

    @model_validator(mode="after")
    def validate_unique_candidates(self) -> "SelectionRequest":
        values = [item.candidate_id for item in self.decisions]
        if len(set(values)) != len(values):
            raise ValueError("同一候选只能提交一次决定。")
        return self


class ExtractedFact(StrictModel):
    field_name: NonEmptyText
    proposed_value: str
    unit: str = ""
    evidence_text: NonEmptyText
    evidence_location: str = "正文"
    confidence: Literal["high", "medium", "low"]


class ExtractionResult(StrictModel):
    """Semantic fields accepted from an untrusted model response."""

    relevant: StrictBool
    reason: str = ""
    chip_model: str = ""
    matched_categories: list[CategoryName] = Field(default_factory=list, max_length=6)
    source_type: str = ""
    facts: list[ExtractedFact] = Field(default_factory=list, max_length=200)

    @field_validator("matched_categories")
    @classmethod
    def validate_unique_categories(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("提取结果的信息类别不得重复。")
        return values
