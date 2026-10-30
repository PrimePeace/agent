"""把会话文档整理成工具返回值。叙事在前，完整产物在后。"""

from __future__ import annotations

from programmer_workflow.model import AgentId, AgentResult, HandoffAction


def from_document(document: dict) -> dict:
    steps = list(document.get("steps") or [])
    return {
        "sessionId": document.get("id"),
        "status": document.get("status"),
        "entryAgentId": document.get("entryAgentId"),
        "scope": document.get("scope"),
        "audienceLevel": document.get("audienceLevel"),
        "demand": document.get("workingDemand"),
        "stopReason": document.get("stopReason"),
        "waitingReason": document.get("waitingReason"),
        "cursor": document.get("cursor"),
        "maxSteps": document.get("maxSteps"),
        "narrative": _narrative(steps),
        "steps": steps,
        "artifacts": document.get("artifacts"),
        "nextAction": _next_action(document),
        "createdAt": document.get("createdAt"),
        "updatedAt": document.get("updatedAt"),
    }


def single(result: AgentResult, successor=None) -> dict:
    handoff = {
        "action": result.handoff.action.value,
        "reason": result.handoff.reason,
    }
    if result.handoff.action in {HandoffAction.NEXT, HandoffAction.SKIP}:
        nxt = successor
        if nxt is None and hasattr(result.agent_id, "next"):
            nxt = result.agent_id.next()
        if nxt is not None:
            handoff["suggestedNextAgentId"] = nxt.tool_name
            handoff["suggestedNextTitle"] = nxt.title
    if result.handoff.target is not None:
        handoff["targetAgentId"] = result.handoff.target.tool_name
    return {
        "mode": "single",
        "agentId": result.agent_id.tool_name,
        "title": result.agent_id.title,
        "summary": result.summary,
        "artifact": result.artifact,
        "handoff": handoff,
        "nextAction": "单独调用不会自动进入下一位。若要沿环交接，把本结果放进下一位工具的 context，或改用 run_workflow。",
    }


def catalog() -> list[dict]:
    agents = []
    for order, agent_id in enumerate(AgentId, start=1):
        agents.append(
            {
                "order": order,
                "agentId": agent_id.tool_name,
                "title": agent_id.title,
                "duty": agent_id.duty,
                "output": agent_id.output_name,
                "tool": agent_id.tool_name,
                "nextAgentId": agent_id.next().tool_name,
            }
        )
    return agents


def _narrative(steps: list[dict]) -> str:
    lines = [f"{step.get('index')}. {step.get('title')}（{step.get('action')}）：{step.get('summary')}" for step in steps]
    return "\n".join(lines)


def _next_action(document: dict) -> str:
    status = str(document.get("status") or "")
    session_id = str(document.get("id") or "")
    if status == "WAITING_FOR_USER":
        return f"调用 resume_workflow，sessionId={session_id}，用 feedback 补充用户、核心动作和成功标准。"
    if status == "STOPPED_MAX_STEPS":
        return f"调用 resume_workflow，sessionId={session_id}，可省略 feedback，环会从 cursor 继续。"
    if status == "COMPLETED":
        return "本轮交接环已结束。按 artifacts 和 skillKnowledge 实现代码；需要某一角色重做时单独调用对应工具。"
    if status == "FAILED":
        return "会话已失败。查看 stopReason，修正输入后重新 run_workflow。"
    return "会话仍在运行。"
