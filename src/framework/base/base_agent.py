from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar

import anyio
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def call_llm(
    prompt: str,
    output_schema: type[T],
    system_prompt: str = "",
    **kwargs,
) -> T:
    """Call the configured LLM backend and return a validated Pydantic model.

    Backend is selected via the LLM_BACKEND env var (default: "ollama").
    Supported values: "ollama", "claude" (stub, not yet implemented).

    Env vars consumed:
        LLM_BACKEND       — "ollama" (default) or "claude"
        OLLAMA_HOST       — Ollama server URL (default: http://localhost:11434)
        MODEL_NAME        — model to use (default: qwen2.5-coder:7b)
        OLLAMA_TIMEOUT    — per-call timeout in seconds (default: 600)
        ANTHROPIC_API_KEY — required for claude backend (not yet implemented)

    Kwargs are forwarded to the backend implementation.
    """
    backend = os.environ.get("LLM_BACKEND", "ollama")
    if backend == "ollama":
        return _call_ollama(prompt, output_schema, system_prompt=system_prompt, **kwargs)
    elif backend == "claude":
        return _call_claude(prompt, output_schema, system_prompt=system_prompt, **kwargs)
    else:
        raise ValueError(
            f"Unknown LLM_BACKEND: {backend!r}. Supported values: 'ollama', 'claude'"
        )


def _call_ollama(
    prompt: str,
    output_schema: type[T],
    system_prompt: str = "",
    ollama_host: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> T:
    """Ollama backend — fully implemented."""
    import ollama

    host = ollama_host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    mdl = model or os.environ.get("MODEL_NAME", "qwen2.5-coder:7b")
    to = timeout if timeout is not None else float(os.environ.get("OLLAMA_TIMEOUT", "600"))

    client = ollama.Client(host=host, timeout=to)
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = client.chat(
        model=mdl,
        messages=messages,
        format=output_schema.model_json_schema(),
    )
    return output_schema.model_validate_json(response.message.content)


def _call_claude(
    prompt: str,
    output_schema: type[T],
    system_prompt: str = "",
    **kwargs,
) -> T:
    """Claude API backend — stub, not yet implemented."""
    raise NotImplementedError(
        "Claude backend is not yet implemented. "
        "Set LLM_BACKEND=ollama or implement _call_claude."
    )


def create_agent_server(name: str, host: str = "0.0.0.0", port: int = 8080) -> FastMCP:
    """Create a FastMCP server with a default read_file tool.

    All blocking tool implementations should use:
        async def my_tool(...) -> str:
            return await anyio.to_thread.run_sync(_my_tool_impl, ...)
    to avoid blocking the asyncio event loop (which would starve SSE keepalives).
    """
    server = FastMCP(name, host=host, port=port)

    @server.tool()
    async def read_file(file_path: str, start_line: int = 0, max_lines: int = 200) -> str:
        """Read a file from the shared volume.

        Args:
            file_path: Path to the file (relative to /work or absolute).
            start_line: Line number to start reading from (0-indexed).
            max_lines: Maximum number of lines to return.
        """
        def _read(fp: str, sl: int, ml: int) -> str:
            path = Path(fp)
            if not path.is_absolute():
                path = Path("/work") / path
            if not path.exists():
                return f"Error: file not found: {path}"
            lines = path.read_text().splitlines()
            selected = lines[sl : sl + ml]
            return "\n".join(selected)

        return await anyio.to_thread.run_sync(_read, file_path, start_line, max_lines)

    return server
