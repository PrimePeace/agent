"""从需求原文抽出后续角色都会用到的信号。只做可复现的抽取，不调用模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from programmer_workflow.errors import ErrorCode, WorkflowError
from programmer_workflow.policy import MAX_TEXT_LENGTH

_QPS = re.compile(r"(\d{1,7})\s*(?:qps|QPS|次/秒|次每秒)")
_LATENCY = re.compile(r"(\d{1,6})\s*(?:ms|毫秒)")
_NOUNS = (
    ("订单", "orders", "ORDER"),
    ("商品", "products", "PRODUCT"),
    ("库存", "inventories", "STOCK"),
    ("支付", "payments", "PAY"),
    ("课程", "courses", "COURSE"),
    ("文章", "articles", "ARTICLE"),
    ("任务", "tasks", "TASK"),
    ("评论", "comments", "COMMENT"),
    ("工单", "tickets", "TICKET"),
    ("预约", "reservations", "BOOK"),
    ("文件", "files", "FILE"),
)
_USER = ("用户", "users", "USER")
_FALLBACK = ("业务对象", "resources", "ITEM")


@dataclass(frozen=True)
class DemandProfile:
    demand: str
    scope: str
    audience_level: str
    qps: int | None
    latency_ms: int | None
    consistency: str
    authentication_mentioned: bool
    authentication_required: bool
    payment_like: bool
    multi_tenant: bool
    mentions_cancel: bool
    mentions_idempotency: bool
    primary_label: str
    primary_slug: str
    primary_prefix: str
    nouns: tuple[str, ...]
    clarity_score: int

    @property
    def backend_only(self) -> bool:
        return self.scope == "backend-only"


def parse_profile(demand: str, scope: str | None, audience_level: str | None) -> DemandProfile:
    text = demand or ""
    label, slug, prefix = _primary_noun(text)
    found = [item[0] for item in _NOUNS if item[0] in text]
    if _USER[0] in text:
        found.append(_USER[0])
    qps = _first_number(_QPS, text)
    latency_ms = _first_number(_LATENCY, text)
    auth_mentioned = _contains(text, "登录", "鉴权", "认证", "权限", "oauth", "jwt", "匿名", "无需登录")
    auth_required = auth_mentioned and not _contains(text, "匿名", "无需登录", "不用登录")
    resolved_scope = require_scope(scope, text) if scope else ("backend-only" if detects_backend_only(text) else "full")
    resolved_audience = require_audience(audience_level, text) if audience_level else _detect_audience(text)
    return DemandProfile(
        demand=text,
        scope=resolved_scope,
        audience_level=resolved_audience,
        qps=qps,
        latency_ms=latency_ms,
        consistency=_consistency(text),
        authentication_mentioned=auth_mentioned,
        authentication_required=auth_required,
        payment_like=_contains(text, "支付", "退款", "扣款"),
        multi_tenant=_contains(text, "多租户", "租户"),
        mentions_cancel=_contains(text, "取消", "撤销"),
        mentions_idempotency=_contains(text, "幂等", "重复提交", "重复请求"),
        primary_label=label,
        primary_slug=slug,
        primary_prefix=prefix,
        nouns=tuple(found),
        clarity_score=_score(text, qps, latency_ms, bool(found), auth_mentioned),
    )


def detects_backend_only(demand: str | None) -> bool:
    return _contains(demand, "仅后端", "不要前端", "不需要前端", "无前端", "backend-only", "backend only")


def require_scope(requested: str | None, demand: str) -> str:
    if requested is None or not requested.strip():
        return "backend-only" if detects_backend_only(demand) else "full"
    scope = requested.strip().lower()
    if scope not in {"full", "backend-only"}:
        raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "scope 只能是 full 或 backend-only")
    return scope


def require_audience(requested: str | None, demand: str) -> str:
    audience = _detect_audience(demand) if requested is None or not requested.strip() else requested.strip().lower()
    if audience not in {"beginner", "intermediate", "advanced"}:
        raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "audienceLevel 只能是 beginner、intermediate 或 advanced")
    return audience


def require_demand(raw: str | None) -> str:
    if raw is None or not raw.strip():
        raise WorkflowError(ErrorCode.DEMAND_REJECTED, "demand 不能为空")
    trimmed = raw.strip()
    if "\0" in trimmed:
        raise WorkflowError(ErrorCode.DEMAND_REJECTED, "demand 含有非法字符")
    if len(trimmed) > MAX_TEXT_LENGTH:
        raise WorkflowError(ErrorCode.DEMAND_REJECTED, "demand 超过 8000 个字符")
    return trimmed


def optional_text(raw: str | None, field_name: str) -> str:
    if raw is None or not raw.strip():
        return ""
    trimmed = raw.strip()
    if len(trimmed) > MAX_TEXT_LENGTH:
        raise WorkflowError(ErrorCode.INVALID_ARGUMENT, f"{field_name} 超过 8000 个字符")
    return trimmed


def _score(text: str, qps: int | None, latency_ms: int | None, has_noun: bool, auth_mentioned: bool) -> int:
    score = 30
    length = len(text)
    if length >= 20:
        score += 20
    if length >= 80:
        score += 10
    if has_noun:
        score += 15
    if qps is not None or latency_ms is not None or _contains(text, "强一致", "最终一致", "事务"):
        score += 15
    if _contains(text, "必须", "需要", "支持", "可以"):
        score += 10
    if _contains(text, "用户", "管理员", "运营", "调用方"):
        score += 10
    if auth_mentioned:
        score += 5
    return min(score, 100)


def _consistency(text: str) -> str:
    if "强一致" in text or "事务" in text:
        return "strong"
    if "最终一致" in text:
        return "eventual"
    return "unspecified"


def _detect_audience(text: str) -> str:
    if _contains(text, "入门", "新手", "小白", "零基础"):
        return "beginner"
    if _contains(text, "资深", "高级", "原理", "源码"):
        return "advanced"
    return "intermediate"


def _primary_noun(text: str) -> tuple[str, str, str]:
    earliest: tuple[str, str, str] | None = None
    earliest_index = 10**9
    for noun in _NOUNS:
        index = text.find(noun[0])
        if index >= 0 and index < earliest_index:
            earliest_index = index
            earliest = noun
    if earliest is not None:
        return earliest
    # 「用户可以创建订单」里的用户是调用方。只有没有业务名词时才把用户当成资源。
    if _USER[0] in text:
        return _USER
    return _FALLBACK


def _first_number(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if match is None:
        return None
    return int(match.group(1))


def _contains(text: str | None, *tokens: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)
