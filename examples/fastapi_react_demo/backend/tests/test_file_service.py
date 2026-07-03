import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
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


class FileServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("fastapi") is None:
            raise unittest.SkipTest("fastapi is not installed in current environment")
        cls.file_service = importlib.import_module("services.file_service")
        cls.HTTPException = cls.file_service.HTTPException

    def test_build_download_response_safe_returns_inner_response(self):
        logger = FakeLogger()
        sentinel = object()

        with patch("services.file_service.build_download_response", return_value=sentinel) as mocked:
            result = self.file_service.build_download_response_safe("s1", "a.txt", logger)

        mocked.assert_called_once_with(session_id="s1", filename="a.txt")
        self.assertIs(result, sentinel)
        self.assertEqual(logger.errors, [])

    def test_build_download_response_safe_reraises_http_exception(self):
        logger = FakeLogger()

        with patch(
            "services.file_service.build_download_response",
            side_effect=self.HTTPException(status_code=404, detail="文件不存在"),
        ):
            with self.assertRaises(self.HTTPException) as ctx:
                self.file_service.build_download_response_safe("s1", "missing.txt", logger)

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "文件不存在")
        self.assertEqual(logger.errors, [])

    def test_build_download_response_safe_wraps_unexpected_error(self):
        logger = FakeLogger()

        with patch(
            "services.file_service.build_download_response",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(self.HTTPException) as ctx:
                self.file_service.build_download_response_safe("s1", "oops.txt", logger)

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("下载文件失败: boom", ctx.exception.detail)
        self.assertEqual(len(logger.errors), 1)


if __name__ == "__main__":
    unittest.main()
