from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from framework.coordinator.api import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    shared_volume = os.environ.get("SHARED_VOLUME", "/work/shared")
    config_path = Path("/app/pipeline.yml")
    port = int(os.environ.get("COORDINATOR_PORT", "8000"))
    app = create_app(shared_volume=shared_volume, config_path=config_path)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
