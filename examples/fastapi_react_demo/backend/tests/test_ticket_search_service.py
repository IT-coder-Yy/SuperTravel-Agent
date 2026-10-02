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


def test_planning_chooses_daytime_outbound_and_evening_return_instead_of_cheapest_night_train():
    def row(code, departure, arrival, price):
        return {'trip_no': code, 'route': '上海 -> 杭州', 'depart': '2026-10-10 ' + departure,
                'arrive': '2026-10-10 ' + arrival, 'price': price, 'duration': '1小时'}
    for direction, rows, expected in (
        ('outbound', [row('night', '04:00', '05:00', 20), row('morning', '08:00', '09:00', 40), row('late', '18:00', '19:00', 10)], 'morning'),
        ('return', [row('early', '01:55', '03:00', 0), row('noon', '12:00', '13:00', 20), row('evening', '20:00', '21:00', 40)], 'evening'),
    ):
        section = transport_section_from_bundle({'direct_rows': rows, 'direct_source': '12306'}, direction, 'domestic', '2026-10-10')
        selected = next(item for item in section['options'] if item['option_id'] == section['recommended_option_id'])
        assert selected['service_number'] == expected
        assert len(section['options']) == len(rows), '原始可选班次必须保留'


def test_zero_ticket_quote_is_unknown_for_both_option_and_seat():
    section = transport_section_from_bundle({'direct_source': '12306', 'direct_rows': [{
        'trip_no': 'K528', 'depart': '2026-10-10 01:55', 'arrive': '2026-10-10 03:46',
        'price': '0.0', 'seat_options': [{'name': '硬座', 'remaining_text': '有', 'price': '¥0'}],
    }]}, 'return', 'domestic', '2026-10-10')
    assert section['options'][0]['price'] is None
    assert section['options'][0]['seat_options'][0]['price'] is None
