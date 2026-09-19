# API usage and explicit intents

The service is synchronous. Upload once, retain the returned dataset UUID, and
use that UUID in subsequent requests. Dataset IDs are workspace-local; an ID
from a temporary CLI demo disappears when that temporary workspace is removed.

## Exact requests

The following bodies target `POST /analysis`. Replace `DATASET_UUID`. An explicit
intent takes precedence over language parsing, while the query remains context
for the report. Check names and types using the dataset profile first.

Monthly regional sales trend, restricted to the latest three observed months:

```json
{
  "dataset_id": "DATASET_UUID",
  "query": "Analyze the recent monthly sales trend by region",
  "intent": {
    "analysis_type": "trend",
    "metrics": ["sales_amount"],
    "dimensions": ["region"],
    "scope": {"date_column": "date", "last_n_months": 3},
    "frequency": "M"
  }
}
```

The first selected month may have no preceding month inside the scope. This is
reported as undefined growth, not zero growth. Include more history when needed.

Bottom three products by weighted profit margin in an exact region/date range:

```json
{
  "dataset_id": "DATASET_UUID",
  "query": "Rank the lowest product margins in North",
  "intent": {
    "analysis_type": "ranking",
    "metrics": ["profit_margin"],
    "dimensions": ["product"],
    "aggregation": "sum",
    "ascending": true,
    "top_k": 3,
    "scope": {
      "filters": {"region": "North"},
      "date_column": "date",
      "start_date": "2026-04-01",
      "end_date": "2026-06-30"
    }
  }
}
```

Filters currently support equality only. Supply values matching the dataset's
actual categories; this example is a schema illustration, not an assertion
that every uploaded dataset contains `North`.

Product-level margin anomalies:

```json
{
  "dataset_id": "DATASET_UUID",
  "query": "Find unusual row-level product margins",
  "intent": {
    "analysis_type": "anomaly",
    "metrics": ["profit_margin"],
    "dimensions": ["product"],
    "scope": {"date_column": "date"},
    "iqr_multiplier": 1.5
  }
}
```

Regional diagnosis (including configured sales-growth/profit-decline checks):

```json
{
  "dataset_id": "DATASET_UUID",
  "query": "Check regional business conditions",
  "intent": {
    "analysis_type": "business_diagnosis",
    "metrics": ["sales_amount", "profit"],
    "dimensions": ["region"],
    "scope": {"date_column": "date"}
  }
}
```

Other `analysis_type` values: `summary`, `comparison`, `growth`, `correlation`.
Correlation requires at least two raw numeric columns. For year-over-year growth,
set `analysis_type: growth`, `frequency: M`, `comparison: year_over_year` and
include the comparison year inside the selected scope.

## Inspecting the response

`plan.steps` contains validated tool calls. `tool_results` contains the numeric
results, statistics and warnings. `trace` identifies stages, sources and timing.
`report.key_findings` links evidence IDs and paths to the corresponding result.
`report.provenance` distinguishes the configured provider from the providers
actually used after fallback.

A null metric is not automatically zero: inspect `reason` and `growth_reason`.
Outlier count may exceed returned records; inspect the truncation statistics.
Do not treat possible causes as established business facts.

## Errors and retry behavior

Responses use `{"error":{"code":"...","message":"...","request_id":"..."}}`.
A failed task may also provide `analysis_id`; `GET /analysis/{analysis_id}`
returns its persisted failed status. A report request for an unfinished or failed
task returns 409. Invalid UUIDs/queries/plans produce 422, unknown IDs 404,
authentication failures 401, and exceeded limits 413.

POST requests are not idempotent. Disable automatic analysis retries in external
workflows unless duplicate tasks are acceptable. A timed-out client does not
imply the server computation stopped. There is currently no task-list endpoint;
an operator can inspect task records in the workspace database when the client
did not receive an analysis ID.

## OpenAPI

The full schema is generated from the application at `/openapi.json` and exposed
through `/docs`. Export it with:

```bash
python scripts/export_openapi.py --output .datapilot/openapi.json
```

`docs/coze/openapi.json` deliberately describes only the flat integration facade.
It is not a replacement for the full analysis schema.
