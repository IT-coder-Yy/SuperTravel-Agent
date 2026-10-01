import builtins
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.agent.agent_base import AgentBase
from agents.utils.logger import Logger


class DummyAgent(AgentBase):
    def run_stream(self, messages, tool_manager=None, session_id=None, system_context=None):
        if False:
            yield []


class AgentBaseLoggingTests(unittest.TestCase):
    def test_system_message_and_token_stats_do_not_write_to_stdout(self):
        agent = DummyAgent(model=None, model_config={}, system_prefix="测试系统提示")

        with patch.object(builtins, "print", side_effect=OSError(233, "管道的另一端上无任何进程")) as print_mock:
            message = agent.prepare_unified_system_message(
                session_id="session-1",
                system_context={"destination": "杭州"},
            )
            agent.print_token_stats()

        self.assertEqual(message["role"], "system")
        self.assertIn("杭州", message["content"])
        print_mock.assert_not_called()

    def test_custom_logger_accepts_standard_formatting_and_exception_keywords(self):
        custom_logger = Logger()
        error = RuntimeError("provider unavailable")

        with patch.object(custom_logger.logger, "error") as error_mock:
            custom_logger.error(
                "model request failed: %s",
                "timeout",
                exc_info=error,
                stack_info=True,
                extra={"request_id": "request-1"},
            )

        args, kwargs = error_mock.call_args
        self.assertEqual(("model request failed: %s", "timeout"), args)
        self.assertIs(error, kwargs["exc_info"])
        self.assertTrue(kwargs["stack_info"])
        self.assertEqual("request-1", kwargs["extra"]["request_id"])
        self.assertIn("caller_filename", kwargs["extra"])
        self.assertIn("caller_lineno", kwargs["extra"])


if __name__ == "__main__":
    unittest.main()
