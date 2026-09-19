"""Validated contracts shared by providers, tools, orchestration and HTTP."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Scope(Contract):
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=8)
    date_column: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    last_n_months: int | None = Field(None, ge=1, le=36)

    @model_validator(mode="after")
    def dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date")
        if self.last_n_months and (self.start_date or self.end_date):
            raise ValueError("Choose absolute dates or last_n_months, not both")
        if (self.start_date or self.end_date or self.last_n_months) and not self.date_column:
            raise ValueError("Time filters require date_column")
        return self


AnalysisType = Literal["summary", "trend", "comparison", "ranking", "anomaly", "growth", "correlation", "business_diagnosis"]
ToolName = Literal["dataset_summary", "group_aggregate", "trend_analysis", "ranking_analysis", "growth_analysis", "anomaly_detection", "correlation_analysis", "business_rule_check"]


class ToolArgs(Contract):
    metrics: list[str] = Field(default_factory=list, max_length=8)
    dimensions: list[str] = Field(default_factory=list, max_length=2)
    aggregation: Literal["sum", "mean", "count", "min", "max"] = "sum"
    scope: Scope = Field(default_factory=Scope)
    frequency: Literal["D", "M"] = "M"
    comparison: Literal["previous_period", "year_over_year"] = "previous_period"
    top_k: int = Field(5, ge=1, le=50)
    ascending: bool = False
    iqr_multiplier: float = Field(1.5, gt=0, le=10)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.metrics)) != len(self.metrics) or len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("Metrics and dimensions must be unique")
        if set(self.metrics) & set(self.dimensions):
            raise ValueError("A metric cannot also be a grouping dimension")
        return self


class Intent(ToolArgs):
    analysis_type: AnalysisType = "summary"


class Step(Contract):
    tool: ToolName
    arguments: ToolArgs


class Plan(Contract):
    steps: list[Step] = Field(min_length=1, max_length=8)


class AnalysisRequest(Contract):
    dataset_id: UUID
    query: str = Field(min_length=1, max_length=2000)
    intent: Intent | None = None

    @model_validator(mode="after")
    def meaningful_query(self):
        if not self.query.strip():
            raise ValueError("Query cannot be whitespace")
        return self


class ToolResult(Contract):
    tool: ToolName
    rows_considered: int
    records: list[dict] = Field(default_factory=list)
    statistics: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
