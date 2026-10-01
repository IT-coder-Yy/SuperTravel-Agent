import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.text_sanitizer_service import sanitize_user_visible_payload, sanitize_user_visible_text


class TextSanitizerServiceTests(unittest.TestCase):
    def test_redacts_inline_local_paths_and_credentials(self):
        fake_token = "sk-" + "example1234567890"
        content = (
            "文档已生成到 D:\\workspace\\outputs\\trip.md，"
            f"备用文件位于 /home/runner/output/trip.json，令牌 {fake_token}。"
        )

        sanitized = sanitize_user_visible_text(content)

        self.assertNotIn("D:\\workspace", sanitized)
        self.assertNotIn("/home/runner", sanitized)
        self.assertNotIn("sk-example", sanitized)
        self.assertEqual(sanitized.count("[内部路径已隐藏]"), 2)
        self.assertIn("[敏感凭据已隐藏]", sanitized)

    def test_redacts_named_credentials_and_url_userinfo(self):
        named_secret = "fixture_secret_value_123456"
        basic_token = "Zml4dHVyZTpzZWNyZXQ="
        content = "\n".join(
            [
                f"api_key={named_secret}",
                f"X-API-Key: {named_secret}",
                f"Authorization: Basic {basic_token}",
                "请访问 https://fixture:secret@example.com/guide。",
            ]
        )

        sanitized = sanitize_user_visible_text(content)

        self.assertNotIn(named_secret, sanitized)
        self.assertNotIn(basic_token, sanitized)
        self.assertNotIn("fixture:secret@", sanitized)
        self.assertEqual(sanitized.count("[敏感凭据已隐藏]"), 4)
        self.assertEqual(
            sanitize_user_visible_text(
                f'```json\n{{"api_key":"{named_secret}"}}\n```'
            ),
            "",
        )

    def test_removes_raw_tool_lines_and_debug_trace(self):
        content = "\n".join(
            [
                "查询已完成。",
                '[map_weather] result: {"city": "Beijing"}',
                'File "D:/app/service.py", line 20',
                "请出发前再次核对天气。",
            ]
        )

        sanitized = sanitize_user_visible_text(content)

        self.assertEqual(sanitized, "查询已完成。\n请出发前再次核对天气。")

    def test_removes_internal_json_but_preserves_curated_map_json(self):
        internal = """```json
{"tool_call_id":"call-1","arguments":{"city":"北京"}}
```"""
        curated = """```json
{"map_locations":[{"name":"故宫","lat":39.9,"lng":116.4}]}
```"""

        self.assertEqual(sanitize_user_visible_text(internal), "")
        self.assertEqual(sanitize_user_visible_text(curated), curated)

    def test_removes_unfenced_internal_json_payload(self):
        content = '{"tool_name":"map_weather","stdout":"debug data"}'

        self.assertEqual(sanitize_user_visible_text(content), "")

    def test_replaces_internal_tool_names_with_user_facing_source_labels(self):
        content = "来源 serper_site_search 已检索，随后调用 baidu-map 进行地点核验。"

        sanitized = sanitize_user_visible_text(content)

        self.assertNotIn("serper_site_search", sanitized)
        self.assertNotIn("baidu-map", sanitized)
        self.assertIn("联网搜索", sanitized)
        self.assertIn("地图地点核验", sanitized)

    def test_sanitizes_structured_sources_without_breaking_display_fields(self):
        payload = {
            "type": "trip_sources",
            "sources": [
                {
                    "title": "杭州旅行参考",
                    "source": "serper_site_search",
                    "snippet": "来自 serper_site_search 的旅行建议",
                    "url": "https://example.com/hangzhou",
                    "source_tool": "serper_site_search",
                    "raw_payload": {"tool_name": "serper_site_search"},
                }
            ],
        }

        sanitized = sanitize_user_visible_payload(payload)
        source = sanitized["sources"][0]

        self.assertEqual(source["source"], "联网搜索")
        self.assertIn("联网搜索", source["snippet"])
        self.assertNotIn("source_tool", source)
        self.assertNotIn("raw_payload", source)
        self.assertEqual(source["url"], "https://example.com/hangzhou")

    def test_drops_sensitive_structured_fields_recursively(self):
        payload = {
            "title": "杭州旅行参考",
            "api_key": "fixture_secret_value_123456",
            "headers": {
                "Authorization": "Basic Zml4dHVyZTpzZWNyZXQ=",
                "X-API-Key": "fixture_secret_value_123456",
                "accept": "application/json",
            },
        }

        sanitized = sanitize_user_visible_payload(payload)

        self.assertEqual(sanitized, {"title": "杭州旅行参考", "headers": {"accept": "application/json"}})

    def test_keeps_valid_official_query_url_without_replacing_host_identifiers(self):
        sanitized = sanitize_user_visible_payload({
            "official_query_url": "https://www.12306.cn/index/",
            "source": "12306-mcp",
        })

        self.assertEqual(sanitized["official_query_url"], "https://www.12306.cn/index/")
        self.assertEqual(sanitized["source"], "铁路票务平台")


if __name__ == "__main__":
    unittest.main()
