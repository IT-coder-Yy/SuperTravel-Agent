import importlib
import importlib.util
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


class AppFactoryServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi is not installed in current environment")
        cls.app_factory_service = importlib.import_module("services.app_factory_service")

    def test_create_fastapi_app_builds_expected_metadata_and_cors(self):
        @asynccontextmanager
        async def fake_lifespan(_app):
            yield

        app = self.app_factory_service.create_fastapi_app(lifespan=fake_lifespan)

        self.assertEqual(app.title, "Sage Multi-Agent Framework")
        self.assertEqual(app.description, "现代化多智能体协作框架API")
        self.assertEqual(app.version, "0.8")

        cors_middlewares = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
        self.assertEqual(len(cors_middlewares), 1)

        options = getattr(cors_middlewares[0], "options", None)
        if options is None:
            options = getattr(cors_middlewares[0], "kwargs", {})
        self.assertEqual(
            options["allow_origins"],
            ["http://localhost:8080", "http://127.0.0.1:8080"],
        )
        self.assertEqual(options["allow_credentials"], True)
        self.assertEqual(options["allow_methods"], ["*"])
        self.assertEqual(options["allow_headers"], ["*"])


if __name__ == "__main__":
    unittest.main()
