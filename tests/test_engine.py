from pathlib import Path

import pytest

from programmer_workflow.agents import AgentRegistry
from programmer_workflow.engine import WorkflowEngine
from programmer_workflow.errors import ErrorCode, WorkflowError
from programmer_workflow.model import AgentId
from programmer_workflow.policy import LOOP_BACK_BELOW
from programmer_workflow.store import WorkflowStore

RICH_BACKEND = "用户可以创建订单并查询订单状态。需要登录。峰值 500 QPS，下单接口 P99 延迟 200 毫秒以内，订单数据强一致。仅后端，不要前端。"
RICH_FULL = "用户可以创建订单并查询订单状态。需要登录。峰值 500 QPS，延迟 200 毫秒，订单数据强一致。"


@pytest.fixture
def engine(tmp_path: Path) -> WorkflowEngine:
    return WorkflowEngine(WorkflowStore(tmp_path), AgentRegistry())


def test_full_lap_starts_at_the_clarifier_and_skips_the_web_role_for_backend_scope(engine: WorkflowEngine):
    view = engine.run(RICH_BACKEND)
    steps = view["steps"]
    assert view["status"] == "COMPLETED"
    assert len(steps) == 8
    assert steps[0]["agentId"] == "problem_clarifier"
    assert steps[7]["agentId"] == "programming_mentor"
    assert _step(steps, "web_app_builder")["action"] == "SKIP"
    assert _count(steps, "LOOP_BACK") == 0
    assert view["artifacts"]["solution_designer"]["recommendation"] == "模块化单体"
    assert view["artifacts"]["api_developer"]["idempotentWrites"] is True


def test_thin_demand_loops_back_once_then_finishes_the_ring(engine: WorkflowEngine):
    view = engine.run("请实现订单查询功能", scope="full")
    steps = view["steps"]
    assert view["status"] == "COMPLETED"
    assert _count(steps, "LOOP_BACK") == 1
    assert _count_agent(steps, "problem_clarifier") == 2
    assert _count_agent(steps, "solution_designer") == 2
    assert view["artifacts"]["problem_clarifier"]["clarityScore"] >= LOOP_BACK_BELOW
    assert view["artifacts"]["solution_designer"]["accepted"] is True
    assert _step(steps, "web_app_builder")["action"] == "NEXT"


def test_entry_can_be_any_role_and_the_ring_wraps_around(engine: WorkflowEngine):
    view = engine.run(RICH_FULL, entry_agent_id="api_developer", scope="full", audience_level="advanced")
    steps = view["steps"]
    assert len(steps) == 8
    assert steps[0]["agentId"] == "api_developer"
    assert steps[7]["agentId"] == "backend_architect"
    assert _step(steps, "web_app_builder")["action"] == "NEXT"
    assert view["audienceLevel"] == "advanced"


def test_vague_demand_waits_until_the_user_adds_a_real_goal(engine: WorkflowEngine):
    waiting = engine.run("做")
    assert waiting["status"] == "WAITING_FOR_USER"
    assert len(waiting["steps"]) == 1

    still_waiting = engine.resume(waiting["sessionId"], "好")
    assert still_waiting["status"] == "WAITING_FOR_USER"
    assert len(still_waiting["steps"]) == 2

    done = engine.resume(waiting["sessionId"], RICH_BACKEND)
    assert done["status"] == "COMPLETED"
    assert _step(done["steps"], "web_app_builder")["action"] == "SKIP"


def test_stopped_session_can_resume_without_new_feedback(engine: WorkflowEngine):
    stopped = engine.run(RICH_BACKEND, max_steps=1)
    assert stopped["status"] == "STOPPED_MAX_STEPS"
    assert stopped["cursor"] == "solution_designer"

    done = engine.resume(stopped["sessionId"])
    assert done["status"] == "COMPLETED"


def test_single_invoke_does_not_walk_the_ring(engine: WorkflowEngine):
    view = engine.invoke("solution_designer", "用户下单支付，峰值 5000 QPS，需要登录，订单强一致。", scope="backend-only")
    assert view["mode"] == "single"
    assert view["agentId"] == "solution_designer"
    assert view["artifact"]["recommendation"] == "按业务能力拆分的微服务"


def test_session_survives_a_new_store(tmp_path: Path):
    engine = WorkflowEngine(WorkflowStore(tmp_path), AgentRegistry())
    done = engine.run(RICH_BACKEND)
    reloaded = WorkflowEngine(WorkflowStore(tmp_path), AgentRegistry())
    loaded = reloaded.get(done["sessionId"])
    assert loaded["status"] == "COMPLETED"
    assert loaded["artifacts"]["solution_designer"]["recommendation"] == "模块化单体"


def test_closed_session_and_bad_arguments_are_rejected(engine: WorkflowEngine):
    done = engine.run(RICH_BACKEND)
    with pytest.raises(WorkflowError) as closed:
        engine.resume(done["sessionId"], "再改一下")
    assert closed.value.code == ErrorCode.SESSION_CLOSED

    with pytest.raises(WorkflowError) as empty:
        engine.run("  ")
    assert empty.value.code == ErrorCode.DEMAND_REJECTED

    with pytest.raises(WorkflowError) as unknown:
        engine.run(RICH_FULL, entry_agent_id="nobody")
    assert unknown.value.code == ErrorCode.UNKNOWN_AGENT

    with pytest.raises(WorkflowError) as steps:
        engine.run(RICH_FULL, max_steps=0)
    assert steps.value.code == ErrorCode.INVALID_ARGUMENT

    with pytest.raises(WorkflowError) as missing:
        engine.get("00000000-0000-0000-0000-000000000000")
    assert missing.value.code == ErrorCode.SESSION_NOT_FOUND


def test_ring_order_is_the_delivery_order_not_the_catalog_grid():
    assert AgentId.PROBLEM_CLARIFIER.next() is AgentId.SOLUTION_DESIGNER
    assert AgentId.PROGRAMMING_MENTOR.next() is AgentId.PROBLEM_CLARIFIER
    assert len(list(AgentId)) == 8


def _step(steps, agent_id):
    return next(item for item in steps if item["agentId"] == agent_id)


def _count(steps, action):
    return sum(1 for item in steps if item["action"] == action)


def _count_agent(steps, agent_id):
    return sum(1 for item in steps if item["agentId"] == agent_id)
