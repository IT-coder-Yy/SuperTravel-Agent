"""Typed failures that may safely cross the planning SSE boundary."""

from __future__ import annotations

from typing import Iterable, Mapping


class PlanningPipelineError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        user_message: str,
        diagnostics: Iterable[Mapping[str, object]] = (),
        retryable: bool = True,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        self.diagnostics = [
            {
                "location": str(item.get("location") or "document"),
                "type": str(item.get("type") or "validation_error"),
                "message": str(item.get("message") or "文档字段校验失败"),
            }
            for item in diagnostics
        ][:8]
