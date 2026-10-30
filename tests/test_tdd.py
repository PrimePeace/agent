from programmer_workflow.agents import TddEngineerAgent
from programmer_workflow.model import AgentContext, AgentId, HandoffAction
from programmer_workflow.profile import parse_profile


def test_missing_idempotency_sends_the_contract_back_to_the_api_role():
    result = TddEngineerAgent().execute(_context(reentry=False))
    assert result.handoff.action is HandoffAction.LOOP_BACK
    assert result.handoff.target is AgentId.API_DEVELOPER
    assert result.artifact["accepted"] is False


def test_second_pass_does_not_loop_again_when_the_contract_is_still_wrong():
    result = TddEngineerAgent().execute(_context(reentry=True))
    assert result.handoff.action is HandoffAction.NEXT
    assert result.artifact["accepted"] is False


def _context(reentry: bool) -> AgentContext:
    return AgentContext(
        workflow=True,
        reentry=reentry,
        upstream_note="",
        profile=parse_profile("用户可以创建订单。需要登录。", "backend-only", "intermediate"),
        artifacts={
            "api_developer": {
                "idempotentWrites": False,
                "endpoints": [{"method": "POST", "path": "/api/orders"}],
            }
        },
    )
