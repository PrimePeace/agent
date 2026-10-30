"""交接环引擎。负责交给下一位，不负责某个角色内部的专业判断。

退回时先让被点名的角色重做，再回到退回者复核。同一对角色只允许退回一次。
"""

from __future__ import annotations

import logging

from programmer_workflow.agents import AgentRegistry
from programmer_workflow.errors import ErrorCode, WorkflowError
from programmer_workflow.policy import DEFAULT_MAX_STEPS, REQUEST_MAX_STEPS
from programmer_workflow.profile import optional_text, parse_profile, require_audience, require_demand, require_scope
from programmer_workflow.session import WorkflowSession
from programmer_workflow.skills_loader import SkillCatalog
from programmer_workflow.store import WorkflowStore
from programmer_workflow.views import from_document, single

logger = logging.getLogger(__name__)


class WorkflowEngine:
    def __init__(self, store: WorkflowStore, registry: AgentRegistry | None = None, catalog: SkillCatalog | None = None) -> None:
        self._store = store
        self._registry = registry or AgentRegistry()
        self._catalog = catalog or SkillCatalog(registry=self._registry)

    def run(
        self,
        demand: str | None,
        entry_agent_id: str | None = None,
        scope: str | None = None,
        audience_level: str | None = None,
        max_steps: int | None = None,
    ) -> dict:
        valid_demand = require_demand(demand)
        scope_pinned = scope is not None and bool(scope.strip())
        valid_scope = require_scope(scope, valid_demand)
        valid_audience = require_audience(audience_level, valid_demand)
        if entry_agent_id is None or not entry_agent_id.strip():
            entry = self._catalog.default_entry()
        else:
            entry = self._catalog.require(entry_agent_id)
        if not entry.ring:
            raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "该技能不在交接环上，请单独调用")
        session = WorkflowSession.start(valid_demand, valid_scope, scope_pinned, valid_audience, entry, _max_steps(max_steps))
        self._store.insert(session)
        self._drive(session)
        return self._present(session)

    def resume(self, session_id: str | None, feedback: str | None = None, extra_steps: int | None = None) -> dict:
        extra = DEFAULT_MAX_STEPS if extra_steps is None else extra_steps
        if extra < 1 or extra > REQUEST_MAX_STEPS:
            raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "extraSteps 必须在 1 到 16 之间")
        addition = optional_text(feedback, "feedback")
        session = self._store.require(session_id)
        with session.lock:
            if not session.can_resume():
                raise WorkflowError(ErrorCode.SESSION_CLOSED, f"会话状态是 {session.status}，不能继续")
            if session.step_count() >= 32:
                from programmer_workflow.policy import ABSOLUTE_STEP_CEILING

                raise WorkflowError(ErrorCode.STEP_LIMIT, f"会话步数已达上限 {ABSOLUTE_STEP_CEILING}")
            waiting = session.status == "WAITING_FOR_USER"
            if waiting and not addition:
                raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "会话正在等待补充，feedback 不能为空")
            if addition:
                session.append_feedback(addition)
            if waiting and session.cursor:
                session.mark_fresh_assessment()
                session.remove_visited(session.cursor)
            session.raise_cap(extra)
            session.mark_running()
        self._drive(session)
        return self._present(session)

    def get(self, session_id: str | None) -> dict:
        return self._present(self._store.require(session_id))

    def invoke(
        self,
        agent_id: str | None,
        demand: str | None,
        context: str | None = None,
        scope: str | None = None,
        audience_level: str | None = None,
    ) -> dict:
        valid_demand = require_demand(demand)
        valid_scope = require_scope(scope, valid_demand)
        valid_audience = require_audience(audience_level, valid_demand)
        note = optional_text(context, "context")
        skill = self._catalog.require(agent_id)
        result = self._catalog.execute(skill.name, _single_context(valid_demand, valid_scope, valid_audience, note))
        logger.info("single agent %s action %s", skill.name, result.handoff.action.value)
        view = single(result, self._catalog.next_of(skill) if skill.ring else None)
        view["knowledge"] = skill.knowledge
        view["knowledgePath"] = str(skill.skill_file or skill.directory)
        view["source"] = skill.source
        view["skillsDirectory"] = str(self._catalog.root)
        return view

    def list_agents(self) -> dict:
        return {
            "server": "programmer-workflow",
            "title": "程序员",
            "cycle": "从入口角色开始沿环交接。下一位能看到前面各位已经写下的产物。绕回到入口即结束。同一对角色只允许退回一次，退回者会再复核。",
            "singleCall": "直接调用角色同名工具只执行那一个人，不会自动进入下一位。",
            "workflowTools": ["run_workflow", "resume_workflow", "get_workflow", "list_agents", "invoke_skill"],
            "skillsDirectory": str(self._catalog.root),
            "howToUpdate": "每个技能是 skills 目录下的一个文件夹。改 SKILL.md 后，下一次调用就会读到新内容。新增文件夹后，用 invoke_skill 或 run_workflow 立即生效；同名工具要在 Cursor 里重连 MCP 后出现。放入 agent.py 可替换该技能的判断逻辑。",
            "agents": self._catalog.public_catalog(),
        }

    def _drive(self, session: WorkflowSession) -> None:
        try:
            with session.lock:
                self._execute(session)
            self._store.save(session)
        except WorkflowError as exc:
            self._fail_quietly(session, str(exc))
            raise
        except Exception as exc:
            logger.exception("Workflow session %s failed", session.id)
            self._fail_quietly(session, "角色执行失败")
            raise WorkflowError(ErrorCode.INTERNAL, "角色执行失败", system=True) from exc

    def _execute(self, session: WorkflowSession) -> None:
        while session.status == "RUNNING" and session.step_count() < session.max_steps:
            cursor = session.cursor_name()
            if session.visited_agent(cursor):
                session.complete("已绕环一周，每个角色都已接手或明确跳过")
                break
            if not session.scope_pinned:
                session.update_scope(require_scope(None, session.working_demand))
            fresh = session.consume_fresh_assessment()
            reentry = (not fresh) and session.has_step(cursor)
            context = _workflow_context(session, reentry)
            skill = self._catalog.require(cursor)
            result = self._catalog.execute(cursor, context)
            session.add_step(result, title=skill.title)
            session.put_artifact(cursor, result.artifact)
            session.add_visited(cursor)
            logger.info("session %s agent %s action %s", session.id, cursor, result.handoff.action.value)
            action = result.handoff.action.value
            if action == "WAIT":
                session.move_cursor(cursor)
                session.wait_for_user(result.handoff.reason)
            elif action == "COMPLETE":
                session.complete(result.handoff.reason)
            elif action == "LOOP_BACK":
                self._redirect(session, cursor, result.handoff.target)
            else:
                session.move_cursor(self._advance(session, cursor))
        if session.status == "RUNNING":
            session.stop_max_steps()

    def _redirect(self, session: WorkflowSession, source: str, target) -> None:
        target_name = None if target is None else target.tool_name
        if target_name is None or session.loop_edge_used(source, target_name):
            session.move_cursor(self._catalog.next_name(source))
            return
        session.mark_loop_edge(source, target_name)
        session.remove_visited(target_name)
        session.remove_visited(source)
        session.set_pending_return(source)
        session.move_cursor(target_name)

    def _advance(self, session: WorkflowSession, current: str) -> str:
        # 被退回的角色做完后回到退回者，而不是按环跳过退回者。
        pending = session.consume_pending_return()
        if pending:
            return pending
        return self._catalog.next_name(current)

    def _present(self, session: WorkflowSession) -> dict:
        document = session.to_document()
        view = from_document(document)
        names = [str(step.get("agentId")) for step in document.get("steps") or []]
        for name in (document.get("artifacts") or {}):
            if name not in names:
                names.append(str(name))
        view["skillsDirectory"] = str(self._catalog.root)
        view["skillKnowledge"] = self._catalog.knowledge_view(names)
        return view

    def _fail_quietly(self, session: WorkflowSession, message: str) -> None:
        try:
            with session.lock:
                if session.status != "FAILED":
                    session.fail(message)
            self._store.save(session)
        except Exception:
            logger.exception("Could not persist failed session %s", session.id)


def _max_steps(max_steps: int | None) -> int:
    if max_steps is None:
        return DEFAULT_MAX_STEPS
    if max_steps < 1 or max_steps > REQUEST_MAX_STEPS:
        raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "maxSteps 必须在 1 到 16 之间")
    return max_steps


def _workflow_context(session: WorkflowSession, reentry: bool):
    from programmer_workflow.model import AgentContext

    return AgentContext(
        workflow=True,
        reentry=reentry,
        upstream_note="",
        profile=parse_profile(session.working_demand, session.scope, session.audience_level),
        artifacts=session.artifact_snapshot(),
    )


def _single_context(demand: str, scope: str, audience: str, note: str):
    from programmer_workflow.model import AgentContext

    return AgentContext(
        workflow=False,
        reentry=False,
        upstream_note=note,
        profile=parse_profile(demand, scope, audience),
        artifacts={},
    )
