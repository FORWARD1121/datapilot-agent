# Coze integration / 扣子工作流接入

Status: backend facade and local contract tests implemented. **No Coze bot has
been published or tested in a cloud account.** This repository does not contain
Coze credentials. Platform UI labels and permissions may differ by edition.

## Prepare the backend

Run DataPilot behind an HTTPS reverse proxy on your own reachable domain.
Set `APP_ENV=public`, a randomly generated `API_TOKEN` of at least 32 characters,
and persist `DATA_DIR`. Keep the upstream server private; enforce proxy request,
concurrency and rate limits. Never expose local unauthenticated mode.
A cloud workflow cannot reach your laptop's loopback address.

Upload `data/samples/sample_sales.csv` through `/docs` or `POST /datasets/upload`
and retain the returned `dataset_id`. Version one passes this ID into Coze; it
does not download arbitrary attachment URLs or ingest Coze files automatically.
The token grants access to the entire single workspace, not one user's dataset.

## Build the workflow

The following is our wiring design, not an exported Coze workflow. The companion
`workflow.blueprint.json` intentionally cannot be imported as a native workflow.

| Node | Configure | Output / next node |
|---|---|---|
| Start | String `dataset_id`; string `query` | Validate inputs |
| Validate | Require both values; query length at most 2000 | Valid: Profile; invalid: Error End |
| Profile HTTP | GET `{base_url}/datasets/{dataset_id}/profile`; header `X-API-Key` from a secret | Body, status code, response headers |
| Profile condition | Status 200 and `body.profile.rows > 0` | Analyze; otherwise Error End |
| Analyze HTTP / plugin | POST `{base_url}/integrations/coze/analyze`; JSON body defined below | Body, status code, headers |
| Analysis condition | Status 201 and `body.status == completed` | Parse report; otherwise Error End |
| JSON deserialization | Parse `body.report_json` | Structured report |
| Success End | Return `analysis_id`, `summary`, `report_json` | Final answer |
| Error End | Return a safe message, error code, request ID and analysis ID when present | No success report |

In each HTTP node, set custom authentication: key `X-API-Key`, value your
DataPilot token, location Header. Do not use a Coze personal access token here:
this is authentication to DataPilot, not to the Coze API. Do not put secrets in
Start inputs, prompts, URLs, logs, screenshots, or the checked-in schema.

Choose JSON as the analysis request body. Bind the Start-node variables using
the editor's variable picker; serialize strings as JSON rather than concatenating
raw user input. A literal request used for local testing looks like:

```json
{"dataset_id":"00000000-0000-0000-0000-000000000000","query":"compare sales by region"}
```

Replace the example UUID with a real upload response. The endpoint is synchronous:
success is HTTP 201, not 202. Set the analysis HTTP timeout to 300 seconds and its
retry count to zero. Retrying a POST creates a second analysis; there is no
idempotency-key API. On a timeout, check server task history before resubmitting.
Use mock mode for the initial cloud wiring test; real models can take longer.

Use the backend report directly. A separate LLM formatter is optional, not needed
for calculations. Its prompt is `formatter_prompt.txt`; it must retain evidence,
uncertainty and provider provenance. Do not feed original spreadsheet rows to it.

## Plugin import alternative

`openapi.json` is a small OpenAPI 3.0.3 description of the flat facade, avoiding
nested union schemas in the full API. Replace its example `servers[0].url` with
your HTTPS origin. In the Coze edition that exposes OpenAPI plugin import, import
this description, configure the custom header secret, and test the operation
`analyzeWithDataPilot`. Import acceptance still requires verification in your
account. Where that UI is unavailable, use the HTTP-node configuration above.

The full application schema is served at `/openapi.json`; generate a local copy:

```bash
python scripts/export_openapi.py --output /tmp/datapilot-openapi.json
```

The minimal plugin schema intentionally describes only `dataset_id` and `query`.
For explicit filters, time ranges or custom metrics, use the full `/analysis` API
with its validated `intent` object through an HTTP node.

## Error handling and acceptance

401: fix the DataPilot token. 404: upload again or use the correct workspace ID.
413: reduce the request. 422: correct the inputs or use explicit intent. 500/503:
show only the safe backend error; inspect the server separately. A model failure
may still return 201 with deterministic fallback: inspect report `provenance`.
Disable any node option that masks failures with a successful default response.

Before claiming a deployed integration, manually verify: upload and successful
analysis; nonexistent dataset; wrong token; an unsupported offline request;
missing real-model configuration and its disclosed fallback; report equality
against `GET /analysis/{analysis_id}/report`. Record workspace, workflow version,
test date and redacted outputs. None of these cloud checks are claimed here.

## Official platform reference

Coze's [HTTP request node documentation](https://docs.coze.cn/guides_http_node)
was consulted on 2026-09-19 for request methods, custom header authentication,
JSON bodies, returned status/body/headers and configurable timeouts/retries.
This document's endpoints, variables and workflow topology are DataPilot's own
integration design; they are not a claim of an official Coze template.
