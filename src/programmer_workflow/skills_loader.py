"""从 skills 目录加载技能。每次调用都重新读文件，改完 Markdown 不用重启。

有 agent.py 时用脚本决定产物；没有脚本时，内置八个角色仍走原有规则。
只有 Markdown 的新技能把知识库交给宿主，由宿主按文档实现。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from programmer_workflow.errors import ErrorCode, WorkflowError
from programmer_workflow.model import AgentContext, AgentId, AgentResult, Handoff, HandoffAction

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RESERVED = {"run_workflow", "resume_workflow", "get_workflow", "list_agents", "invoke_skill"}
_TRUE = {"true", "yes", "1", "是"}
_FALSE = {"false", "no", "0", "否"}


@dataclass(frozen=True)
class SkillHandle:
    """脚本技能的身份。字段与 AgentId 对齐，会话层只使用 tool_name 和 title。"""

    tool_name: str
    title: str


@dataclass(frozen=True)
class SkillSpec:
    name: str
    title: str
    duty: str
    output_name: str
    order: int
    ring: bool
    knowledge: str
    directory: Path
    skill_file: Path | None
    script_file: Path | None
    source: str

    @property
    def tool_name(self) -> str:
        return self.name


class SkillCatalog:
    def __init__(self, root: Path | None = None, registry=None) -> None:
        self.root = (root or default_skills_dir()).resolve()
        if registry is None:
            from programmer_workflow.agents import AgentRegistry

            registry = AgentRegistry()
        self._registry = registry
        self._scripts: dict[tuple[str, int], object] = {}

    def load(self) -> list[SkillSpec]:
        found = self._scan()
        if found:
            return found
        return [_builtin_spec(agent) for agent in AgentId]

    def ring(self) -> list[SkillSpec]:
        members = [skill for skill in self.load() if skill.ring]
        return sorted(members, key=lambda skill: (skill.order, skill.name))

    def require(self, raw: str | None) -> SkillSpec:
        if raw is None or not str(raw).strip():
            raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "角色标识不能为空")
        text = str(raw).strip()
        skills = self.load()
        for skill in skills:
            if text in {skill.name, skill.title}:
                return skill
        for agent in AgentId:
            if text in {agent.tool_name, agent.name, agent.title} or text.lower() == agent.name.lower():
                for skill in skills:
                    if skill.name == agent.tool_name:
                        return skill
        raise WorkflowError(ErrorCode.UNKNOWN_AGENT, f"未知角色: {text}")

    def default_entry(self) -> SkillSpec:
        members = self.ring()
        if not members:
            raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "交接环上没有技能")
        for skill in members:
            if skill.name == "problem_clarifier":
                return skill
        return members[0]

    def next_of(self, skill: SkillSpec) -> SkillSpec | None:
        members = self.ring()
        names = [item.name for item in members]
        if skill.name not in names:
            return None
        return members[(names.index(skill.name) + 1) % len(members)]

    def next_name(self, name: str) -> str:
        skill = self.require(name)
        nxt = self.next_of(skill)
        if nxt is None:
            raise WorkflowError(ErrorCode.INVALID_ARGUMENT, f"技能 {skill.name} 不在交接环上，不能自动交给下一位")
        return nxt.name

    def public_catalog(self) -> list[dict]:
        members = self.ring()
        ring_index = {skill.name: skill for skill in members}
        listed = []
        for skill in sorted(self.load(), key=lambda item: (not item.ring, item.order, item.name)):
            follower = self.next_of(skill) if skill.name in ring_index else None
            next_id = follower.name if follower is not None else None
            listed.append(
                {
                    "order": skill.order,
                    "agentId": skill.name,
                    "title": skill.title,
                    "duty": skill.duty,
                    "output": skill.output_name,
                    "tool": skill.name,
                    "nextAgentId": next_id,
                    "ring": skill.ring,
                    "source": skill.source,
                    "knowledgePath": str(skill.skill_file or skill.directory),
                    "hasScript": skill.script_file is not None,
                }
            )
        return listed

    def knowledge_view(self, names: list[str]) -> dict[str, dict]:
        view = {}
        for name in names:
            try:
                skill = self.require(name)
            except WorkflowError:
                continue
            view[skill.name] = {
                "title": skill.title,
                "source": skill.source,
                "path": str(skill.skill_file or skill.directory),
                "knowledge": skill.knowledge,
            }
        return view

    def execute(self, name: str, context: AgentContext) -> AgentResult:
        skill = self.require(name)
        context = replace(context, knowledge=skill.knowledge)
        if skill.script_file is not None:
            return self._run_script(skill, context)
        builtin = _builtin_agent(skill.name)
        if builtin is not None:
            return self._registry.require(builtin).execute(context)
        return _knowledge_result(skill, context)

    def _run_script(self, skill: SkillSpec, context: AgentContext) -> AgentResult:
        path = skill.script_file
        assert path is not None
        try:
            stamp = path.stat().st_mtime_ns
        except OSError as exc:
            raise WorkflowError(ErrorCode.INTERNAL, f"无法读取技能脚本 {skill.name}", system=True) from exc
        execute = self._scripts.get((str(path), stamp))
        if execute is None:
            execute = _load_execute(skill.name, path)
            self._scripts[(str(path), stamp)] = execute
        try:
            payload = execute(context)
        except WorkflowError:
            raise
        except Exception as exc:
            logger.exception("Skill script failed: %s", path)
            raise WorkflowError(ErrorCode.INTERNAL, f"技能脚本执行失败: {skill.name}: {exc}", system=True) from exc
        return _result_from_payload(self, skill, payload)

    def _scan(self) -> list[SkillSpec]:
        if not self.root.is_dir():
            logger.warning("Skills directory missing: %s", self.root)
            return []
        specs = []
        try:
            folders = sorted(path for path in self.root.iterdir() if path.is_dir())
        except OSError as exc:
            logger.warning("Cannot read skills directory %s: %s", self.root, exc)
            return []
        for folder in folders:
            if folder.name.startswith(("_", ".")):
                continue
            spec = _load_folder(folder)
            if spec is not None:
                specs.append(spec)
        return specs


def default_skills_dir() -> Path:
    override = os.environ.get("PROGRAMMER_SKILLS_DIR", "").strip()
    if override:
        return Path(override)
    start = Path(__file__).resolve()
    for parent in start.parents:
        if (parent / "skills" / "problem_clarifier" / "SKILL.md").is_file():
            return parent / "skills"
    return start.parents[2] / "skills"


def _load_folder(folder: Path) -> SkillSpec | None:
    if not _NAME.fullmatch(folder.name) or folder.name in _RESERVED:
        logger.warning("Skip skill folder with illegal name: %s", folder.name)
        return None
    skill_file = folder / "SKILL.md"
    script_file = folder / "agent.py"
    has_markdown = skill_file.is_file()
    has_script = script_file.is_file()
    if not has_markdown and not has_script:
        return None
    meta: dict[str, str] = {}
    body = ""
    if has_markdown:
        try:
            meta, body = _frontmatter(skill_file.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Skip unreadable skill file %s: %s", skill_file, exc)
            return None
    builtin = _builtin_agent(folder.name)
    ring_default = "true" if builtin is not None else "false"
    order_default = str((list(AgentId).index(builtin) + 1) * 10) if builtin is not None else "1000"
    return SkillSpec(
        name=folder.name,
        title=meta.get("title") or (builtin.title if builtin is not None else folder.name),
        duty=meta.get("description") or (builtin.duty if builtin is not None else "按本目录知识库处理需求。"),
        output_name=meta.get("output") or (builtin.output_name if builtin is not None else "知识库结论"),
        order=_integer(meta.get("order"), int(order_default)),
        ring=_bool(meta.get("ring"), ring_default),
        knowledge=body,
        directory=folder,
        skill_file=skill_file if has_markdown else None,
        script_file=script_file if has_script else None,
        source="script" if has_script else ("builtin" if builtin is not None else "knowledge"),
    )


def _builtin_spec(agent: AgentId) -> SkillSpec:
    return SkillSpec(
        name=agent.tool_name,
        title=agent.title,
        duty=agent.duty,
        output_name=agent.output_name,
        order=(list(AgentId).index(agent) + 1) * 10,
        ring=True,
        knowledge="",
        directory=Path("."),
        skill_file=None,
        script_file=None,
        source="builtin",
    )


def _builtin_agent(name: str) -> AgentId | None:
    for agent in AgentId:
        if agent.tool_name == name:
            return agent
    return None


def _knowledge_result(skill: SkillSpec, context: AgentContext) -> AgentResult:
    profile = context.profile
    return AgentResult(
        SkillHandle(skill.name, skill.title),
        f"已读取「{skill.title}」的知识库。宿主按 knowledge 处理「{profile.primary_label}」。",
        {
            "drivenBy": "knowledge",
            "demand": profile.demand,
            "scope": profile.scope,
            "audienceLevel": profile.audience_level,
        },
        Handoff.next("知识库已交给宿主。若该技能在环上，下一位继续。"),
    )


def _result_from_payload(catalog: SkillCatalog, skill: SkillSpec, payload: object) -> AgentResult:
    if not isinstance(payload, dict):
        raise WorkflowError(ErrorCode.INTERNAL, f"技能 {skill.name} 的脚本必须返回 dict", system=True)
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise WorkflowError(ErrorCode.INTERNAL, f"技能 {skill.name} 的脚本没有 summary", system=True)
    artifact = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else None
    if artifact is None:
        raise WorkflowError(ErrorCode.INTERNAL, f"技能 {skill.name} 的 artifact 必须是对象", system=True)
    try:
        json.dumps(artifact, ensure_ascii=False)
    except TypeError as exc:
        raise WorkflowError(ErrorCode.INTERNAL, f"技能 {skill.name} 的 artifact 不能序列化成 JSON", system=True) from exc
    handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
    action_text = str(handoff.get("action") or "NEXT")
    try:
        action = HandoffAction(action_text)
    except ValueError as exc:
        raise WorkflowError(ErrorCode.INVALID_ARGUMENT, f"技能 {skill.name} 的 handoff.action 不合法") from exc
    reason = str(handoff.get("reason") or "").strip() or "脚本未写交接原因"
    target = None
    target_name = handoff.get("target")
    if target_name:
        resolved = catalog.require(str(target_name))
        target = SkillHandle(resolved.name, resolved.title)
    return AgentResult(SkillHandle(skill.name, skill.title), summary, artifact, Handoff(action, reason, target))


def _load_execute(name: str, path: Path):
    module_name = f"programmer_skill_{name}_{path.stat().st_mtime_ns}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise WorkflowError(ErrorCode.INTERNAL, f"无法加载技能脚本 {name}", system=True)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.exception("Skill script import failed: %s", path)
        raise WorkflowError(ErrorCode.INTERNAL, f"技能脚本无法导入: {name}: {exc}", system=True) from exc
    execute = getattr(module, "execute", None)
    if not callable(execute):
        raise WorkflowError(ErrorCode.INTERNAL, f"技能 {name} 的 agent.py 必须定义 execute(context)", system=True)
    return execute


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    meta: dict[str, str] = {}
    end = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = index
            break
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    if end is None:
        return {}, text.strip()
    return meta, "\n".join(lines[end + 1 :]).strip()


def _integer(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool(value: str | None, default: str) -> bool:
    text = (value if value is not None and value.strip() else default).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default.strip().lower() in _TRUE
