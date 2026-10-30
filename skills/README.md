# 技能目录

一个文件夹就是一个技能。进程每次调用都会重新读取这里的文件。

```text
skills/
  problem_clarifier/
    SKILL.md          知识库。改完，下一次调用就生效
    agent.py          可选。有这个文件时，用它代替内置规则
  my_reviewer/        新建文件夹即新增技能
    SKILL.md
```

`SKILL.md` 开头用这段信息头：

```yaml
---
name: my_reviewer
title: 代码审阅
description: 检查这次改动的正确性、安全和缺测。
output: 审阅意见
order: 35
ring: false
---
```

`name` 省略时用文件夹名。文件夹名只能是小写字母、数字和下划线，并且不能与 `run_workflow` 这类流程工具同名。

`ring: true` 会按 `order` 插进交接环。内置八个角色的顺序是 10、20、30、40、50、60、70、80，所以 35 会排在后端和 API 之间。`ring: false` 只在单独调用时出现，不改变原来的八步环。

没有 `agent.py` 的新技能，会把本文件全文交给 Cursor 里的模型，由它按知识库处理需求。

`agent.py` 要定义 `execute(context)`，返回字典：

```python
def execute(context):
    demand = context.profile.demand
    return {
        "summary": "已按脚本完成审阅。",
        "artifact": {"findings": [], "demand": demand},
        "handoff": {"action": "NEXT", "reason": "审阅完成", "target": None},
    }
```

`context.profile` 是需求画像，`context.artifacts` 是前面角色的产物，`context.knowledge` 是本文件正文。`handoff.action` 可以是 `NEXT`、`SKIP`、`WAIT`、`LOOP_BACK`、`COMPLETE`。`LOOP_BACK` 时 `target` 填要退回的技能名。`artifact` 必须能写成 JSON。

改 `SKILL.md` 或 `agent.py` 后，下一次 `invoke_skill`、同名工具或 `run_workflow` 就会用新内容。新文件夹要在 Cursor 的工具列表里显示同名工具时，重连一次 MCP。重连之前，用 `invoke_skill`，`skillId` 填文件夹名。
