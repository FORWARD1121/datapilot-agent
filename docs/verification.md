# Verification and final critic audit

Audit date: 2026-09-19. Scope: a runnable single-workspace portfolio backend and
offline demo, not certification of a deployed production service.

## Recovered state

Development continued from `bc634fa156cf6ebdb45d3dfd25825e6e8a81319f` on
`feat/datapilot-core`, rather than replacing the existing implementation.
That baseline had 120 passing tests. GitHub Actions run `35431752876` retained
its reproducible source and test outputs. A subsequent lint gate exposed nine
import-order findings, which were corrected instead of disabling the gate.

## Reproduced and fixed defects

| Finding | Reproduction / consequence | Fix and regression coverage |
|---|---|---|
| Int64 SUM wraparound | Summing two maximum signed int64 values produced -2 | Python integer accumulation; ratio inputs use the same safe helper |
| Floating-point overflow | A sum could become null without an explanation; a finite mean could overflow in the intermediate sum | Bounded finite output, explicit overflow reason, scaled mean calculation |
| Ambiguous numeric dates | Compact numeric calendar dates could be interpreted as nanoseconds near 1970 | Explicit YYYYMMDD inference; unlabeled numeric date units are not guessed |
| Implicit summary filtering | Assigning a date column to an untimed summary dropped rows with missing dates | Offline parser assigns date scope only where relevant |
| Coze whitespace request | Facade accepted whitespace then inner validation raised a server error | Aligned nonblank request contract; invalid input returns 422 |
| Evidence omission | Arbitrary first/last sampling missed a large decline or anomaly in the middle | Prioritize growth extremes, retained anomaly deviations and rule severity |
| Excessive JSON nesting | Deeply nested model output could exceed safe parser depth | Lexical depth bound before JSON parsing |
| Unicode numeric commentary | Full-width digits bypassed an ASCII-only restriction | Unicode digit guard; hypotheses still explicitly unverified |
| Huge Content-Length | Integer conversion of an excessively long header could raise unexpectedly | Reject invalid or oversized digit strings as a 400 client error |
| Demo environment leakage | Offline demo could inherit DATABASE_URL and write outside its demo workspace | Explicit isolated offline database/provider/security settings |
| Documentation drift | Coze plugin schema lacked the runtime nonblank query restriction | Matching schema and an automated contract regression |

`tests/test_regressions.py` contains 19 added cases, including parameterized
cases. Before the implementation fixes, the initial 18-case audit subset had
17 failures and one pass. After fixes and the schema contract test, the complete
suite passed 139 tests. The changes did not remove existing tests.

## Actually executed locally

Environment: Linux, Python 3.13.5. Runtime dependencies matched the declared
versions in the installed environment. Editable installation succeeded with
`python -m pip install --no-deps --no-build-isolation -e .`; dependency download
and fresh installation are independently exercised by GitHub Actions.

- Pytest: **139 passed**; statement coverage **1109 / 1178 = 94.14%**.
- Compilation: `python -m compileall -q app scripts tests` passed.
- AST safety scan: `python scripts/check_safety.py`, 20 application/script files,
  zero findings under the script's explicitly limited checks.
- Offline CLI: all four demo queries completed without a paid API.
- OpenAPI: full application schema exported successfully.
- Real HTTP process: Uvicorn started on loopback; health, upload, profile, trend,
  ranking, anomaly, task retrieval and report equality passed over HTTP.

The 107-row sample and its intentional duplicate are fixtures, not a measured
business dataset or performance benchmark. Coverage is statement coverage, not
proof that every behavior is correct.

## Continuous verification

The checked-in workflow runs on Python 3.11, 3.12 and 3.13 and requires pytest,
coverage of at least 80%, compilation, the safety scan, offline demo, a real HTTP
smoke run and Ruff lint. The workflow is authoritative for each commit's remote
status; a local passing result must not be substituted for an unverified CI run.

For each job it retains a source archive, tested commit, dependency versions,
JUnit XML, coverage JSON, demo output and HTTP smoke output for 14 days.
[Open workflow history](https://github.com/FORWARD1121/datapilot-agent/actions/workflows/tests.yml).
Unit/provider tests block network sockets and inject mock HTTP transports.

## Unverified or intentionally out of scope

No paid LLM endpoint was called. No Coze cloud workspace was configured or
published. No MySQL server, multi-user isolation, production load, penetration
test, data-retention policy, migration system or failure-recovery worker was
verified. There is no browser frontend. Those claims must not appear as completed
features on a resume.

The model commentary guard is not a semantic proof: numerical words and incorrect
non-numeric interpretations remain possible. Reports mark hypotheses as
unverified and expose the deterministic evidence for inspection. Ordinary
floating-point analytics are not accounting-grade decimal calculations.

## Portfolio status criterion

A public portfolio release requires the complete source and documentation to be
visible, the final commit's CI gates to pass, an executable offline demo, a
retained license and accurate disclosure of the external integrations above.
It does not imply a cloud deployment or production readiness.
