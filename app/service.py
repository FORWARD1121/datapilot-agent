"""Application services shared by the API and CLI."""

import logging

from app.agent import Agent
from app.core import AppError, Settings
from app.db import Store
from app.ingestion import parse_upload
from app.llm import Provider, make_provider
from app.profiling import profile
from app.rules import RuleEngine
from app.schemas import AnalysisRequest
from app.tools import ToolRegistry


logger = logging.getLogger("datapilot")


class DataPilot:
    def __init__(self, settings: Settings, provider: Provider | None = None):
        self.settings = settings
        self.store = Store(settings)
        self.rules = RuleEngine(settings.rules_path)
        self.agent = Agent(ToolRegistry(self.rules.run), provider or make_provider(settings))

    def upload(self, filename: str, raw: bytes, drop_duplicates: bool = False) -> dict:
        parsed = parse_upload(filename, raw, self.settings, drop_duplicates)
        metadata = profile(parsed.frame)
        metadata["cleaning"] = parsed.audit
        dataset = self.store.save_dataset(parsed.frame, filename, metadata)
        return {"dataset_id": dataset.id, "filename": dataset.filename, "profile": metadata,
                "created_at": dataset.created_at.isoformat()}

    def analyze(self, request: AnalysisRequest) -> dict:
        dataset_id = str(request.dataset_id)
        frame = self.store.frame(dataset_id)
        task_id = self.store.create_task(dataset_id, request.query)
        try:
            state = self.agent.run(frame, request.query, request.intent)
            self.store.finish_task(task_id, state.plan.model_dump(mode="json"),
                [result.model_dump(mode="json") for result in state.results],
                state.report.model_dump(mode="json"), state.trace)
        except Exception as exc:
            code = exc.code if isinstance(exc, AppError) else "analysis_failed"
            self.store.fail_task(task_id, code)
            # Do not log queries, spreadsheet content, upstream bodies, credentials or exception text.
            logger.warning("analysis_failed task_id=%s code=%s", task_id, code)
            safe = exc if isinstance(exc, AppError) else AppError("analysis_failed", "Analysis could not be completed", 500)
            safe.analysis_id = task_id
            raise safe from None
        return self.store.task(task_id)

    def close(self) -> None:
        self.store.engine.dispose()
