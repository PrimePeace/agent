import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from programmer_workflow.agents import AgentRegistry
from programmer_workflow.engine import WorkflowEngine
from programmer_workflow.server import create_server
from programmer_workflow.store import WorkflowStore


def test_exposes_eight_roles_and_four_workflow_tools(tmp_path: Path):
    server = create_server(_engine(tmp_path))
    tools = asyncio.run(server.list_tools())
    names = [tool.name for tool in tools]
    assert len(names) == 13
    assert "problem_clarifier" in names
    assert "run_workflow" in names
    assert "invoke_skill" in names
    assert len(set(names)) == 13


def test_invalid_demand_becomes_a_business_tool_error(tmp_path: Path):
    server = create_server(_engine(tmp_path))
    with pytest.raises(ToolError) as caught:
        asyncio.run(server.call_tool("run_workflow", {"demand": " "}))
    assert "AGENT-4221" in str(caught.value)


def test_single_role_tool_does_not_open_a_session(tmp_path: Path):
    server = create_server(_engine(tmp_path))
    result = asyncio.run(
        server.call_tool(
            "deployment_engineer",
            {"demand": "用户可以创建订单。需要登录。峰值 100 QPS。仅后端。", "scope": "backend-only"},
        )
    )
    assert result.is_error is not True
    assert result.structured_content["mode"] == "single"
    assert result.structured_content["agentId"] == "deployment_engineer"


def _engine(directory: Path) -> WorkflowEngine:
    return WorkflowEngine(WorkflowStore(directory), AgentRegistry())
