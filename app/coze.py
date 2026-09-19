"""Small flat-response facade for external workflow/plugin consumers."""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Security
from pydantic import Field

from app.schemas import AnalysisRequest, Contract


class CozeRequest(Contract):
    dataset_id: UUID
    query: str = Field(min_length=1, max_length=2000)


class CozeResponse(Contract):
    analysis_id: UUID
    status: Literal["completed"]
    summary: str
    report_json: str


def create_coze_router(require_key) -> APIRouter:
    router = APIRouter(dependencies=[Security(require_key)], tags=["integrations"])

    @router.post("/integrations/coze/analyze", response_model=CozeResponse,
                 status_code=201, operation_id="analyzeWithDataPilot")
    def analyze(body: CozeRequest, request: Request):
        task = request.app.state.service.analyze(AnalysisRequest(**body.model_dump()))
        return {"analysis_id": task["analysis_id"], "status": task["status"],
                "summary": task["report"]["summary"],
                "report_json": json.dumps(task["report"], ensure_ascii=False, allow_nan=False)}

    return router
