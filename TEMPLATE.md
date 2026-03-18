# MCP Agent Pipeline — Framework Template

A scaffold for building multi-agent pipelines using the Model Context Protocol (MCP) over SSE transport. Each agent runs as a Docker container exposing MCP tools; a coordinator drives them through configurable pipeline stages.

## Prerequisites

- Docker + Docker Compose v2 (`docker compose`, not `docker-compose`)
- Python 3.11+
- [Ollama](https://ollama.ai) running locally (for the default LLM backend)

## Quick Start

### 1. Clone or copy this template

```bash
git clone https://github.com/EdibleTuber/mcp-agent-pipeline my-project
cd my-project
```

### 2. Edit `pipeline.yml`

A starter `pipeline.yml` is included in the repo root — edit it in place to define your stages and agents. Each stage lists which agents run (sequentially or in parallel):

```yaml
stages:
  - name: ingest
    agents: [my_processor]
    parallel: false
  - name: analyze
    agents: [agent_a, agent_b]
    parallel: true

agents:
  my_processor:
    url: "http://my_processor:8080/sse"
  agent_a:
    url: "http://agent_a:8080/sse"
  agent_b:
    url: "http://agent_b:8080/sse"
```

### 3. Write your agents

Each agent is a Python module with a `server.py` entry point:

```python
# src/my_processor/server.py
import anyio
from framework.base.base_agent import create_agent_server, call_llm
from pydantic import BaseModel

server = create_agent_server("my_processor")

class MyResult(BaseModel):
    summary: str

@server.tool()
async def process(job_id: str, shared_volume: str) -> str:
    """Process a job and return JSON results."""
    return await anyio.to_thread.run_sync(_process_impl, job_id, shared_volume)

def _process_impl(job_id: str, shared_volume: str) -> str:
    result = call_llm(
        prompt=f"Analyze job {job_id}",
        output_schema=MyResult,
        system_prompt="You are a helpful analyst.",
    )
    return result.model_dump_json()

if __name__ == "__main__":
    server.run(transport="sse")
```

> **Important:** All tools that call the LLM or do blocking I/O must be `async def` and use
> `anyio.to_thread.run_sync`. FastMCP runs sync tools directly on the event loop, which blocks
> SSE keepalives and causes timeouts on slow hardware.

### 4. Add agent services to `docker-compose.yml`

Copy the `agent_alpha` block and rename it for each agent:

```yaml
  my_processor:
    build:
      context: .
      dockerfile: Dockerfile.agent
      args:
        AGENT_MODULE: my_processor
    ports:
      - "9001:8080"
    volumes:
      - shared_data:/work
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - OLLAMA_HOST=${OLLAMA_HOST:-http://host.docker.internal:11434}
      - MODEL_NAME=${MODEL_NAME:-qwen2.5-coder:7b}
```

### 5. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set OLLAMA_HOST if Ollama isn't on localhost
# See the "LLM backend" table below for all supported variables
```

### 6. Build and run

```bash
docker compose up --build
```

### 7. Submit a job

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{"input_path": "/work/shared/myfile.txt"}'
# Returns: {"job_id": "...", "state": "pending", ...}
```

### 8. Poll for status

```bash
curl http://localhost:8000/jobs/{job_id}
# Returns: {"job_id": "...", "state": "running"|"completed"|"failed", "current_stage": "..."}
```

---

## Advanced Usage

### Custom argument dispatch

By default, the coordinator calls each agent's first tool with `job_id` and `shared_volume` as arguments. If an agent needs different arguments, subclass `Pipeline` and override `_execute_agent_tools`:

```python
from framework.coordinator.pipeline import Pipeline
from framework.schemas import JobRequest
from mcp import ClientSession

class MyPipeline(Pipeline):
    async def _execute_agent_tools(
        self, session: ClientSession, agent_name: str,
        tool_names: list[str], job: JobRequest, stage_name: str = ""
    ) -> dict:
        if agent_name == "special_agent":
            result = await session.call_tool("special_tool", arguments={
                "input_path": job.input_path,
                "extra_param": "value",
            })
            ...
        return await super()._execute_agent_tools(
            session, agent_name, tool_names, job, stage_name
        )
```

### Post-stage hooks

To process one stage's output before the next stage runs (e.g., digest a scan report into snippet files), override `on_stage_complete`:

```python
class MyPipeline(Pipeline):
    async def on_stage_complete(self, stage_name: str, job: JobRequest) -> None:
        if stage_name == "scan":
            self._digest_scan_output(job)
```

### LLM backend

The `call_llm()` function dispatches to the backend configured by the `LLM_BACKEND` env var:

| `LLM_BACKEND` | Status | Config |
|---|---|---|
| `ollama` (default) | Implemented | `OLLAMA_HOST`, `MODEL_NAME`, `OLLAMA_TIMEOUT` |
| `claude` | Stub (not yet implemented) | `ANTHROPIC_API_KEY` |

---

## Shared Volume Layout

All agents share a Docker volume mounted at `/work`. The coordinator writes job state here; agents read inputs and write findings.

```
/work/shared/
  findings/
    {job_id}/
      status.json          # written by coordinator (pending → running → completed/failed)
      {agent_name}.json    # written by each agent after it runs
```

Agents should write their results as JSON to the `shared_volume/findings/{job_id}/` directory.
