from programmer_workflow.policy import LOOP_BACK_BELOW, WAIT_BELOW
from programmer_workflow.profile import detects_backend_only, parse_profile, require_scope


def test_short_demand_stays_below_the_wait_line():
    profile = parse_profile("做", None, None)
    assert profile.clarity_score < WAIT_BELOW
    assert profile.primary_label == "业务对象"


def test_concrete_but_thin_demand_can_continue_and_still_be_returned():
    profile = parse_profile("请实现订单查询功能", None, None)
    assert profile.clarity_score >= WAIT_BELOW
    assert profile.clarity_score < LOOP_BACK_BELOW
    assert profile.primary_label == "订单"
    assert profile.primary_slug == "orders"


def test_earliest_noun_wins_and_capacity_signals_are_parsed():
    profile = parse_profile("先做商品再做订单，峰值 5000 QPS，延迟 200 毫秒", "full", "advanced")
    assert profile.primary_label == "商品"
    assert profile.qps == 5000
    assert profile.latency_ms == 200
    assert profile.audience_level == "advanced"
    assert profile.scope == "full"


def test_actor_word_does_not_steal_the_business_noun():
    profile = parse_profile("用户可以创建订单并查询订单状态。需要登录。", None, None)
    assert profile.primary_label == "订单"
    assert profile.primary_slug == "orders"


def test_backend_only_is_detected_from_the_wording():
    assert detects_backend_only("只要接口，不要前端")
    assert require_scope(None, "仅后端实现订单") == "backend-only"
