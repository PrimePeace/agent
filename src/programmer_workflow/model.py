"""八个角色。枚举声明顺序就是交接环，宫格顺序只是目录。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from programmer_workflow.errors import ErrorCode, WorkflowError
from programmer_workflow.profile import DemandProfile


class AgentId(Enum):
    PROBLEM_CLARIFIER = ("problem_clarifier", "问题梳理助手", "把模糊需求整理成目标、约束、盲区和验收标准。信息不够时停下等待补充，不把猜测直接交给下游。", "问题简报")
    SOLUTION_DESIGNER = ("solution_designer", "技术方案梳理", "在写代码前给出至少两种方案，从复杂度、性能、成本和可维护性对比，并说明为什么推荐其中一个。", "方案对比与推荐")
    BACKEND_ARCHITECT = ("backend_architect", "后端开发模式", "按推荐方案落地服务边界、数据存储、一致性、错误码和安全控制，产出可实现的后端结构。", "后端架构说明")
    API_DEVELOPER = ("api_developer", "API 开发助手", "把架构收成可调用的 HTTP 契约：资源、状态码、幂等、错误模型和一段可编译的接口骨架。", "接口契约")
    WEB_APP_BUILDER = ("web_app_builder", "网页应用创建", "在范围包含界面时，从接口契约画出页面、状态和数据来源。仅后端时明确跳过，不虚构页面。", "网页应用方案")
    TDD_ENGINEER = ("tdd_engineer", "测试驱动开发", "先把验收标准写成失败测试，再规定红-绿-重构顺序。写接口没有幂等约定时退回 API 角色。", "测试计划与样例")
    DEPLOYMENT_ENGINEER = ("deployment_engineer", "部署助手", "给出可回滚的发布方式、健康检查、流水线阶段和告警。密钥只出现在环境变量名里。", "发布与回滚方案")
    PROGRAMMING_MENTOR = ("programming_mentor", "编程教学", "按听众水平解释前面各位的结论，标出术语，并留可操作的练习。它是环的最后一棒，不替代前面的设计。", "讲解与练习")

    def __init__(self, tool_name: str, title: str, duty: str, output_name: str) -> None:
        self.tool_name = tool_name
        self.title = title
        self.duty = duty
        self.output_name = output_name

    def next(self) -> AgentId:
        items = list(AgentId)
        return items[(items.index(self) + 1) % len(items)]

    @staticmethod
    def from_text(raw: str | None) -> AgentId:
        if raw is None or not raw.strip():
            raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "角色标识不能为空")
        text = raw.strip()
        for agent in AgentId:
            if text in {agent.tool_name, agent.name, agent.title} or text.lower() == agent.name.lower():
                return agent
        raise WorkflowError(ErrorCode.UNKNOWN_AGENT, f"未知角色: {text}")


class HandoffAction(Enum):
    NEXT = "NEXT"
    SKIP = "SKIP"
    LOOP_BACK = "LOOP_BACK"
    WAIT = "WAIT"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True)
class Handoff:
    action: HandoffAction
    reason: str
    target: AgentId | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("交接原因不能为空")
        if self.action is HandoffAction.LOOP_BACK and self.target is None:
            raise ValueError("退回必须指定角色")

    @staticmethod
    def next(reason: str) -> Handoff:
        return Handoff(HandoffAction.NEXT, reason)

    @staticmethod
    def skip(reason: str) -> Handoff:
        return Handoff(HandoffAction.SKIP, reason)

    @staticmethod
    def wait_for_user(reason: str) -> Handoff:
        return Handoff(HandoffAction.WAIT, reason)

    @staticmethod
    def loop_back(target: AgentId, reason: str) -> Handoff:
        return Handoff(HandoffAction.LOOP_BACK, reason, target)


@dataclass(frozen=True)
class AgentResult:
    agent_id: AgentId
    summary: str
    artifact: dict
    handoff: Handoff


@dataclass(frozen=True)
class AgentContext:
    """一次角色执行能看到的全部输入。artifacts 用角色工具名做键。"""

    workflow: bool
    reentry: bool
    upstream_note: str
    profile: DemandProfile
    artifacts: dict[str, dict]
    knowledge: str = ""

    def artifact(self, agent_id: AgentId) -> dict:
        return self.artifacts.get(agent_id.tool_name, {})

    def published_clarity(self) -> int:
        value = self.artifact(AgentId.PROBLEM_CLARIFIER).get("clarityScore")
        if isinstance(value, int):
            return value
        return self.profile.clarity_score
