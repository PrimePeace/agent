"""会话既留在内存里，也原子写入磁盘，进程重启后还能 resume。文件名只接受 UUID。"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from programmer_workflow.errors import ErrorCode, WorkflowError
from programmer_workflow.session import WorkflowSession

logger = logging.getLogger(__name__)


class WorkflowStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._sessions: dict[str, WorkflowSession] = {}
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkflowError(ErrorCode.PERSISTENCE_FAILED, "无法创建会话目录", system=True) from exc
        self._load_existing()

    def insert(self, session: WorkflowSession) -> None:
        self.save(session)
        self._sessions[session.id] = session

    def save(self, session: WorkflowSession) -> None:
        document = session.to_document()
        target = self._directory / f"{session.id}.json"
        temporary = self._directory / f"{session.id}.json.tmp"
        try:
            temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, target)
        except OSError as exc:
            logger.exception("Failed to persist workflow session %s", session.id)
            raise WorkflowError(ErrorCode.PERSISTENCE_FAILED, "会话写入失败", system=True) from exc

    def require(self, session_id: str | None) -> WorkflowSession:
        identifier = _parse_identifier(session_id)
        session = self._sessions.get(str(identifier))
        if session is None:
            raise WorkflowError(ErrorCode.SESSION_NOT_FOUND, "会话不存在")
        return session

    def _load_existing(self) -> None:
        try:
            files = list(self._directory.glob("*.json"))
        except OSError as exc:
            raise WorkflowError(ErrorCode.PERSISTENCE_FAILED, "无法读取会话目录", system=True) from exc
        for path in files:
            self._load_one(path)

    def _load_one(self, path: Path) -> None:
        identifier = path.name[: -len(".json")]
        try:
            uuid.UUID(identifier)
            document = json.loads(path.read_text(encoding="utf-8"))
            session = WorkflowSession.restore(document)
            self._sessions[session.id] = session
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.warning("Skip unreadable workflow file %s", path.name)


def _parse_identifier(session_id: str | None) -> uuid.UUID:
    if session_id is None or not session_id.strip():
        raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "sessionId 不能为空")
    try:
        return uuid.UUID(session_id.strip())
    except ValueError as exc:
        raise WorkflowError(ErrorCode.INVALID_ARGUMENT, "sessionId 不是合法的 UUID") from exc
