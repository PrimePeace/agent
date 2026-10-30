import os
from pathlib import Path

import pytest

from programmer_workflow.engine import WorkflowEngine
from programmer_workflow.errors import ErrorCode, WorkflowError
from programmer_workflow.model import AgentId
from programmer_workflow.skills_loader import SkillCatalog
from programmer_workflow.store import WorkflowStore


def test_builtin_ring_follows_the_skill_files():
    catalog = SkillCatalog()
    assert [skill.name for skill in catalog.ring()] == [agent.tool_name for agent in AgentId]
    clarifier = catalog.require("problem_clarifier")
    assert catalog.next_of(clarifier).name == "solution_designer"
    assert "下一次调用就会读到新内容" in clarifier.knowledge


def test_markdown_edits_apply_on_the_next_call(tmp_path: Path):
    _write_skill(tmp_path, "第一版知识", ring=False)
    engine = _engine(tmp_path)
    first = engine.invoke("note_taker", "用户可以创建订单")
    assert first["source"] == "knowledge"
    assert "第一版知识" in first["knowledge"]

    _write_skill(tmp_path, "第二版知识", ring=False)
    second = engine.invoke("note_taker", "用户可以创建订单")
    assert "第二版知识" in second["knowledge"]
    assert "第一版知识" not in second["knowledge"]


def test_new_markdown_skill_can_join_the_ring(tmp_path: Path):
    _write_skill(tmp_path, "只记录，不设计。", ring=True)
    engine = _engine(tmp_path)
    view = engine.run("用户可以创建订单。需要登录。")
    assert view["status"] == "COMPLETED"
    assert [step["agentId"] for step in view["steps"]] == ["note_taker"]
    assert "只记录，不设计。" in view["skillKnowledge"]["note_taker"]["knowledge"]


def test_skill_outside_the_ring_is_not_started_by_the_workflow(tmp_path: Path):
    _write_skill(tmp_path, "单独使用。", ring=False)
    engine = _engine(tmp_path)
    with pytest.raises(WorkflowError) as caught:
        engine.run("用户可以创建订单。需要登录。", entry_agent_id="note_taker")
    assert caught.value.code == ErrorCode.INVALID_ARGUMENT


def test_script_replaces_builtin_behavior_and_reloads(tmp_path: Path):
    folder = _write_skill(tmp_path, "脚本可读的知识", ring=False)
    script = folder / "agent.py"
    script.write_text(
        "def execute(context):\n"
        "    return {\n"
        "        'summary': '第一版脚本',\n"
        "        'artifact': {'marker': context.knowledge},\n"
        "        'handoff': {'action': 'NEXT', 'reason': '脚本完成'},\n"
        "    }\n",
        encoding="utf-8",
    )
    engine = _engine(tmp_path)
    first = engine.invoke("note_taker", "用户可以创建订单")
    assert first["summary"] == "第一版脚本"
    assert first["artifact"]["marker"] == "脚本可读的知识"
    assert first["source"] == "script"

    script.write_text(
        "def execute(context):\n"
        "    return {\n"
        "        'summary': '第二版脚本',\n"
        "        'artifact': {'marker': 'v2'},\n"
        "        'handoff': {'action': 'NEXT', 'reason': '脚本完成'},\n"
        "    }\n",
        encoding="utf-8",
    )
    os.utime(script, (script.stat().st_atime + 5, script.stat().st_mtime + 5))
    second = engine.invoke("note_taker", "用户可以创建订单")
    assert second["summary"] == "第二版脚本"


def _engine(tmp_path: Path) -> WorkflowEngine:
    return WorkflowEngine(WorkflowStore(tmp_path / "data"), catalog=SkillCatalog(tmp_path / "skills"))


def _write_skill(tmp_path: Path, body: str, ring: bool) -> Path:
    folder = tmp_path / "skills" / "note_taker"
    folder.mkdir(parents=True, exist_ok=True)
    folder.joinpath("SKILL.md").write_text(
        "---\n"
        "name: note_taker\n"
        "title: 记录\n"
        "description: 把需求记下来。\n"
        "output: 记录\n"
        "order: 10\n"
        f"ring: {'true' if ring else 'false'}\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return folder
