"""程序员交接环的业务错误。4xxx 调用方可以改参数，5xxx 是服务自身失败。"""

from __future__ import annotations


class ErrorCode:
    INVALID_ARGUMENT = "AGENT-4001"
    UNKNOWN_AGENT = "AGENT-4002"
    SESSION_NOT_FOUND = "AGENT-4004"
    SESSION_CLOSED = "AGENT-4091"
    DEMAND_REJECTED = "AGENT-4221"
    STEP_LIMIT = "AGENT-4222"
    PERSISTENCE_FAILED = "AGENT-5002"
    INTERNAL = "AGENT-5000"


class WorkflowError(Exception):
    """主动抛出的业务或系统错误。调用方必须转成工具错误，不能吞掉。"""

    def __init__(self, code: str, message: str, *, system: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.error_type = "SYSTEM" if system else "BUSINESS"
