"""交接环的质量门槛。集中放在这里，避免每个角色各写一个魔法数字。"""

from __future__ import annotations

# 低于此分说明目标还不能执行，问题梳理助手停下来等用户补充。
WAIT_BELOW = 45
# 低于此分说明还能往下做，但方案角色会退回问题梳理再冻结一次假设。
LOOP_BACK_BELOW = 60
# 退回重入时加上的分数。同一段原文不会因为退回而变长，用它表示假设已冻结，停止打转。
REENTRY_BONUS = 25
DEFAULT_MAX_STEPS = 12
# 单次 run_workflow 允许调用方指定的上限。一次完整环加一次退回大约是 10 步。
REQUEST_MAX_STEPS = 16
# 多次 resume 累加后的绝对上限，防止会话被无限续跑。
ABSOLUTE_STEP_CEILING = 32
MAX_TEXT_LENGTH = 8000
# 单体和微服务的分界。低于此峰值时，拆服务的运维成本大于扩容收益。
MICROSERVICE_QPS = 2000
