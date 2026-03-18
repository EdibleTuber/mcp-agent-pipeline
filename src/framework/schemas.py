from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- Job lifecycle ---

class JobRequest(BaseModel):
    input_path: str
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class JobStatus(BaseModel):
    job_id: str
    state: Literal["pending", "running", "completed", "failed"] = "pending"
    current_stage: str | None = None
    results: dict[str, str] | None = None  # agent_name -> findings file path


# --- Generic agent result wrapper ---

class AgentResult(BaseModel):
    agent_name: str
    job_id: str
    status: str  # "success", "error"
    error: str | None = None
    findings: dict[str, Any] = Field(default_factory=dict)
