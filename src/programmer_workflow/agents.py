"""八个角色的方法论。每个类只产自己的交付物，不调用下一位。"""

from __future__ import annotations

from programmer_workflow.model import AgentContext, AgentId, AgentResult, Handoff
from programmer_workflow.policy import LOOP_BACK_BELOW, MICROSERVICE_QPS, REENTRY_BONUS, WAIT_BELOW


class ProblemClarifierAgent:
    agent_id = AgentId.PROBLEM_CLARIFIER

    def execute(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        score = profile.clarity_score
        if context.reentry:
            score = min(100, score + REENTRY_BONUS)
        blind_spots = _blind_spots(profile)
        open_questions = [spot["question"] for spot in blind_spots if spot["status"] == "open"]
        criteria = _acceptance(profile)
        waiting = (not context.reentry) and score < WAIT_BELOW
        artifact = {
            "restatedProblem": f"为调用方提供「{profile.primary_label}」相关能力：{_clip(profile.demand)}",
            "actors": _actors(profile.demand),
            "goals": [f"完成「{profile.primary_label}」的核心读写，并让失败时可以判断原因"],
            "constraints": {
                "qps": profile.qps,
                "latencyMs": profile.latency_ms,
                "consistency": profile.consistency,
                "scope": profile.scope,
            },
            "assumptions": _assumptions(profile, context.reentry),
            "blindSpots": blind_spots,
            "acceptanceCriteria": criteria,
            "openQuestions": open_questions,
            "clarityScore": score,
            "needsUserInput": waiting,
        }
        _note(artifact, context)
        if waiting:
            return AgentResult(
                self.agent_id,
                "需求还不能执行：看不出调用方、核心动作或成功标准。",
                artifact,
                Handoff.wait_for_user(f"请补充谁在用、要完成的动作，以及怎样算做完。当前清晰度 {score}"),
            )
        summary = (
            f"已冻结假设并继续。清晰度 {score}，未决问题 {len(open_questions)} 个。"
            if context.reentry
            else f"问题已整理。清晰度 {score}，验收标准 {len(criteria)} 条。"
        )
        return AgentResult(self.agent_id, summary, artifact, Handoff.next("问题简报已足够让方案角色对比选项"))


class SolutionDesignerAgent:
    agent_id = AgentId.SOLUTION_DESIGNER

    def execute(self, context: AgentContext) -> AgentResult:
        clarity = context.published_clarity()
        if context.workflow and not context.reentry and clarity < LOOP_BACK_BELOW:
            return AgentResult(
                self.agent_id,
                f"清晰度 {clarity}，不足以冻结方案，退回问题梳理助手。",
                {"accepted": False, "clarityScore": clarity, "reason": "目标、约束或验收标准还不足以在两种架构里做选择"},
                Handoff.loop_back(AgentId.PROBLEM_CLARIFIER, f"清晰度 {clarity} 低于 {LOOP_BACK_BELOW}"),
            )
        profile = context.profile
        microservice = profile.qps is not None and profile.qps >= MICROSERVICE_QPS
        recommendation = "按业务能力拆分的微服务" if microservice else "模块化单体"
        why = (
            f"峰值 {profile.qps} QPS 已经超过单进程垂直扩容的舒适区。拆出「{profile.primary_label}」服务后可以单独扩容，但必须放弃跨服务的分布式事务。"
            if microservice
            else f"峰值未知或低于 {MICROSERVICE_QPS} QPS。模块化单体可以用本地事务保住「{profile.primary_label}」的一致性，复杂度和机器成本都低于微服务。等出现独立的扩容指标再拆。"
        )
        if microservice:
            consistency = "原文要求强一致或未声明。微服务里不用分布式事务，每个服务用本地事务，跨服务用发件箱在本库事务内写事件。"
            if profile.consistency == "eventual":
                consistency = "原文接受最终一致。写入本地成功后异步通知其他读模型。"
        elif profile.consistency == "eventual":
            consistency = "原文接受最终一致。写入本地成功后异步通知其他读模型。"
        else:
            consistency = f"用单库本地事务保证「{profile.primary_label}」写入的强一致。不引入两阶段提交。"
        artifact = {
            "accepted": True,
            "clarityScore": clarity,
            "recommendation": recommendation,
            "why": why,
            "options": [_option_modular(profile.primary_label), _option_services(profile.primary_label)],
            "consistencyChoice": consistency,
            "residualRisks": context.artifact(AgentId.PROBLEM_CLARIFIER).get("openQuestions", []),
        }
        _note(artifact, context)
        return AgentResult(self.agent_id, f"推荐「{recommendation}」。{why}", artifact, Handoff.next("方案已选定，后端角色按此拆模块和数据"))


class BackendArchitectAgent:
    agent_id = AgentId.BACKEND_ARCHITECT

    def execute(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        recommendation = str(context.artifact(AgentId.SOLUTION_DESIGNER).get("recommendation") or "")
        microservice = "微服务" in recommendation or (
            not recommendation and profile.qps is not None and profile.qps >= MICROSERVICE_QPS
        )
        style = "微服务" if microservice else "模块化单体"
        use_cache = profile.qps is not None or profile.latency_ms is not None
        outbox = microservice or profile.payment_like
        storage = "MySQL 作为事实来源，Redis 只缓存可重建的读模型" if use_cache else "MySQL 作为唯一事实来源"
        modules = [
            "api：只做校验、认证和协议转换，不写业务规则",
            f"domain：承载「{profile.primary_label}」的状态机和不变式",
            "persistence：参数化访问 MySQL，禁止拼接 SQL",
        ]
        if profile.authentication_required or profile.authentication_mentioned:
            modules.append("identity：校验调用方身份，业务模块不自己解析令牌")
        if microservice:
            modules.append("messaging：只投递本服务发件箱里的事件")
        artifact = {
            "style": style,
            "whyStyle": "沿用方案角色的拆分结论。服务之间不共享数据库，避免隐式耦合。" if microservice else "沿用方案角色的单体结论。一个进程里用模块边界代替网络边界。",
            "modules": modules,
            "storage": storage,
            "cache": use_cache,
            "async": "发件箱：业务数据和事件写在同一个本地事务里，异步投递失败可以重试。" if outbox else "同步处理。没有跨进程协作时不引入消息队列。",
            "consistency": "每个服务一个库。跨服务只传递已经提交的事件，不使用两阶段提交。" if microservice else f"「{profile.primary_label}」的一次状态变更放在单个本地事务里。",
            "healthPath": "/actuator/health",
            "errorCodes": _error_codes(profile.primary_prefix, profile.primary_label),
            "security": [
                "SQL 使用参数化查询或 ORM，不拼接用户输入。",
                "密码只存 bcrypt 或 argon2 哈希，不写在代码和镜像里。",
                "数据库口令、令牌密钥从环境变量或密钥管理服务读取。",
                "对外错误信息只包含错误码和可公开的说明。",
                "写接口校验内容类型、长度和枚举值，不信任调用方传入的状态。",
            ],
            "entity": {
                "name": profile.primary_label,
                "fields": ["id", "status", "version", "createdAt", "updatedAt"],
                "whyVersion": "用版本号做乐观锁，避免两个并发更新互相覆盖。",
            },
            "diagram": _diagram(profile.primary_label, microservice, use_cache, outbox),
        }
        _note(artifact, context)
        tail = "，读模型可走 Redis。" if use_cache else "。"
        return AgentResult(
            self.agent_id,
            f"{style}。「{profile.primary_label}」以 MySQL 为事实来源{tail}",
            artifact,
            Handoff.next("模块和数据已经定下来，接口角色按此暴露契约"),
        )


class ApiDeveloperAgent:
    agent_id = AgentId.API_DEVELOPER

    def execute(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        endpoints = _endpoints(profile)
        errors = context.artifact(AgentId.BACKEND_ARCHITECT).get("errorCodes") or _error_codes(profile.primary_prefix, profile.primary_label)
        artifact = {
            "style": "REST",
            "resource": profile.primary_label,
            "slug": profile.primary_slug,
            "auth": "required" if profile.authentication_required else ("optional" if profile.authentication_mentioned else "unspecified"),
            "idempotentWrites": True,
            "idempotency": "所有 POST 要求 Idempotency-Key 头。相同键且相同正文返回第一次的结果；相同键不同正文返回 409。",
            "pagination": "列表使用 limit 与 cursor，不使用可被跳页放大的超大 offset。",
            "endpoints": endpoints,
            "errors": errors,
            "openapi": _openapi(profile, endpoints),
            "controllerSketch": _controller(profile),
            "revisedAfterReview": context.reentry,
        }
        _note(artifact, context)
        writes = sum(1 for item in endpoints if item["method"] == "POST")
        reason = "契约已足够测试和发布使用" if profile.backend_only else "契约已足够页面和测试使用"
        return AgentResult(
            self.agent_id,
            f"已定义 {len(endpoints)} 个端点，其中写操作 {writes} 个，均要求幂等键。",
            artifact,
            Handoff.next(reason),
        )


class WebAppBuilderAgent:
    agent_id = AgentId.WEB_APP_BUILDER

    def execute(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        if profile.backend_only:
            return AgentResult(
                self.agent_id,
                "范围是仅后端，本角色跳过，不生成页面。",
                {
                    "skipped": True,
                    "reason": "需求声明不包含网页。",
                    "why": "没有界面需求时虚构页面，会让测试和发布多出一个无人维护的客户端。",
                },
                Handoff.skip("仅后端范围，交接给测试角色"),
            )
        slug = profile.primary_slug
        label = profile.primary_label
        endpoints = context.artifact(AgentId.API_DEVELOPER).get("endpoints") or []
        artifact = {
            "skipped": False,
            "pages": [
                {"path": f"/{slug}", "purpose": f"列出{label}", "dataSource": f"GET /api/{slug}"},
                {"path": f"/{slug}/new", "purpose": f"创建{label}", "dataSource": f"POST /api/{slug}"},
                {"path": f"/{slug}/:id", "purpose": f"查看{label}详情", "dataSource": f"GET /api/{slug}/{{id}}"},
            ],
            "state": "服务端是事实来源。页面只保存当前光标、表单草稿和最近一次错误码，刷新后以接口结果为准。",
            "whyState": f"如果页面本地再维护一份「{label}」状态，取消和重试会和幂等键对不上。",
            "apiBinding": endpoints or ["等待 API 角色给出端点后再绑定"],
            "clientSketch": _client_sketch(slug, label),
        }
        _note(artifact, context)
        return AgentResult(self.agent_id, "三个页面：列表、创建、详情。数据以接口返回为准。", artifact, Handoff.next("页面已绑定接口，测试角色补浏览器外的契约测试"))


class TddEngineerAgent:
    agent_id = AgentId.TDD_ENGINEER

    def execute(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        api = context.artifact(AgentId.API_DEVELOPER)
        endpoints = api.get("endpoints") or []
        writes = any(item.get("method") == "POST" for item in endpoints if isinstance(item, dict))
        idempotent = bool(api.get("idempotentWrites"))
        api_present = bool(api)
        if context.workflow and not context.reentry and api_present and writes and not idempotent:
            return AgentResult(
                self.agent_id,
                "写接口没有幂等约定，退回 API 开发助手。",
                {"accepted": False, "gap": "missing-idempotency", "why": "没有幂等键的创建接口，测试无法断言重试不会产生第二笔数据。"},
                Handoff.loop_back(AgentId.API_DEVELOPER, "POST 端点缺少 idempotentWrites"),
            )
        criteria = context.artifact(AgentId.PROBLEM_CLARIFIER).get("acceptanceCriteria") or [f"调用方可以创建并读取「{profile.primary_label}」"]
        cases = [
            {"name": f"acceptance_{index}", "criterion": criterion, "firstColor": "red", "assertion": "按验收标准构造输入，断言状态码和错误码，不断言文案。"}
            for index, criterion in enumerate(criteria, start=1)
        ]
        gaps: list[str] = []
        if not api_present:
            gaps.append("接口角色尚未交接。本步只起草验收测试，环走到接口后再核对路径。")
        elif writes and not idempotent:
            gaps.append("写接口仍缺少幂等约定。退回次数已用完，发布前必须补上。")
        accepted = not gaps or "仍缺少" not in gaps[0]
        artifact = {
            "accepted": accepted,
            "order": ["红：先写失败测试", "绿：写刚好让测试通过的生产代码", "重构：去掉重复，测试保持绿色"],
            "pyramid": ["领域单测覆盖状态机和幂等分支", "接口契约测试覆盖状态码和错误码", "一条端到端测试覆盖创建后读取"],
            "cases": cases,
            "gaps": gaps,
            "testSketch": _test_sketch(profile.primary_prefix, profile.primary_slug),
        }
        _note(artifact, context)
        return AgentResult(
            self.agent_id,
            f"先写 {len(cases)} 条失败测试，再实现「{profile.primary_label}」。",
            artifact,
            Handoff.next("测试顺序已定，部署角色按可回滚的方式发布"),
        )


class DeploymentEngineerAgent:
    agent_id = AgentId.DEPLOYMENT_ENGINEER

    def execute(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        health = str(context.artifact(AgentId.BACKEND_ARCHITECT).get("healthPath") or "/actuator/health")
        blue_green = profile.payment_like
        strategy = "蓝绿发布" if blue_green else "滚动更新"
        why = (
            "需求涉及支付或退款。蓝绿可以在健康检查失败时把流量一次切回旧版本，避免一半实例写新账、一半写旧账。"
            if blue_green
            else "没有支付类写路径。滚动更新占用的机器更少；每一批新实例通过健康检查后才接流量。"
        )
        latency = f"{profile.latency_ms} 毫秒" if profile.latency_ms is not None else "未声明的延迟预算"
        artifact = {
            "runtime": "容器",
            "healthPath": health,
            "strategy": strategy,
            "whyStrategy": why,
            "pipeline": ["单元测试和契约测试", "构建不可变镜像", f"部署到预发并检查 {health}", "按策略切换生产流量", "观察错误率和延迟"],
            "rollback": "保留上一镜像标签。新版本健康检查失败或错误率超过阈值时，把流量切回该标签，不在线上改镜像内容。",
            "zeroDowntime": "切换期间旧版本继续服务已建立的请求。数据库变更新增可空字段或兼容读写，不在同一次发布里删除旧列。",
            "config": ["DB_URL", "DB_PASSWORD", "REDIS_URL"],
            "whyConfig": "口令和连接串来自环境变量或密钥管理，不写进镜像和仓库。",
            "metrics": ["请求速率", "核心接口 P99", "5xx 比例", "数据库连接池占用"],
            "alerts": [f"5 分钟内 5xx 比例超过 1% 就告警", f"核心读接口 P99 持续 5 分钟高于 {latency} 就告警", "连续 3 次健康检查失败就自动回滚并告警"],
            "pipelineSketch": f"steps:\n  - run: pytest\n  - run: docker build -t app:${{GITHUB_SHA}} .\n  - run: deploy --image app:${{GITHUB_SHA}} --health {health}\n",
        }
        _note(artifact, context)
        return AgentResult(self.agent_id, f"{strategy}。健康检查 {health} 失败则切回上一镜像。", artifact, Handoff.next("发布路径已定，教学角色把整环结论讲清楚"))


class ProgrammingMentorAgent:
    agent_id = AgentId.PROGRAMMING_MENTOR

    def execute(self, context: AgentContext) -> AgentResult:
        profile = context.profile
        level = profile.audience_level
        style = str(context.artifact(AgentId.BACKEND_ARCHITECT).get("style") or "尚未定稿的后端结构")
        recommendation = str(context.artifact(AgentId.SOLUTION_DESIGNER).get("recommendation") or "")
        web_skipped = bool(context.artifact(AgentId.WEB_APP_BUILDER).get("skipped")) or profile.backend_only
        web_sentence = "这轮没有做网页，因为范围是后端。" if web_skipped else f"网页只展示接口结果，不自己保存一份「{profile.primary_label}」。"
        if level == "beginner":
            explanation = f"可以把这八个人想成一条接力：先问清楚要做什么，再选一条路，然后规定数据放哪、接口长什么样。{web_sentence}测试是在写功能之前先写下怎样算对。发布时留好上一版，新版本不健康就换回去。当前结构是{style}。"
        elif level == "advanced":
            chosen = recommendation or style
            transaction = "，跨服务只传递已提交事件，避免两阶段提交。" if style == "微服务" else "，不拆库，所以不需要分布式事务。"
            explanation = f"推荐「{chosen}」。写入使用本地事务{transaction}幂等键把重试变成同一次业务操作。发布以不可变镜像和健康检查为门禁。{web_sentence}"
        else:
            explanation = f"本轮先把「{profile.primary_label}」收成「{style}」。方案选择的理由在技术方案产物的 why 字段。接口把状态码和错误码分开，测试按验收标准从红到绿。{web_sentence}部署产物里写了回滚条件和告警。"
        artifact = {
            "audienceLevel": level,
            "explanation": explanation,
            "glossary": _glossary(profile),
            "practice": [
                f"用自己的话写出「{profile.primary_label}」创建失败时调用方看到什么，不能只写成功路径。",
                "给创建请求设计一个幂等键，并说明相同键、不同正文时为什么返回冲突。",
                "画出发布时健康检查失败的流量走向，标出旧版本还在接哪一部分请求。" if web_skipped else "指出页面上哪一块状态刷新后必须丢弃，改以接口结果为准。",
            ],
            "doNotMemorize": "不要背端点路径。先记住三件事：事实来源在服务端、写请求要能安全重试、发布必须能退回上一版本。",
        }
        _note(artifact, context)
        return AgentResult(self.agent_id, f"已按「{level}」解释本轮结论，并留下三道练习。", artifact, Handoff.next("讲解完成。若从教学角色进入环，下一位是问题梳理助手"))


class AgentRegistry:
    def __init__(self, agents: list | None = None) -> None:
        items = agents or [
            ProblemClarifierAgent(),
            SolutionDesignerAgent(),
            BackendArchitectAgent(),
            ApiDeveloperAgent(),
            WebAppBuilderAgent(),
            TddEngineerAgent(),
            DeploymentEngineerAgent(),
            ProgrammingMentorAgent(),
        ]
        expected = list(AgentId)
        if len(items) != len(expected):
            raise ValueError("角色数量必须是 8")
        indexed = {}
        for index, agent in enumerate(items):
            if agent.agent_id is not expected[index]:
                raise ValueError("角色环顺序必须与 AgentId 声明顺序一致")
            indexed[agent.agent_id] = agent
        self._agents = indexed

    def require(self, agent_id: AgentId):
        agent = self._agents.get(agent_id)
        if agent is None:
            from programmer_workflow.errors import ErrorCode, WorkflowError

            raise WorkflowError(ErrorCode.UNKNOWN_AGENT, f"未注册角色: {agent_id.tool_name}")
        return agent


def _glossary(profile) -> list[dict]:
    terms = [
        {"term": "幂等", "meaning": "同一个请求执行一次和执行多次，业务结果相同。"},
        {"term": "事实来源", "meaning": f"「{profile.primary_label}」以数据库里已提交的数据为准，缓存和页面都可以丢掉重建。"},
        {"term": "交接环", "meaning": "八个角色按固定顺序传递产物，绕回入口就结束；质量不够时退回一次。"},
    ]
    if profile.consistency in {"strong", "unspecified"}:
        terms.append({"term": "本地事务", "meaning": "一次写入在同一个数据库里要么全部提交，要么全部撤销。"})
    return terms


def _note(artifact: dict, context: AgentContext) -> None:
    if context.upstream_note:
        artifact["callerContext"] = context.upstream_note


def _clip(demand: str) -> str:
    if len(demand) <= 120:
        return demand
    return demand[:120] + "…"


def _actors(demand: str) -> list[str]:
    actors = []
    if "管理员" in demand:
        actors.append("管理员")
    if "用户" in demand or "调用方" in demand:
        actors.append("终端用户或调用方")
    return actors or ["未点名的调用方"]


def _assumptions(profile, reentry: bool) -> list[str]:
    assumptions = []
    if profile.qps is None:
        assumptions.append(f"未给出峰值。先按低于 {MICROSERVICE_QPS} QPS 的单团队系统设计，避免过早拆服务。")
    if profile.consistency == "unspecified":
        assumptions.append(f"未声明一致性。涉及「{profile.primary_label}」的写入先按单库本地事务处理。")
    if not profile.authentication_mentioned:
        assumptions.append("未说明身份。先把写接口视为需要认证，读接口是否公开留到接口契约里单列。")
    if reentry:
        assumptions.append("方案角色退回过一次。本轮不再追问，缺口记在盲区里，用上述假设冻结范围。")
    return assumptions


def _acceptance(profile) -> list[str]:
    criteria = [f"调用方可以完成「{profile.primary_label}」的创建和按标识读取"]
    if profile.mentions_cancel:
        criteria.append(f"取消「{profile.primary_label}」可以重复调用，并且只生效一次")
    if profile.authentication_required:
        criteria.append("未认证请求访问受保护资源时返回 401，响应体不含内部异常")
    if profile.qps is not None:
        criteria.append(f"在 {profile.qps} QPS 下，核心读接口仍返回明确的成功或限流结果")
    criteria.append(f"写请求携带幂等键时，重复提交不产生第二笔「{profile.primary_label}」")
    return criteria


def _blind_spots(profile) -> list[dict]:
    demand = profile.demand
    return [
        _spot("身份与权限", profile.authentication_mentioned, "原文提到了认证或匿名" if profile.authentication_mentioned else "未说明谁可以调用", "谁可以调用，未认证请求应该拒绝还是允许匿名？"),
        _spot("幂等与重复提交", profile.mentions_idempotency, "原文提到了幂等或重复请求" if profile.mentions_idempotency else "未说明写请求重试时是否允许第二笔数据", f"创建「{profile.primary_label}」时，超时重试应该返回同一结果还是再写一笔？"),
        _spot("失败与补偿", _has(demand, "失败", "重试", "补偿", "回滚"), "原文提到了失败处理" if _has(demand, "失败", "重试", "补偿", "回滚") else "原文没有失败路径", "下游失败时是直接把错误返回调用方，还是先记下再补偿？"),
        _spot("数据保留", _has(demand, "保留", "删除", "归档"), "原文提到了数据生命周期" if _has(demand, "保留", "删除", "归档") else "原文没有保留期限", f"「{profile.primary_label}」要保留多久，删除是物理删除还是标记删除？"),
        _spot("可观测性", _has(demand, "监控", "日志", "指标", "告警"), "原文提到了观测" if _has(demand, "监控", "日志", "指标", "告警") else "原文没有观测要求", "上线后靠哪些指标判断这套能力是健康的？"),
        _spot("容量", profile.qps is not None or "限流" in demand or "容量" in demand, "没有峰值或限流要求" if profile.qps is None else f"峰值 {profile.qps} QPS", "峰值和超限时的行为是什么？"),
        _spot("租户隔离", profile.multi_tenant or "单租户" in demand, "原文要求租户" if profile.multi_tenant else "按单租户处理", "数据是否要按租户隔离？"),
    ]


def _spot(item: str, covered: bool, evidence: str, question: str) -> dict:
    return {"item": item, "status": "covered" if covered else "open", "evidence": evidence, "question": question}


def _has(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def _option_modular(label: str) -> dict:
    return {
        "name": "模块化单体",
        "summary": f"一个可部署单元，内部分成接口、领域和「{label}」存储模块。",
        "complexity": "低",
        "performance": "单库事务，读路径可以加缓存。垂直扩容为主。",
        "cost": "低。一条发布流水线，一套数据库。",
        "maintainability": "高，适合当前这一组需求。模块边界靠包结构而不是网络。",
        "fitsWhen": f"团队小，峰值低于 {MICROSERVICE_QPS} QPS，或峰值未知。",
    }


def _option_services(label: str) -> dict:
    return {
        "name": "按业务能力拆分的微服务",
        "summary": f"「{label}」独立部署，通过接口和发件箱事件协作。",
        "complexity": "高。要处理超时、重复投递和跨服务排障。",
        "performance": "可以按热点服务水平扩展。",
        "cost": "高。每多一个服务就多一份发布、监控和值班成本。",
        "maintainability": "服务边界清楚之后较高；边界划错时改动会跨仓库。",
        "fitsWhen": f"峰值达到 {MICROSERVICE_QPS} QPS，或已有独立团队拥有该能力。",
    }


def _error_codes(prefix: str, label: str) -> list[dict]:
    return [
        {"code": f"{prefix}-4001", "httpStatus": 400, "type": "BUSINESS", "meaning": "字段缺失或格式不合法"},
        {"code": f"{prefix}-4004", "httpStatus": 404, "type": "BUSINESS", "meaning": f"「{label}」不存在"},
        {"code": f"{prefix}-4009", "httpStatus": 409, "type": "BUSINESS", "meaning": "状态冲突或幂等键被另一笔请求占用"},
        {"code": f"{prefix}-4010", "httpStatus": 401, "type": "BUSINESS", "meaning": "未认证"},
        {"code": f"{prefix}-5000", "httpStatus": 500, "type": "SYSTEM", "meaning": "未预期的系统故障，日志带请求标识，响应不回传堆栈"},
    ]


def _diagram(label: str, microservice: bool, cache: bool, outbox: bool) -> str:
    cache_line = "\n  domain --> cache[(Redis)]" if cache else ""
    if not microservice:
        return f"flowchart LR\n  client[Client] --> api[API]\n  api --> domain[\"{label} Domain\"]\n  domain --> db[(MySQL)]{cache_line}\n"
    bus = "\n  domain --> outbox[Outbox]\n  outbox --> bus[Event Bus]" if outbox else ""
    return f"flowchart LR\n  client[Client] --> api[API]\n  api --> domain[\"{label} Service\"]\n  domain --> db[(MySQL)]{cache_line}{bus}\n"


def _endpoints(profile) -> list[dict]:
    slug = profile.primary_slug
    label = profile.primary_label
    endpoints = []
    if profile.authentication_required:
        endpoints.append({"method": "POST", "path": "/api/sessions", "purpose": "校验凭证并建立会话", "idempotent": True})
    endpoints.extend(
        [
            {"method": "GET", "path": f"/api/{slug}", "purpose": f"分页查询{label}", "idempotent": False},
            {"method": "POST", "path": f"/api/{slug}", "purpose": f"创建{label}", "idempotent": True},
            {"method": "GET", "path": f"/api/{slug}/{{id}}", "purpose": f"按标识读取{label}", "idempotent": False},
        ]
    )
    if profile.mentions_cancel:
        endpoints.append({"method": "POST", "path": f"/api/{slug}/{{id}}/cancel", "purpose": f"取消{label}，重复调用返回同一终态", "idempotent": True})
    return endpoints


def _openapi(profile, endpoints: list[dict]) -> str:
    lines = [
        "openapi: 3.0.3",
        "info:",
        f"  title: {profile.primary_label} API",
        "  version: 0.1.0",
        "paths:",
    ]
    for endpoint in endpoints:
        lines.append(f"  {endpoint['path']}:")
        lines.append(f"    {endpoint['method'].lower()}:")
        lines.append(f"      summary: {endpoint['purpose']}")
        if endpoint["idempotent"] and endpoint["method"] == "POST":
            lines.append("      parameters:")
            lines.append("        - name: Idempotency-Key")
            lines.append("          in: header")
            lines.append("          required: true")
            lines.append("          schema: {type: string, maxLength: 64}")
    return "\n".join(lines) + "\n"


def _controller(profile) -> str:
    type_name = profile.primary_prefix[:1].upper() + profile.primary_prefix[1:].lower()
    slug = profile.primary_slug
    return (
        f"@RestController\n@RequestMapping(\"/api/{slug}\")\n"
        f"class {type_name}Controller {{\n"
        f"    @PostMapping\n"
        f"    ResponseEntity<{type_name}Response> create(\n"
        f"            @RequestHeader(\"Idempotency-Key\") String idempotencyKey,\n"
        f"            @Valid @RequestBody {type_name}Request request) {{\n"
        f"        // 先按幂等键查找。SQL 必须参数化，不能拼接 request 字段。\n"
        f"        return ResponseEntity.status(HttpStatus.CREATED).body(service.create(idempotencyKey, request));\n"
        f"    }}\n}}\n"
    )


def _client_sketch(slug: str, label: str) -> str:
    function_name = slug[:1].upper() + slug[1:]
    return (
        f"// 创建{label}。刷新后以响应体为准，不把成功结果写入本地数据库。\n"
        f"async function create{function_name}(idempotencyKey, body) {{\n"
        f"  const response = await fetch('/api/{slug}', {{\n"
        f"    method: 'POST',\n"
        f"    headers: {{ 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey }},\n"
        f"    body: JSON.stringify(body)\n"
        f"  }});\n"
        f"  if (!response.ok) throw new Error((await response.json()).code || '请求失败');\n"
        f"  return response.json();\n}}\n"
    )


def _test_sketch(prefix: str, slug: str) -> str:
    type_name = prefix[:1].upper() + prefix[1:].lower()
    return (
        f"class {type_name}ApiTest {{\n"
        f"    @Test\n"
        f"    void duplicateIdempotencyKeyDoesNotCreateSecondRecord() {{\n"
        f"        Response first = post(\"/api/{slug}\", \"test-key-1\", \"{{\\\"name\\\":\\\"demo\\\"}}\");\n"
        f"        Response second = post(\"/api/{slug}\", \"test-key-1\", \"{{\\\"name\\\":\\\"demo\\\"}}\");\n"
        f"        assertEquals(first.body(), second.body());\n"
        f"    }}\n}}\n"
    )
