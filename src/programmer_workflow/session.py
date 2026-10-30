"""一次交接环的可变状态。文件存储使用 to_document 的副本。"""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone

from programmer_workflow.errors import ErrorCode, WorkflowError
from programmer_workflow.model import AgentId, AgentResult
from programmer_workflow.policy import ABSOLUTE_STEP_CEILING, DEFAULT_MAX_STEPS, MAX_TEXT_LENGTH, REQUEST_MAX_STEPS


class WorkflowSession:
    def __init__(
        self,
        session_id: str,
        original_demand: str,
        working_demand: str,
        scope: str,
        scope_pinned: bool,
        audience_level: str,
        entry_agent_id: str,
        status: str,
        max_steps: int,
        cursor: str | None,
        created_at: str,
    ) -> None:
        self.id = session_id
        self.original_demand = original_demand
        self.working_demand = working_demand
        self.scope = scope
        self.scope_pinned = scope_pinned
        self.audience_level = audience_level
        self.entry_agent_id = entry_agent_id
        self.status = status
        self.max_steps = max_steps
        self.cursor = cursor
        self.pending_return: str | None = None
        self.fresh_assessment = False
        self.waiting_reason: str | None = None
        self.stop_reason: str | None = None
        self.visited: list[str] = []
        self.loop_edges: list[str] = []
        self.steps: list[dict] = []
        self.artifacts: dict[str, dict] = {}
        self.created_at = created_at
        self.updated_at = created_at
        self.lock = threading.RLock()

    @staticmethod
    def start(demand: str, scope: str, scope_pinned: bool, audience_level: str, entry: AgentId, max_steps: int) -> WorkflowSession:
        now = _now()
        return WorkflowSession(
            str(uuid.uuid4()),
            demand,
            demand,
            scope,
            scope_pinned,
            audience_level,
            entry.tool_name,
            "RUNNING",
            max_steps,
            entry.tool_name,
            now,
        )

    def step_count(self) -> int:
        return len(self.steps)

    def cursor_name(self) -> str:
        if not self.cursor:
            raise WorkflowError(ErrorCode.SESSION_CLOSED, "会话没有可继续的角色")
        return self.cursor

    def cursor_agent(self) -> AgentId:
        return AgentId.from_text(self.cursor_name())

    def has_step(self, agent_id) -> bool:
        return any(step.get("agentId") == _as_name(agent_id) for step in self.steps)

    def visited_agent(self, agent_id) -> bool:
        return _as_name(agent_id) in self.visited

    def add_visited(self, agent_id) -> None:
        name = _as_name(agent_id)
        if name not in self.visited:
            self.visited.append(name)
        self._touch()

    def remove_visited(self, agent_id) -> None:
        name = _as_name(agent_id)
        self.visited = [item for item in self.visited if item != name]
        self._touch()

    def loop_edge_used(self, source, target) -> bool:
        return _edge(source, target) in self.loop_edges

    def mark_loop_edge(self, source, target) -> None:
        edge = _edge(source, target)
        if edge not in self.loop_edges:
            self.loop_edges.append(edge)
        self._touch()

    def set_pending_return(self, agent_id) -> None:
        self.pending_return = _as_name(agent_id)
        self._touch()

    def consume_pending_return(self) -> str | None:
        # 返回工具名。新增技能不在内置枚举里，不能在这里解析成 AgentId。
        if not self.pending_return:
            return None
        name = self.pending_return
        self.pending_return = None
        self._touch()
        return name

    def mark_fresh_assessment(self) -> None:
        self.fresh_assessment = True
        self._touch()

    def consume_fresh_assessment(self) -> bool:
        value = self.fresh_assessment
        self.fresh_assessment = False
        if value:
            self._touch()
        return value

    def put_artifact(self, agent_id, artifact: dict) -> None:
        self.artifacts[_as_name(agent_id)] = copy.deepcopy(artifact)
        self._touch()

    def artifact_snapshot(self) -> dict[str, dict]:
        return copy.deepcopy(self.artifacts)

    def add_step(self, result: AgentResult, title: str | None = None) -> None:
        step = {
            "index": len(self.steps) + 1,
            "agentId": result.agent_id.tool_name,
            "title": title or result.agent_id.title,
            "action": result.handoff.action.value,
            "summary": result.summary,
            "reason": result.handoff.reason,
        }
        if result.handoff.target is not None:
            step["targetAgentId"] = result.handoff.target.tool_name
        self.steps.append(step)
        self._touch()

    def move_cursor(self, agent_id) -> None:
        self.cursor = _as_name(agent_id)
        self._touch()

    def update_scope(self, scope: str) -> None:
        # 调用方没有写死 scope 时，后续补充里的「仅后端」要生效。
        if not self.scope_pinned:
            self.scope = scope
            self._touch()

    def wait_for_user(self, reason: str) -> None:
        self.status = "WAITING_FOR_USER"
        self.waiting_reason = reason
        self.stop_reason = reason
        self._touch()

    def complete(self, reason: str) -> None:
        self.status = "COMPLETED"
        self.stop_reason = reason
        self.cursor = None
        self._touch()

    def stop_max_steps(self) -> None:
        self.status = "STOPPED_MAX_STEPS"
        self.stop_reason = f"已达到本轮步数上限 {self.max_steps}"
        self._touch()

    def fail(self, reason: str) -> None:
        self.status = "FAILED"
        self.stop_reason = reason
        self._touch()

    def mark_running(self) -> None:
        self.status = "RUNNING"
        self.waiting_reason = None
        self._touch()

    def append_feedback(self, feedback: str) -> None:
        combined = self.working_demand + "\n补充：" + feedback
        if len(combined) > MAX_TEXT_LENGTH * 2:
            raise WorkflowError(ErrorCode.DEMAND_REJECTED, "累积需求超过 16000 个字符")
        self.working_demand = combined
        self._touch()

    def raise_cap(self, extra_steps: int) -> None:
        if extra_steps < 1 or extra_steps > REQUEST_MAX_STEPS:
            raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "extraSteps 必须在 1 到 16 之间")
        if len(self.steps) >= ABSOLUTE_STEP_CEILING:
            raise WorkflowError(ErrorCode.STEP_LIMIT, f"会话步数已达上限 {ABSOLUTE_STEP_CEILING}")
        self.max_steps = min(ABSOLUTE_STEP_CEILING, len(self.steps) + extra_steps)
        self._touch()

    def can_resume(self) -> bool:
        return self.status in {"WAITING_FOR_USER", "STOPPED_MAX_STEPS"}

    def to_document(self) -> dict:
        return {
            "id": self.id,
            "originalDemand": self.original_demand,
            "workingDemand": self.working_demand,
            "scope": self.scope,
            "scopePinned": self.scope_pinned,
            "audienceLevel": self.audience_level,
            "entryAgentId": self.entry_agent_id,
            "status": self.status,
            "maxSteps": self.max_steps,
            "cursor": self.cursor,
            "pendingReturn": self.pending_return,
            "freshAssessment": self.fresh_assessment,
            "waitingReason": self.waiting_reason,
            "stopReason": self.stop_reason,
            "visited": list(self.visited),
            "loopEdges": list(self.loop_edges),
            "steps": copy.deepcopy(self.steps),
            "artifacts": self.artifact_snapshot(),
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @staticmethod
    def restore(document: dict) -> WorkflowSession:
        session = WorkflowSession(
            str(document.get("id") or ""),
            str(document.get("originalDemand") or ""),
            str(document.get("workingDemand") or ""),
            str(document.get("scope") or "full"),
            bool(document.get("scopePinned")),
            str(document.get("audienceLevel") or "intermediate"),
            str(document.get("entryAgentId") or ""),
            str(document.get("status") or "FAILED"),
            int(document.get("maxSteps") or DEFAULT_MAX_STEPS),
            _text_or_none(document.get("cursor")),
            str(document.get("createdAt") or _now()),
        )
        session.pending_return = _text_or_none(document.get("pendingReturn"))
        session.fresh_assessment = bool(document.get("freshAssessment"))
        session.waiting_reason = _text_or_none(document.get("waitingReason"))
        session.stop_reason = _text_or_none(document.get("stopReason"))
        session.updated_at = str(document.get("updatedAt") or session.created_at)
        session.visited = [str(item) for item in document.get("visited") or []]
        session.loop_edges = [str(item) for item in document.get("loopEdges") or []]
        session.steps = copy.deepcopy(list(document.get("steps") or []))
        artifacts = document.get("artifacts") or {}
        if isinstance(artifacts, dict):
            session.artifacts = copy.deepcopy(artifacts)
        return session

    def _touch(self) -> None:
        self.updated_at = _now()


def _as_name(agent_id) -> str:
    if isinstance(agent_id, str):
        return agent_id
    return agent_id.tool_name


def _edge(source, target) -> str:
    return f"{_as_name(source)}->{_as_name(target)}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text.strip() or text == "null":
        return None
    return text
