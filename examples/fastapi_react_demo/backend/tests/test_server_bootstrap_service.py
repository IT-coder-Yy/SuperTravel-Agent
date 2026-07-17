import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.server_bootstrap_service import run_backend_server


class ServerBootstrapServiceTests(unittest.TestCase):
    def test_run_backend_server_prints_banner_and_calls_runner(self):
        outputs = []
        calls = []

        app_config = SimpleNamespace(
            server=SimpleNamespace(
                host="0.0.0.0",
                port=8001,
                reload=True,
                log_level="info",
            )
        )

        def fake_loader():
            return app_config

        def fake_runner(app_target, host, port, reload, log_level):
            calls.append(
                {
                    "app_target": app_target,
                    "host": host,
                    "port": port,
                    "reload": reload,
                    "log_level": log_level,
                }
            )

        run_backend_server(
            config_loader=fake_loader,
            uvicorn_runner=fake_runner,
            printer=outputs.append,
            app_target="main:app",
        )

        self.assertEqual(len(outputs), 4)
        self.assertIn("启动 Sage Multi-Agent Framework 服务器", outputs[0])
        self.assertIn("http://127.0.0.1:8001", outputs[1])
        self.assertIn("/docs", outputs[2])
        self.assertIn("热重载: 开启", outputs[3])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["app_target"], "main:app")
        self.assertEqual(calls[0]["host"], "0.0.0.0")
        self.assertEqual(calls[0]["port"], 8001)
        self.assertEqual(calls[0]["reload"], True)
        self.assertEqual(calls[0]["log_level"], "info")


if __name__ == "__main__":
    unittest.main()
