import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _decode_sse_payload(chunk: Any) -> Optional[Dict[str, Any]]:
    if isinstance(chunk, bytes):
        text = chunk.decode("utf-8", errors="replace")
    else:
        text = str(chunk)
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def attach_persisted_planning_run(
    response: Any,
    *,
    repository: Any,
    device_id: str,
    trip_id: str,
    run_id: str,
) -> Any:
    """Persist public SSE events and release the device run lock on every exit path."""
    original_iterator = response.body_iterator

    async def persisted_iterator():
        terminal_status = "cancelled"
        completed_normally = False
        try:
            async for chunk in original_iterator:
                payload = _decode_sse_payload(chunk)
                if payload:
                    sequence = int(payload.get("sequence") or 0)
                    if sequence > 0:
                        repository.append_run_event(
                            run_id,
                            sequence,
                            f"{run_id}:{sequence}",
                            payload,
                            datetime.now(timezone.utc).isoformat(),
                        )
                    if payload.get("type") == "error":
                        terminal_status = "failed"
                    elif payload.get("type") == "chat_complete":
                        finish_reason = str(payload.get("finish_reason") or "")
                        terminal_status = "failed" if finish_reason == "failed" else "completed"
                yield chunk
            completed_normally = True
        except asyncio.CancelledError:
            terminal_status = "cancelled"
            raise
        except Exception:
            terminal_status = "failed"
            raise
        finally:
            if completed_normally and terminal_status == "cancelled":
                terminal_status = "failed"
            repository.finish_planning_run(device_id, run_id, terminal_status)

    response.body_iterator = persisted_iterator()
    return response
