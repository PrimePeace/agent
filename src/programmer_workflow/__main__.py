"""stdio 入口。标准输出只写 MCP 帧，日志走标准错误。"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from programmer_workflow.engine import WorkflowEngine
from programmer_workflow.server import create_server
from programmer_workflow.store import WorkflowStore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    directory = data_directory()
    engine = WorkflowEngine(WorkflowStore(directory))
    logging.getLogger(__name__).info("programmer-workflow MCP server ready on stdio, dataDirectory=%s", directory)
    create_server(engine).run(transport="stdio")


def data_directory() -> Path:
    configured = os.environ.get("WORKFLOW_DATA_DIR", "").strip()
    if not configured:
        return Path("data") / "workflows"
    return Path(configured)


if __name__ == "__main__":
    main()
