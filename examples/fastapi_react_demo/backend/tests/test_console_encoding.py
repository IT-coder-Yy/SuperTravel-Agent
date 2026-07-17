import io
import unittest
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.utils.console import configure_utf8_stream, safe_print


class Cp936Stream:
    encoding = "gbk"

    def __init__(self):
        self.value = ""

    def write(self, value):
        value.encode("gbk")
        self.value += value

    def flush(self):
        return None


class ReconfigurableStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.settings = None

    def reconfigure(self, **kwargs):
        self.settings = kwargs


class ConsoleEncodingTests(unittest.TestCase):
    def test_safe_print_does_not_crash_on_gbk_emoji(self):
        stream = Cp936Stream()
        safe_print("🚀 服务启动", file=stream)
        self.assertIn("服务启动", stream.value)

    def test_configure_requests_utf8_and_replacement(self):
        stream = ReconfigurableStream()
        configure_utf8_stream(stream)
        self.assertEqual(stream.settings, {"encoding": "utf-8", "errors": "replace"})


if __name__ == "__main__":
    unittest.main()
