"""Deterministic, JSON-safe data profiling with bounded categorical output."""

import math
from datetime import date, datetime

import numpy as np
import pandas as pd


def scalar(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def profile(frame: pd.DataFrame) -> dict:
    result = {"rows": len(frame), "columns": len(frame.columns), "column_names": list(frame),
              "dtypes": {}, "missing": {}, "duplicates": int(frame.duplicated().sum()),
              "numeric_columns": [], "categorical_columns": [], "date_columns": [],
              "numeric_statistics": {}, "categorical_statistics": {}, "date_ranges": {}}
    for name in frame:
        values = frame[name]
        result["dtypes"][name] = str(values.dtype)
        result["missing"][name] = {"count": int(values.isna().sum()),
                                    "ratio": float(values.isna().mean()) if len(values) else 0.0}
        if pd.api.types.is_datetime64_any_dtype(values):
            result["date_columns"].append(name)
            result["date_ranges"][name] = {"min": scalar(values.min()), "max": scalar(values.max())}
        elif pd.api.types.is_numeric_dtype(values) and not pd.api.types.is_bool_dtype(values):
            result["numeric_columns"].append(name)
            result["numeric_statistics"][name] = {k: scalar(v) for k, v in values.describe().items()}
        else:
            result["categorical_columns"].append(name)
            result["categorical_statistics"][name] = {
                "unique": int(values.nunique()),
                "top_values": [{"value": scalar(k), "count": int(v)}
                               for k, v in values.value_counts().head(10).items()]}
    return result
