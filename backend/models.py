from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=80)
    idea: str = Field(min_length=1, max_length=12000)


class PRDInput(Input):
    content: str = Field(min_length=1, max_length=100000)
    expected_version: int = Field(ge=0)
    change_note: str = Field(default="需求更新", min_length=1, max_length=300)

    @field_validator("content")
    @classmethod
    def text_only(cls, value):
        if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("只接受 UTF-8 文本文档")
        return value


class ImportInput(PRDInput):
    filename: str = Field(min_length=1, max_length=120)

    @field_validator("filename")
    @classmethod
    def markdown_filename(cls, value):
        if "/" in value or "\\" in value or not value.lower().endswith(".md"):
            raise ValueError("请选择 .md 文件，文件名不能包含路径")
        return value


class DraftConfirm(Input):
    draft_id: str = Field(min_length=32, max_length=32)
    expected_version: int = Field(ge=0)
    change_note: str = Field(default="确认 AI 需求草稿", min_length=1, max_length=300)


class InterviewInput(Input):
    message: str = Field(min_length=1, max_length=12000)


class TaskUpdate(Input):
    status: Literal["todo", "doing", "done", "blocked"]


class InterviewResult(Input):
    reply: str = Field(min_length=1, max_length=12000)
    questions: list[str] = Field(max_length=5)
    ready: bool
    draft: str = Field(min_length=1, max_length=100000)


class GeneratedTask(Input):
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=4000)
    acceptance: str = Field(min_length=1, max_length=4000)
    priority: Literal["P0", "P1", "P2"]


class TaskResult(Input):
    tasks: list[GeneratedTask] = Field(min_length=1, max_length=40)


class TeardownInput(Input):
    product_name: str = Field(min_length=1, max_length=80)
    product_url: str = Field(default="", max_length=2000)
    focus: str = Field(default="", max_length=2000)
    depth: Literal["standard", "deep"] = "standard"

    @field_validator("product_url")
    @classmethod
    def public_product_url(cls, value):
        if value:
            from backend.research import public_url
            checked = public_url(value)
            if not checked:
                raise ValueError("请输入公开产品的 HTTP 或 HTTPS 网址")
            return checked
        return value


class MatrixItem(Input):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=800)
    tone: Literal["red", "blue", "green", "orange", "purple", "gray"]


class JourneyItem(Input):
    stage: Literal["入口", "输入", "过程", "输出", "交付"]
    user: str = Field(min_length=1, max_length=600)
    system: str = Field(min_length=1, max_length=600)
    checkpoint: str = Field(min_length=1, max_length=600)
    exception: str = Field(min_length=1, max_length=600)


class ComparisonItem(Input):
    label: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=800)
    alternatives: str = Field(min_length=1, max_length=800)


class InsightItem(Input):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=1000)
    tone: Literal["red", "blue", "green", "orange", "purple", "gray"]


class TeardownResult(Input):
    content: str = Field(min_length=500, max_length=40000)
    matrix: list[MatrixItem] = Field(min_length=2, max_length=9)
    journey: list[JourneyItem] = Field(min_length=5, max_length=5)
    comparison: list[ComparisonItem] = Field(min_length=2, max_length=8)
    insights: list[InsightItem] = Field(min_length=3, max_length=3)

    @field_validator("journey")
    @classmethod
    def journey_order(cls, value):
        if [item.stage for item in value] != ["入口", "输入", "过程", "输出", "交付"]:
            raise ValueError("旅程必须按入口、输入、过程、输出、交付排列")
        return value
