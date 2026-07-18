import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.runtime_state_service import RuntimeState, create_runtime_state


class RuntimeStateServiceTests(unittest.TestCase):
    def test_create_runtime_state_defaults(self):
        state = create_runtime_state()

        self.assertIsInstance(state, RuntimeState)
        self.assertIsNone(state.tool_manager)
        self.assertIsNone(state.controller)
        self.assertEqual(state.active_sessions, {})
        self.assertEqual(state.baidu_request_dispatcher.max_concurrency, 1)

    def test_create_runtime_state_active_sessions_not_shared(self):
        state_a = create_runtime_state()
        state_b = create_runtime_state()

        state_a.active_sessions["session-1"] = {"x": 1}

        self.assertNotIn("session-1", state_b.active_sessions)
        self.assertIsNot(state_a.baidu_request_dispatcher, state_b.baidu_request_dispatcher)


if __name__ == "__main__":
    unittest.main()
