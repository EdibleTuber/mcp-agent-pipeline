from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException

from framework.coordinator.pipeline import Pipeline
from framework.schemas import JobRequest, JobStatus

logger = logging.getLogger(__name__)


def create_app(
    shared_volume: str = "/work/shared",
    agent_urls: dict[str, str] | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="MCP Agent Pipeline")
    volume = Path(shared_volume)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/jobs", status_code=202)
    def submit_job(request: JobRequest, background_tasks: BackgroundTasks):
        status = JobStatus(job_id=request.job_id, state="pending")

        # Write initial status
        job_dir = volume / "findings" / request.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "status.json").write_text(status.model_dump_json(indent=2))

        # Run pipeline in background
        pipeline = Pipeline(
            shared_volume=shared_volume,
            agent_urls=agent_urls,
            config_path=config_path,
        )
        background_tasks.add_task(_run_pipeline, pipeline, request)

        return status.model_dump()

    @app.get("/jobs/{job_id}")
    def get_job_status(job_id: str):
        status_file = volume / "findings" / job_id / "status.json"
        if not status_file.exists():
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            return json.loads(status_file.read_text())
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Status file corrupted")

    return app


async def _run_pipeline(pipeline: Pipeline, job: JobRequest) -> None:
    try:
        logger.info("Pipeline started for job %s", job.job_id)
        await pipeline.run(job)
        logger.info("Pipeline completed for job %s", job.job_id)
    except Exception:
        logger.exception("Pipeline failed for job %s", job.job_id)
        status = JobStatus(job_id=job.job_id, state="failed")
        status_file = Path(pipeline.shared_volume) / "findings" / job.job_id / "status.json"
        status_file.write_text(status.model_dump_json(indent=2))
