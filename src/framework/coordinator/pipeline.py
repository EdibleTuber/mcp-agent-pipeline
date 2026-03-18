from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from mcp import ClientSession
from mcp.client.sse import sse_client

from framework.schemas import JobRequest, JobStatus

logger = logging.getLogger(__name__)


@dataclass
class PipelineStage:
    name: str
    agents: list[str]
    parallel: bool = False


def _load_pipeline_config(
    config_path: str | Path,
) -> tuple[list[PipelineStage], dict[str, str], dict]:
    """Load stages, agent URLs, and settings from pipeline.yml."""
    data = yaml.safe_load(Path(config_path).read_text())
    stages = [
        PipelineStage(
            name=s["name"],
            agents=s["agents"],
            parallel=s.get("parallel", False),
        )
        for s in data.get("stages", [])
    ]
    agents_cfg = data.get("agents", {})
    agent_urls = {name: cfg["url"] for name, cfg in agents_cfg.items()}
    settings = data.get("settings", {})
    return stages, agent_urls, settings


class Pipeline:
    def __init__(
        self,
        shared_volume: str = "/work/shared",
        agent_urls: dict[str, str] | None = None,
        config_path: str | Path | None = None,
    ):
        self.shared_volume = Path(shared_volume)
        if config_path is not None:
            self.stages, loaded_urls, settings = _load_pipeline_config(config_path)
            self.agent_urls = agent_urls if agent_urls is not None else loaded_urls
            if "shared_volume" in settings:
                self.shared_volume = Path(settings["shared_volume"])
        else:
            self.stages = []
            self.agent_urls = agent_urls or {}

    async def run(self, job: JobRequest) -> JobStatus:
        job_dir = self.shared_volume / "findings" / job.job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        status = JobStatus(job_id=job.job_id, state="running")

        for stage in self.stages:
            status.current_stage = stage.name
            logger.info("Starting stage: %s", stage.name)
            self._write_status(job_dir, status)
            await self._run_stage(stage, job)
            await self.on_stage_complete(stage.name, job)
            logger.info("Completed stage: %s", stage.name)

        status.state = "completed"
        status.current_stage = None
        self._write_status(job_dir, status)
        return status

    async def _run_stage(self, stage: PipelineStage, job: JobRequest) -> None:
        if stage.parallel:
            await asyncio.gather(
                *[self._call_agent(agent, job, stage.name) for agent in stage.agents]
            )
        else:
            for agent in stage.agents:
                await self._call_agent(agent, job, stage.name)

    async def _call_agent(self, agent_name: str, job: JobRequest, stage_name: str = "") -> None:
        url = self.agent_urls.get(agent_name)
        if not url:
            return

        findings_dir = self.shared_volume / "findings" / job.job_id
        findings_dir.mkdir(parents=True, exist_ok=True)
        findings_file = findings_dir / f"{agent_name}.json"

        logger.info("Calling agent: %s (stage: %s)", agent_name, stage_name)
        try:
            async with sse_client(url, sse_read_timeout=60 * 60) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    tools = await session.list_tools()
                    tool_names = [t.name for t in tools.tools]

                    result = await self._execute_agent_tools(
                        session, agent_name, tool_names, job, stage_name=stage_name
                    )

                    findings_file.write_text(
                        json.dumps(result, indent=2) if isinstance(result, dict) else str(result)
                    )
            logger.info("Agent %s completed successfully", agent_name)
        except Exception as e:
            logger.exception("Agent %s failed", agent_name)
            error_result = {"status": "error", "agent": agent_name, "error": str(e)}
            findings_file.write_text(json.dumps(error_result, indent=2))

    async def _execute_agent_tools(
        self,
        session: ClientSession,
        agent_name: str,
        tool_names: list[str],
        job: JobRequest,
        stage_name: str = "",
    ) -> dict:
        """Generic dispatcher: call the first non-read_file tool the agent exposes,
        passing job_id and shared_volume as standard arguments.

        Override this method in a subclass to implement custom argument logic
        for agents that require domain-specific parameters.
        """
        dispatch_tool = next(
            (t for t in tool_names if t != "read_file"),
            None,
        )
        if dispatch_tool is None:
            return {"status": "completed", "agent": agent_name}
        result = await session.call_tool(
            dispatch_tool,
            arguments={"job_id": job.job_id, "shared_volume": str(self.shared_volume)},
        )
        result_text = result.content[0].text if result.content else str(result)
        try:
            return json.loads(result_text)
        except json.JSONDecodeError:
            return {"raw_output": result_text}

    async def on_stage_complete(self, stage_name: str, job: JobRequest) -> None:
        """Override in a subclass to run post-stage logic (e.g., digest one agent's
        output to feed the next stage). Default is a no-op."""
        pass

    def _write_status(self, job_dir: Path, status: JobStatus) -> None:
        status_file = job_dir / "status.json"
        status_file.write_text(status.model_dump_json(indent=2))
