import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


class FakeLogger:
    def __init__(self):
        self.errors = []

    def error(self, message):
        self.errors.append(message)


class FakeApp:
    def __init__(self):
        self.calls = []

    def mount(self, path, app, name=None):
        self.calls.append(
            {
                "path": path,
                "name": name,
                "directory": str(getattr(app, "directory", "")),
            }
        )


class SpaServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi is not installed in current environment")
        cls.spa_service = importlib.import_module("services.spa_service")
        cls.HTTPException = cls.spa_service.HTTPException

    def test_serve_root_with_boundary_returns_inner_response(self):
        logger = FakeLogger()
        sentinel = object()

        with patch("services.spa_service.serve_root_or_fallback", return_value=sentinel) as mocked:
            payload = self.spa_service.serve_root_with_boundary(Path("/tmp"), logger)

        self.assertIs(payload, sentinel)
        mocked.assert_called_once_with(static_path=Path("/tmp"))
        self.assertEqual(logger.errors, [])

    def test_serve_root_with_boundary_wraps_unexpected_error(self):
        logger = FakeLogger()

        with patch("services.spa_service.serve_root_or_fallback", side_effect=RuntimeError("boom")):
            with self.assertRaises(self.HTTPException) as ctx:
                self.spa_service.serve_root_with_boundary(Path("/tmp"), logger)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("根路径页面服务失败: boom", ctx.exception.detail)
        self.assertEqual(len(logger.errors), 1)

    def test_serve_spa_with_boundary_passthrough_http_exception(self):
        logger = FakeLogger()

        with patch(
            "services.spa_service.serve_spa_or_static",
            side_effect=self.HTTPException(status_code=404, detail="Not Found"),
        ):
            with self.assertRaises(self.HTTPException) as ctx:
                self.spa_service.serve_spa_with_boundary(Path("/tmp"), "api/status", logger)

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(logger.errors, [])

    def test_serve_spa_with_boundary_wraps_unexpected_error(self):
        logger = FakeLogger()

        with patch("services.spa_service.serve_spa_or_static", side_effect=RuntimeError("io-fail")):
            with self.assertRaises(self.HTTPException) as ctx:
                self.spa_service.serve_spa_with_boundary(Path("/tmp"), "plan", logger)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("SPA静态回退失败: io-fail", ctx.exception.detail)
        self.assertEqual(len(logger.errors), 1)

    def test_mount_static_assets_noop_when_static_missing(self):
        app = FakeApp()

        with TemporaryDirectory() as tmpdir:
            static_path = Path(tmpdir) / "missing-static"
            self.spa_service.mount_static_assets(app=app, static_path=static_path)

        self.assertEqual(app.calls, [])

    def test_mount_static_assets_mounts_assets_and_static(self):
        app = FakeApp()

        with TemporaryDirectory() as tmpdir:
            static_path = Path(tmpdir) / "static"
            assets_path = static_path / "assets"
            assets_path.mkdir(parents=True)

            self.spa_service.mount_static_assets(app=app, static_path=static_path)

        self.assertEqual(len(app.calls), 2)
        self.assertEqual(app.calls[0]["path"], "/assets")
        self.assertEqual(app.calls[0]["name"], "assets")
        self.assertEqual(app.calls[1]["path"], "/static")
        self.assertEqual(app.calls[1]["name"], "static")

    def test_mount_static_assets_mounts_static_without_assets(self):
        app = FakeApp()

        with TemporaryDirectory() as tmpdir:
            static_path = Path(tmpdir) / "static"
            static_path.mkdir(parents=True)

            self.spa_service.mount_static_assets(app=app, static_path=static_path)

        self.assertEqual(len(app.calls), 1)
        self.assertEqual(app.calls[0]["path"], "/static")
        self.assertEqual(app.calls[0]["name"], "static")

    def test_mount_static_assets_with_boundary_delegates_success(self):
        logger = FakeLogger()

        with patch("services.spa_service.mount_static_assets") as mocked:
            self.spa_service.mount_static_assets_with_boundary(
                app=object(),
                static_path=Path("/tmp/static"),
                logger=logger,
            )

        mocked.assert_called_once_with(app=unittest.mock.ANY, static_path=Path("/tmp/static"))
        self.assertEqual(logger.errors, [])

    def test_mount_static_assets_with_boundary_logs_and_reraises(self):
        logger = FakeLogger()

        with patch("services.spa_service.mount_static_assets", side_effect=RuntimeError("mount-fail")):
            with self.assertRaises(RuntimeError):
                self.spa_service.mount_static_assets_with_boundary(
                    app=object(),
                    static_path=Path("/tmp/static"),
                    logger=logger,
                )

        self.assertEqual(len(logger.errors), 1)
        self.assertIn("静态资源挂载失败: mount-fail", logger.errors[0])


if __name__ == "__main__":
    unittest.main()
