"""Independent deterministic tools. No eval, SQL, generated code or network calls."""

from collections.abc import Callable

import pandas as pd

from app.core import AppError, ensure_json
from app.profiling import profile, scalar
from app.schemas import Scope, ToolArgs, ToolResult


TOOL_DESCRIPTIONS = {
    "dataset_summary": "Compute dataset quality and descriptive statistics.",
    "group_aggregate": "Aggregate metrics by dimensions, including weighted profit margin.",
    "trend_analysis": "Aggregate metrics into observed calendar periods.",
    "ranking_analysis": "Rank groups by a metric, ascending for Bottom-K.",
    "growth_analysis": "Compare calendar-aligned periods; undefined growth has an explicit reason.",
    "anomaly_detection": "Find row-level IQR outliers, optionally within dimension groups.",
    "correlation_analysis": "Compute pairwise Pearson correlation with complete-case counts.",
    "business_rule_check": "Evaluate configured business rules against computed facts.",
}


def validate_columns(frame: pd.DataFrame, args: ToolArgs, tool: str) -> None:
    for name in args.dimensions + list(args.scope.filters):
        if name not in frame:
            raise AppError("unknown_column", f"Unknown column: {name}")
    for metric in args.metrics:
        required = ["sales_amount", "profit"] if metric == "profit_margin" else [metric]
        for name in required:
            if name not in frame or not pd.api.types.is_numeric_dtype(frame[name]):
                raise AppError("invalid_metric", f"Metric requires numeric column: {name}")
        if metric == "profit_margin" and args.aggregation != "sum":
            raise AppError("invalid_aggregation", "Profit margin is a ratio of sums, not an average of row ratios")
    date_column = args.scope.date_column
    if date_column and (date_column not in frame or not pd.api.types.is_datetime64_any_dtype(frame[date_column])):
        raise AppError("invalid_date_column", "date_column must identify a parsed date field")
    if tool not in {"dataset_summary", "business_rule_check"} and not args.metrics:
        raise AppError("missing_metric", "Select at least one numeric metric")
    if tool in {"trend_analysis", "growth_analysis"} and not date_column:
        raise AppError("missing_date", "Trend and growth tools require date_column")
    if tool == "ranking_analysis" and (len(args.metrics) != 1 or not args.dimensions):
        raise AppError("invalid_ranking", "Ranking requires one metric and at least one dimension")
    if tool == "correlation_analysis" and (len(args.metrics) < 2 or "profit_margin" in args.metrics):
        raise AppError("invalid_correlation", "Select at least two raw numeric columns")
    if tool == "growth_analysis" and args.comparison == "year_over_year" and args.frequency != "M":
        raise AppError("invalid_comparison", "Year-over-year comparisons use monthly periods")


def select_frame(frame: pd.DataFrame, scope: Scope) -> pd.DataFrame:
    view = frame
    for name, value in scope.filters.items():
        view = view.loc[view[name] == value]
    if scope.date_column:
        dates = view[scope.date_column]
        mask = dates.notna()
        if scope.start_date:
            mask &= dates >= pd.Timestamp(scope.start_date)
        if scope.end_date:
            mask &= dates < pd.Timestamp(scope.end_date) + pd.Timedelta(days=1)
        if scope.last_n_months and dates.notna().any():
            end = dates.max().to_period("M")
            mask &= dates >= (end - scope.last_n_months + 1).start_time
        view = view.loc[mask]
    if view.empty:
        raise AppError("empty_selection", "No records match the requested scope")
    return view


def metric_value(frame: pd.DataFrame, metric: str, aggregation: str) -> tuple[float | int | None, str | None]:
    if metric == "profit_margin":
        inputs = frame[["profit", "sales_amount"]]
        if inputs.isna().any().any():
            return None, "incomplete_ratio_inputs"
        denominator = inputs.sales_amount.sum()
        if denominator <= 0:
            return None, "nonpositive_sales_denominator"
        return scalar(inputs.profit.sum() / denominator), None
    series = frame[metric]
    if aggregation == "count":
        return int(series.count()), None
    if series.count() == 0:
        return None, "all_values_missing"
    value = series.sum(min_count=1) if aggregation == "sum" else getattr(series, aggregation)()
    return scalar(value), "missing_values_excluded" if series.isna().any() else None


def groups(frame: pd.DataFrame, dimensions: list[str]):
    if not dimensions:
        yield {}, frame
        return
    for key, group in frame.groupby(dimensions, dropna=False, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        yield {name: scalar(value) for name, value in zip(dimensions, key)}, group


def aggregate(frame: pd.DataFrame, args: ToolArgs, period: bool = False) -> list[dict]:
    records = []
    for dimension, group in groups(frame, args.dimensions):
        slices = group.groupby(group[args.scope.date_column].dt.to_period(args.frequency)) if period else [(None, group)]
        for timestamp, part in slices:
            for metric in args.metrics:
                value, reason = metric_value(part, metric, args.aggregation)
                records.append({"dimension": dimension, "period": str(timestamp) if timestamp is not None else None,
                                "metric": metric, "aggregation": args.aggregation, "value": value,
                                "count": len(part), "reason": reason})
                if len(records) > 2000:
                    raise AppError("result_too_large", "Narrow the scope: result exceeds 2000 records")
    return records


def growth_rate(current, previous) -> tuple[float | None, str | None]:
    if current is None or previous is None:
        return None, "missing_period_or_value"
    if previous == 0:
        return None, "zero_previous_value"
    if previous < 0:
        return None, "negative_previous_value"
    return scalar((current - previous) / previous), None


class ToolRegistry:
    def __init__(self, rule_runner: Callable | None = None):
        self.rule_runner = rule_runner

    def definitions(self) -> list[dict]:
        return [{"type": "function", "function": {"name": name, "description": description,
                  "parameters": ToolArgs.model_json_schema()}} for name, description in TOOL_DESCRIPTIONS.items()]

    def validate(self, name: str, args: ToolArgs, frame: pd.DataFrame) -> None:
        if name not in TOOL_DESCRIPTIONS:
            raise AppError("unknown_tool", "Only registered tools may be called")
        validate_columns(frame, args, name)

    def run(self, name: str, arguments: ToolArgs | dict, frame: pd.DataFrame) -> ToolResult:
        args = arguments if isinstance(arguments, ToolArgs) else ToolArgs.model_validate(arguments)
        self.validate(name, args, frame)
        view = select_frame(frame, args.scope)
        output = ToolResult(tool=name, rows_considered=len(view))
        if len(view) < len(frame):
            output.warnings.append(f"Scope selected {len(view)} of {len(frame)} records.")
        if view.isna().any().any():
            output.warnings.append("Missing values remain; numeric aggregates exclude missing values unless noted.")
        if name == "dataset_summary":
            output.statistics = profile(view)
        elif name in {"group_aggregate", "ranking_analysis", "trend_analysis", "growth_analysis"}:
            output.records = aggregate(view, args, name in {"trend_analysis", "growth_analysis"})
            if name == "group_aggregate":
                for record in output.records:
                    total, _ = metric_value(view, record["metric"], args.aggregation)
                    value = record["value"]
                    record["contribution"] = (value / total if args.aggregation == "sum"
                        and record["metric"] != "profit_margin" and total is not None
                        and total > 0 and value is not None and value >= 0 else None)
            if name == "ranking_analysis":
                valid = [r for r in output.records if r["value"] is not None]
                valid.sort(key=lambda r: r["value"], reverse=not args.ascending)
                for index, record in enumerate(valid):
                    record["rank"] = index + 1 if index == 0 or record["value"] != valid[index - 1]["value"] else valid[index - 1]["rank"]
                output.statistics = {"groups_total": len(output.records), "undefined_groups": len(output.records) - len(valid),
                                     "tie_policy": "competition rank; stable dimension order; exact top_k"}
                output.records = valid[:args.top_k]
            if name == "growth_analysis":
                lag = 12 if args.comparison == "year_over_year" else 1
                lookup = {(tuple(r["dimension"].items()), r["metric"], r["period"]): r["value"] for r in output.records}
                for record in output.records:
                    previous_period = str(pd.Period(record["period"], freq=args.frequency) - lag)
                    previous = lookup.get((tuple(record["dimension"].items()), record["metric"], previous_period))
                    growth, reason = growth_rate(record["value"], previous)
                    record.update({"previous_period": previous_period, "previous_value": previous,
                                   "growth_rate": growth, "growth_reason": reason,
                                   "delta": record["value"] - previous if record["value"] is not None and previous is not None else None})
                output.warnings.append("Calendar-aligned growth uses only observations in the selected scope; missing periods are not zero-filled. Edge periods may be partial.")
        elif name == "anomaly_detection":
            self._anomalies(view, args, output)
        elif name == "correlation_analysis":
            for index, left in enumerate(args.metrics):
                for right in args.metrics[index + 1:]:
                    pair = view[[left, right]].dropna()
                    usable = len(pair) >= 3 and pair[left].nunique() > 1 and pair[right].nunique() > 1
                    output.records.append({"left": left, "right": right, "n": len(pair),
                        "correlation": scalar(pair[left].corr(pair[right])) if usable else None,
                        "reason": None if usable else "insufficient_or_constant_data"})
            output.warnings.append("Pearson correlation does not establish causation.")
        elif name == "business_rule_check":
            if self.rule_runner is None:
                raise AppError("rules_unavailable", "No rule engine is configured", 503)
            output.records, output.statistics = self.rule_runner(view, args)
        # Reject nonfinite or non-JSON results before persistence or model access.
        return ToolResult.model_validate(ensure_json(output.model_dump(mode="json")))

    @staticmethod
    def _anomalies(view: pd.DataFrame, args: ToolArgs, output: ToolResult) -> None:
        count = 0
        for dimension, group in groups(view, args.dimensions):
            for metric in args.metrics:
                if metric == "profit_margin":
                    values = group.profit.div(group.sales_amount.where(group.sales_amount > 0))
                else:
                    values = group[metric]
                values = values.dropna()
                if len(values) < 4:
                    output.warnings.append(f"Insufficient observations for IQR: {metric}, {dimension}")
                    continue
                q1, q3 = values.quantile([0.25, 0.75])
                width = q3 - q1
                lower, upper = q1 - args.iqr_multiplier * width, q3 + args.iqr_multiplier * width
                outliers = values[(values < lower) | (values > upper)]
                count += len(outliers)
                for index, value in outliers.items():
                    if len(output.records) >= 200:
                        break
                    output.records.append({"row_index": int(index), "dimension": dimension, "metric": metric,
                        "value": scalar(value), "expected_range": [scalar(lower), scalar(upper)],
                        "deviation": scalar(value - (upper if value > upper else lower)),
                        "timestamp": scalar(view.loc[index, args.scope.date_column]) if args.scope.date_column else None})
        output.statistics = {"method": "IQR", "outlier_count": count, "returned_count": len(output.records),
                             "truncated": count > len(output.records), "scope": "row-level within selected groups"}
