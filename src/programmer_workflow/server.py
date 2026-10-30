"""把交接环暴露成一个 MCP 服务里的 12 个工具。"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from programmer_workflow.engine import WorkflowEngine
from programmer_workflow.errors import WorkflowError

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "这是一个程序员多智能体 MCP 服务。每个技能是 skills 目录里的一个文件夹，SKILL.md 是知识库，agent.py 可选。"
    "八个内置角色沿固定环交接，run_workflow 把上一任产物交给下一位，绕回入口结束。"
    "单独调用某个角色工具只做该角色的事。新增技能用 invoke_skill。需求信息不足时状态为 WAITING_FOR_USER，用 resume_workflow 补充后继续。"
    "不要把密钥、口令或生产数据写进 demand。"
)


def create_server(engine: WorkflowEngine) -> MCPServer:
    server = MCPServer(
        "programmer-workflow",
        title="程序员",
        description="八个角色组成的分层交接环，也可单独调用。",
        instructions=INSTRUCTIONS,
        version="1.0.0",
    )

    @server.tool(name="list_agents", title="角色目录", description="查看程序员团队的八个角色、职责和交接顺序。不知道该叫谁时先调用它。")
    def list_agents() -> dict[str, Any]:
        """查看八个角色的职责和交接顺序。"""
        return _call(engine.list_agents)

    @server.tool(
        name="run_workflow",
        title="运行交接环",
        description="把需求交给整个程序员团队。从 entryAgentId 开始，默认是问题梳理助手，沿环把上一任产物交给下一位，绕回入口结束。需求过短会停在 WAITING_FOR_USER。",
    )
    def run_workflow(
        demand: str,
        entryAgentId: str | None = None,
        scope: str | None = None,
        audienceLevel: str | None = None,
        maxSteps: int | None = None,
    ) -> dict[str, Any]:
        """从某一位开始跑完整交接环。"""
        return _call(engine.run, demand, entryAgentId, scope, audienceLevel, maxSteps)

    @server.tool(
        name="resume_workflow",
        title="继续交接环",
        description="继续等待补充或因步数停下的会话。等待态必须提供 feedback。不会新开一轮。",
    )
    def resume_workflow(sessionId: str, feedback: str | None = None, extraSteps: int | None = None) -> dict[str, Any]:
        """补充信息或提高步数后继续已有会话。"""
        return _call(engine.resume, sessionId, feedback, extraSteps)

    @server.tool(name="get_workflow", title="查看会话", description="读取某次交接环的状态、叙事和全部产物。")
    def get_workflow(sessionId: str) -> dict[str, Any]:
        """读取会话。"""
        return _call(engine.get, sessionId)

    @server.tool(
        name="invoke_skill",
        title="调用技能",
        description="按技能 id 单独调用。skills 目录里新增的文件夹不用重连，就可以用这个工具调用。",
    )
    def invoke_skill(
        skillId: str,
        demand: str,
        context: str | None = None,
        scope: str | None = None,
        audienceLevel: str | None = None,
    ) -> dict[str, Any]:
        """按目录名调用一个技能，不自动进入下一位。"""
        return _call(engine.invoke, skillId, demand, context, scope, audienceLevel)

    for skill in engine._catalog.load():
        _register_role(server, engine, skill.name, skill.title, skill.duty)
    return server


def _register_role(server: MCPServer, engine: WorkflowEngine, name: str, title: str, duty: str) -> None:
    description = duty + " 这是单独调用，不会自动交给下一位。若要整环协作，使用 run_workflow。知识库在该技能目录的 SKILL.md。"

    @server.tool(name=name, title=title, description=description)
    def role(
        demand: str,
        context: str | None = None,
        scope: str | None = None,
        audienceLevel: str | None = None,
    ) -> dict[str, Any]:
        """单独执行一个角色。"""
        return _call(engine.invoke, name, demand, context, scope, audienceLevel)

    role.__name__ = name


def _call(action, *args):
    try:
        return action(*args)
    except WorkflowError as exc:
        logger.warning("Tool rejected: %s %s", exc.code, exc)
        raise ToolError(f"{exc.code} {exc}") from exc
    except Exception as exc:
        logger.exception("Tool failed")
        raise ToolError("AGENT-5000 工具执行失败") from exc
