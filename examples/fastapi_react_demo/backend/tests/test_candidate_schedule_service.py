import unittest

from backend.services.candidate_schedule_service import _day_operating_bounds, refresh_candidate_schedule_options


def _place(poi_id: str, latitude: float, longitude: float, *, opening_hours: str = "09:00-18:00") -> dict:
    return {
        "poi_id": poi_id,
        "name": poi_id,
        "opening_hours": opening_hours,
        "coordinates": {"latitude": latitude, "longitude": longitude, "coordinate_system": "WGS84"},
    }


def _document() -> dict:
    return {
        "intent": {"days": 2},
        "budget": {"total_budget": {"amount": 500, "currency": "CNY"}},
        "itinerary": {
            "days": [
                {
                    "day": 1,
                    "activities": [
                        {
                            "activity_id": "formal_1",
                            "start_at": "10:00",
                            "end_at": "12:00",
                            "place": _place("formal_1", 30.25, 120.15),
                            "estimated_cost": {"amount": 100, "currency": "CNY"},
                        },
                    ],
                },
                {"day": 2, "activities": []},
            ],
        },
        "candidate_pool": [
            {
                "activity_id": "candidate_1",
                "kind": "attraction",
                "title": "候选一",
                "duration_minutes": 90,
                "place": _place("candidate_1", 30.251, 120.151),
                "estimated_cost": {"amount": 80, "currency": "CNY"},
            },
        ],
    }


class CandidateScheduleServiceTests(unittest.TestCase):
    def test_transport_bounds_use_structured_local_time_and_keep_closed_day_empty(self):
        document = _document()
        document["intent"]["date_mode"] = "fixed"
        document["return_transport"] = {"selected_option_id": "return", "options": [{
            "option_id": "return", "departure_time": {
                "local_iso": "2026-10-02T01:55:00+08:00", "display_text": "2026-10-02 21:55 北京时间",
            },
        }]}
        document["itinerary"]["days"][1]["date"] = "2026-10-02"
        self.assertEqual((540, 115), _day_operating_bounds(document, 2))
        refresh_candidate_schedule_options(document)
        self.assertFalse(any(option["day"] == 2 for option in document["candidate_pool"][0]["insertion_options"]))
        document["return_transport"]["options"][0]["departure_time"]["local_iso"] = "2026-10-02T00:00:00+08:00"
        self.assertEqual((540, 0), _day_operating_bounds(document, 2))

    def test_late_arrival_and_same_day_return_both_constrain_candidate_slots(self):
        document = _document()
        document["intent"]["days"] = 1
        document["itinerary"]["days"] = [{"day": 1, "date": "2026-10-02", "activities": []}]
        document["candidate_pool"][0]["place"]["opening_hours"] = "全天"
        document["outbound_transport"] = {"selected_option_id": "out", "options": [{
            "option_id": "out", "arrival_time": {"local_iso": "2026-10-02T18:40:00+08:00", "display_text": "09:00"},
        }]}
        document["return_transport"] = {"selected_option_id": "back", "options": [{
            "option_id": "back", "departure_time": {"local_iso": "2026-10-02T21:00:00+08:00", "display_text": "22:00"},
        }]}
        refresh_candidate_schedule_options(document)
        self.assertEqual([{"day": 1, "start_at": "18:45", "end_at": "20:15", "position": 0}],
                         document["candidate_pool"][0]["insertion_options"])
        document["outbound_transport"]["options"][0]["arrival_time"]["local_iso"] = "2026-10-02T23:15:00+08:00"
        refresh_candidate_schedule_options(document)
        self.assertEqual([], document["candidate_pool"][0]["insertion_options"])

    def test_legacy_display_clock_and_flexible_dates(self):
        document = _document()
        document["return_transport"] = {"selected_option_id": "return", "options": [{
            "option_id": "return", "departure_time": {"display_text": "2026-10-02 01:55 北京时间"},
        }]}
        self.assertEqual((540, 115), _day_operating_bounds(document, 2))
        document["return_transport"]["options"][0]["departure_time"]["display_text"] = "2026-10-02 待确认"
        self.assertEqual((540, 1320), _day_operating_bounds(document, 2))
        document["return_transport"]["options"][0]["departure_time"]["local_iso"] = "2026-10-02T01:55:00+08:00"
        document["intent"]["date_mode"] = "flexible"
        refresh_candidate_schedule_options(document)
        self.assertEqual((540, 1320), _day_operating_bounds(document, 2))
        self.assertTrue(any(option["day"] == 2 for option in document["candidate_pool"][0]["insertion_options"]))

    def test_existing_out_of_window_activities_cannot_reopen_departure_day(self):
        document = _document()
        document["itinerary"]["days"][1]["activities"] = [{
            "start_at": "16:00", "end_at": "17:00", "place": _place("later", 30.25, 120.15),
        }]
        document["return_transport"] = {"selected_option_id": "return", "options": [{
            "option_id": "return", "departure_time": {"local_iso": "2026-10-02T10:00:00+08:00"},
        }]}
        refresh_candidate_schedule_options(document)
        self.assertFalse(any(option["day"] == 2 for option in document["candidate_pool"][0]["insertion_options"]))

    def test_offers_only_opening_hour_and_gap_compatible_slots(self):
        document = _document()

        refresh_candidate_schedule_options(document)

        options = document["candidate_pool"][0]["insertion_options"]
        self.assertEqual(
            [
                {"day": 1, "start_at": "12:00", "end_at": "13:30", "position": 1},
                {"day": 2, "start_at": "09:00", "end_at": "10:30", "position": 0},
            ],
            options,
        )

    def test_keeps_candidate_and_explains_when_budget_or_hours_block_insertion(self):
        document = _document()
        document["budget"]["total_budget"]["amount"] = 150

        refresh_candidate_schedule_options(document)

        candidate = document["candidate_pool"][0]
        self.assertEqual([], candidate["insertion_options"])
        self.assertEqual("预算余量不足", candidate["insertion_unavailable_reason"])

        document = _document()
        document["candidate_pool"][0]["place"]["opening_hours"] = "周一闭馆"
        refresh_candidate_schedule_options(document)
        self.assertEqual("营业时间待核验，暂不自动排入", document["candidate_pool"][0]["insertion_unavailable_reason"])

    def test_rejects_slot_when_neighbouring_place_is_too_far(self):
        document = _document()
        document["candidate_pool"][0]["place"]["coordinates"]["latitude"] = 31.4
        document["candidate_pool"][0]["place"]["coordinates"]["longitude"] = 121.5
        document["itinerary"]["days"] = document["itinerary"]["days"][:1]
        document["intent"]["days"] = 1

        refresh_candidate_schedule_options(document)

        candidate = document["candidate_pool"][0]
        self.assertEqual([], candidate["insertion_options"])
        self.assertIn("相邻地点距离过远", candidate["insertion_unavailable_reason"])


if __name__ == "__main__":
    unittest.main()
