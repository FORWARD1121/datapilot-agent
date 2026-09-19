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
