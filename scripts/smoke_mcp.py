"""用官方客户端走一遍 stdio：握手、列出 12 个工具、确认订单没有被当成资源。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    data_dir = ROOT / "target" / "smoke-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
        "WORKFLOW_DATA_DIR": str(data_dir),
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "programmer_workflow"],
        env=env,
        cwd=ROOT,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            info = init.server_info
            print("INIT", info.name, getattr(info, "title", None), init.protocol_version)
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print("TOOL_COUNT", len(names))
            print("TOOLS", ",".join(names))
            result = await session.call_tool(
                "problem_clarifier",
                {
                    "demand": "用户可以创建订单并查询订单状态。需要登录。峰值 500 QPS。仅后端。",
                    "scope": "backend-only",
                },
            )
            text = result.model_dump_json()
            print("CALL_IS_ERROR", result.is_error)
            print("CALL_HAS_ORDER", "订单" in text)
            structured = result.structured_content or {}
            artifact = structured.get("artifact") or {}
            restated = str(artifact.get("restatedProblem", ""))
            print("PRIMARY", restated[:80])
            print("LABEL_OK", "订单" in restated and "业务对象" not in restated)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(asyncio.run(main()))
