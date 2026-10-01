import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from services.ticket_search_service import transport_section_from_bundle


class TicketSearchServiceTests(unittest.TestCase):
    def test_realtime_train_option_keeps_each_seat_class_and_remaining_tickets(self):
        section = transport_section_from_bundle(
            {
                "direct_source": "12306-mcp get-tickets",
                "direct_rows": [{
                    "trip_no": "G123",
                    "route": "上海虹桥 -> 南京南",
                    "depart": "2026-08-01 08:00",
                    "arrive": "2026-08-01 09:42",
                    "duration": "1小时42分",
                    "seat": "二等座",
                    "price": "¥157",
                    "note": "余票:18",
                    "seat_options": [
                        {"name": "商务座", "remaining_text": "无", "price": "¥498"},
                        {"name": "一等座", "remaining_text": "2", "price": "¥251"},
                        {"name": "二等座", "remaining_text": "18", "price": "¥157"},
                    ],
                }],
            },
            direction="outbound",
            scope="domestic",
            travel_date="2026-08-01",
        )

        option = section["options"][0]
        self.assertEqual(option["duration_minutes"], 102)
        self.assertEqual(option["availability"], "available")
        self.assertEqual(
            option["seat_options"],
            [
                {"name": "商务座", "availability": "unavailable", "remaining_text": "无", "price": 498.0, "currency": "CNY"},
                {"name": "一等座", "availability": "limited", "remaining_text": "2", "price": 251.0, "currency": "CNY"},
                {"name": "二等座", "availability": "available", "remaining_text": "18", "price": 157.0, "currency": "CNY"},
            ],
        )

    def test_no_realtime_result_keeps_route_guidance_and_official_query_link(self):
        section = transport_section_from_bundle(
            None,
            direction="outbound",
            scope="domestic",
            travel_date="2026-08-01",
            fallback_guidance="暂无可靠实时票务数据。可先按“上海 → 南京”比较交通方式。",
        )

        self.assertEqual(section["options"], [])
        self.assertIn("上海 → 南京", section["status_reason"])
        self.assertEqual(section["official_query_url"], "https://www.12306.cn/index/")


if __name__ == "__main__":
    unittest.main()
