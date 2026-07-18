import asyncio
import unittest
from unittest.mock import patch

from backend.services.provider_gateway import (
    PROVIDER_TIMEOUT_SECONDS,
    ProviderAttemptLimitExceeded,
    ProviderCallTimeout,
    ProviderGateway,
    SourceFact,
    realtime_claim_or_confirmation,
    resolve_source_fact,
)


class ProviderGatewayTests(unittest.IsolatedAsyncioTestCase):
    def test_same_provider_fingerprint_allows_at_most_three_primary_attempts(self):
        gateway = ProviderGateway()

        def fail():
            raise RuntimeError("provider failed")

        for _ in range(3):
            with self.assertRaises(RuntimeError):
                gateway.execute(
                    fail,
                    scope_id="run-1",
                    provider="tickets",
                    operation="search",
                    arguments={"query": "Shanghai Beijing"},
                )
        with self.assertRaises(ProviderAttemptLimitExceeded):
            gateway.execute(
                fail,
                scope_id="run-1",
                provider="tickets",
                operation="search",
                arguments={"query": "Shanghai Beijing"},
            )
        self.assertEqual(gateway.metrics_snapshot()["primary_attempts"], 3)

    def test_fallback_provider_is_allowed_only_once_per_fingerprint(self):
        gateway = ProviderGateway()
        fallback_calls = 0

        def fail():
            raise RuntimeError("primary failed")

        def fallback():
            nonlocal fallback_calls
            fallback_calls += 1
            return "fallback-result"

        self.assertEqual(
            gateway.execute(
                fail,
                scope_id="run-1",
                provider="primary",
                operation="search",
                arguments={"query": "same"},
                fallback_provider="backup",
                fallback_callback=fallback,
            ),
            "fallback-result",
        )
        with self.assertRaises(ProviderAttemptLimitExceeded):
            gateway.execute(
                fail,
                scope_id="run-1",
                provider="primary",
                operation="search",
                arguments={"query": "same"},
                fallback_provider="backup",
                fallback_callback=fallback,
            )
        self.assertEqual(fallback_calls, 1)
        self.assertEqual(gateway.metrics_snapshot()["fallback_attempts"], 1)

    async def test_async_provider_timeout_uses_timeout_class(self):
        gateway = ProviderGateway()

        async def slow():
            await asyncio.sleep(0.05)

        with patch.dict(PROVIDER_TIMEOUT_SECONDS, {"quick_tool": 0.01}):
            with self.assertRaises(ProviderCallTimeout):
                await gateway.execute_async(
                    slow,
                    scope_id="run-timeout",
                    provider="weather",
                    operation="current",
                )
        self.assertEqual(gateway.metrics_snapshot()["timeouts"], 1)

    def test_source_priority_and_equal_priority_conflict_are_deterministic(self):
        resolved = resolve_source_fact(
            [
                SourceFact("opening_hours", "09:00-17:00", "guide_reference", "guide"),
                SourceFact("opening_hours", "08:30-17:30", "map_reference", "baidu map"),
                SourceFact("opening_hours", "闭馆", "official_reference", "official site"),
            ]
        )
        self.assertEqual(resolved.value, "闭馆")
        self.assertEqual(resolved.status, "official_reference")
        self.assertEqual(len(resolved.conflicts), 2)

        conflict = resolve_source_fact(
            [
                SourceFact("availability", "有票", "realtime_verified", "provider-a"),
                SourceFact("availability", "无票", "realtime_verified", "provider-b"),
            ]
        )
        self.assertIsNone(conflict.value)
        self.assertEqual(conflict.status, "user_confirmation_required")

    def test_unverified_realtime_claim_returns_official_entry_and_confirmation(self):
        result = realtime_claim_or_confirmation(
            SourceFact("availability", "可能有票", "guide_reference", "guide"),
            official_url="https://official.example/search",
        )
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "user_confirmation_required")
        self.assertEqual(result.official_url, "https://official.example/search")


if __name__ == "__main__":
    unittest.main()
