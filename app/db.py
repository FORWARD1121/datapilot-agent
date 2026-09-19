"""SQLAlchemy metadata and controlled, canonical on-disk dataset storage."""

from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd
from sqlalchemy import JSON, DateTime, ForeignKey, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.core import AppError, Settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(128))
    profile: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    query: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(20), default="running")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    task_id: Mapped[str] = mapped_column(ForeignKey("analysis_tasks.id"), primary_key=True)
    tool_results: Mapped[list] = mapped_column(JSON)
    report: Mapped[dict] = mapped_column(JSON)
    trace: Mapped[list] = mapped_column(JSON)


class Store:
    def __init__(self, settings: Settings):
        self.root = settings.data_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "datasets").mkdir(exist_ok=True)
        options = {"check_same_thread": False, "timeout": 15} if settings.db_url.startswith("sqlite") else {}
        self.engine = create_engine(settings.db_url, connect_args=options)
        if settings.db_url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def sqlite_setup(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def dataset(self, dataset_id: str) -> Dataset:
        with self.sessions() as session:
            record = session.get(Dataset, dataset_id)
            if record is None:
                raise AppError("dataset_not_found", "Dataset not found", 404)
            return record

    def save_dataset(self, frame: pd.DataFrame, filename: str, profile: dict) -> Dataset:
        record = Dataset(id=str(uuid4()), filename=filename, profile=profile)
        target = self.root / "datasets" / f"{record.id}.json"
        temporary = target.with_suffix(".tmp")
        try:
            frame.to_json(temporary, orient="table", date_format="iso", index=False, double_precision=15)
            temporary.replace(target)
            with self.sessions.begin() as session:
                session.add(record)
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise AppError("storage_failure", "Dataset could not be stored", 503) from None
        return record

    def frame(self, dataset_id: str) -> pd.DataFrame:
        record = self.dataset(dataset_id)
        # The path comes from a database record created by this service, never a supplied filename.
        path = self.root / "datasets" / f"{record.id}.json"
        try:
            return pd.read_json(path, orient="table")
        except Exception:
            raise AppError("dataset_unavailable", "Stored dataset is unavailable", 503) from None

    def create_task(self, dataset_id: str, query: str) -> str:
        self.dataset(dataset_id)
        task_id = str(uuid4())
        with self.sessions.begin() as session:
            session.add(AnalysisTask(id=task_id, dataset_id=dataset_id, query=query))
        return task_id

    def finish_task(self, task_id: str, plan: dict, results: list, report: dict, trace: list) -> None:
        with self.sessions.begin() as session:
            task = session.get(AnalysisTask, task_id)
            task.status, task.plan = "completed", plan
            session.add(AnalysisResult(task_id=task_id, tool_results=results, report=report, trace=trace))

    def fail_task(self, task_id: str, code: str) -> None:
        with self.sessions.begin() as session:
            task = session.get(AnalysisTask, task_id)
            task.status, task.error_code = "failed", code

    def task(self, task_id: str) -> dict:
        with self.sessions() as session:
            task = session.get(AnalysisTask, task_id)
            if task is None:
                raise AppError("analysis_not_found", "Analysis not found", 404)
            result = session.get(AnalysisResult, task_id)
            return {"analysis_id": task.id, "dataset_id": task.dataset_id, "query": task.query,
                    "status": task.status, "plan": task.plan, "error_code": task.error_code,
                    "created_at": task.created_at.isoformat(),
                    "tool_results": result.tool_results if result else [],
                    "report": result.report if result else None,
                    "trace": result.trace if result else []}
