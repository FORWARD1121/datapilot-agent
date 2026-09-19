# Architecture and repository audit

## Initial state

Audited default branch `main`, commit `86da2393be32191374d9e9beb6522f1e0279f4ec`.
The repository contained only README.md, a Python .gitignore and MIT LICENSE.
There was no existing application, dependency manifest, database, or test suite.
The original LICENSE is retained byte-for-byte. The ignore file is reduced to
project-relevant exclusions, including local datasets, databases and secrets.
Development uses `feat/datapilot-core`; no force push or history rewrite.

## Design decisions

One synchronous service for bounded, small/medium tabular datasets; no queue,
RAG, vector store, arbitrary SQL or generated-code execution. Python computes
all statistics. JSON-configured rules supply business thresholds. A bounded
workflow validates intent, plan, tool arguments, tool results and reports.
Mock mode is an explicitly labelled deterministic offline provider, not an LLM.

Stored source data remains local. SQLAlchemy stores dataset metadata and task
artifacts, not entire spreadsheets. Database and file storage are injected per
application instance. A shared API token protects a single workspace in public
mode; this is not a multi-tenant system.

Implementation stages follow ingestion, tools, rules, providers, orchestration,
API, demo, Coze documentation, tests, critic audit and final documentation.

## Module boundaries

`ingestion.py` owns safe table parsing and cleaning audit; `profiling.py` owns
quality statistics. `tools.py` exposes a registry with Pydantic arguments and
structured results. `rules.py` evaluates packaged/operator-supplied JSON rules
through fixed operators, never generated expressions. The core tools have no
model or network dependency.

`agent.py` records explicit state and stage provenance. `llm.py` implements the
provider protocol, deterministic offline intent/plan selection, bounded HTTP
responses, structured JSON repair and function-call planning. Prompts live in
`app/prompts`, not in controllers. `reports.py` creates evidence IDs, validates
citations and renders numeric facts from Python results. Free model commentary
is not an authoritative numerical source.

`service.py` coordinates persistence and the workflow. `db.py` owns SQLAlchemy
sessions and UUID-based dataset storage. `main.py` exposes the application
factory and typed API responses; `middleware.py` authenticates and bounds actual
body bytes before multipart processing. `coze.py` provides a deliberately small
facade for external workflow tools. No business state is initialized at module
import; each application/service instance has its own configured storage.

## Execution contract

An explicit intent bypasses language parsing. Otherwise a provider produces an
intent, which is checked against the actual dataset. The planner may select only
registered tools permitted for that intent; arguments must preserve its scope.
Duplicate calls and plans omitting the requested task are rejected. Invalid model
output is repaired once or falls back with the reason recorded. Fallback is not
silently described as successful model reasoning.

Tool outputs are JSON-validated before persistence and before becoming evidence.
Each tool can retain more records than the model context budget permits. Evidence
is bounded to 30 selected records per tool and the final model context is bounded
by serialized size. Raw spreadsheet rows are not sent to the model. Aggregated
labels and findings can still contain business-sensitive information; a configured
external provider receives that bounded context and the user's query.

## Deliberate tradeoffs

One synchronous process and local controlled files keep the project inspectable.
There is no job queue, arbitrary-code sandbox, autonomous web fetcher, RAG system
or distributed state. The price is limited concurrency and no automatic recovery
of a task left running after process termination. SQLAlchemy makes a future
backend change possible, but does not establish verified MySQL compatibility.

Rule thresholds are business configuration, not learned causal relationships.
Rules can be skipped when required facts are unavailable. Growth requires
calendar-aligned observations inside the scope, and missing data is not filled
with invented zeros. The offline parser is a demo/fallback interface; typed
intent is the reproducible interface for exact analytical questions.

Security is scoped to an operator-controlled single workspace. A shared token
is not per-user authorization. Authentication, upload/parser controls, safe errors
and tool allowlisting address specific boundaries; deployment still requires
HTTPS, reverse-proxy limits, controlled storage access and data-lifecycle policy.
