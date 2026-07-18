from __future__ import annotations

from typing import Any, Dict, Iterable, List


class PlanningEventStore:
    """Five-minute planning event log backed by the trip repository."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def append(self, run_id: str, event: Dict[str, Any]) -> bool:
        sequence = int(event.get("sequence") or 0)
        if sequence < 1:
            raise ValueError("规划事件 sequence 必须从 1 开始")
        event_id = str(event.get("event_id") or f"{run_id}:{sequence}")
        occurred_at = event.get("occurred_at")
        return bool(
            self.repository.append_run_event(
                run_id,
                sequence,
                event_id,
                event,
                occurred_at,
            )
        )

    def replay(self, run_id: str, after_sequence: int = 0) -> List[Dict[str, Any]]:
        rows = self.repository.recent_run_events(run_id, max(0, int(after_sequence)))
        return self.dedupe(row["event"] for row in rows)

    @staticmethod
    def dedupe(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen_ids: set[str] = set()
        seen_sequences: set[int] = set()
        result: List[Dict[str, Any]] = []
        for event in sorted(events, key=lambda item: int(item.get("sequence") or 0)):
            sequence = int(event.get("sequence") or 0)
            event_id = str(event.get("event_id") or "")
            if sequence < 1 or sequence in seen_sequences or (event_id and event_id in seen_ids):
                continue
            seen_sequences.add(sequence)
            if event_id:
                seen_ids.add(event_id)
            result.append(event)
        return result

    def clear_run(self, device_id: str, run_id: str) -> int:
        return self.repository.clear_run_events(device_id, run_id)
