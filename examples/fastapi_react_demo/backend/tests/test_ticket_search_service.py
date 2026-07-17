from services.ticket_search_service import transport_section_from_bundle


def test_transport_section_filters_exact_date_and_keeps_reference_data_label():
    bundle = {
        "flight_source": "web search reference",
        "flight_rows": [
            {
                "trip_no": "CA1001",
                "route": "北京 -> 杭州",
                "depart": "2026-08-01 08:00",
                "arrive": "2026-08-01 10:00",
                "duration": "2小时",
                "price": "¥680",
                "note": "页面参考价格",
            },
            {
                "trip_no": "CA1002",
                "route": "北京 -> 杭州",
                "depart": "2026-08-02 08:00",
                "arrive": "2026-08-02 10:00",
                "duration": "2小时",
                "price": "¥580",
            },
        ],
    }

    section = transport_section_from_bundle(bundle, "outbound", "domestic", "2026-08-01")

    assert section["status"] == "ready"
    assert len(section["options"]) == 1
    assert section["options"][0]["service_number"] == "CA1001"
    assert section["options"][0]["data_type"] == "reference_data"
    assert section["options"][0]["duration_minutes"] == 120


def test_transport_section_marks_official_api_as_confirmed_and_is_stable():
    bundle = {
        "direct_source": "12306 official API",
        "direct_rows": [{
            "train_code": "G101",
            "route": "北京南 -> 杭州东",
            "depart": "2026-08-01 07:00",
            "arrive": "2026-08-01 12:00",
            "duration": "5小时",
            "price": 626,
            "note": "有票",
        }],
    }

    first = transport_section_from_bundle(bundle, "outbound", "domestic", "2026-08-01")
    second = transport_section_from_bundle(bundle, "outbound", "domestic", "2026-08-01")

    assert first["options"][0]["data_type"] == "confirmed_live_data"
    assert first["options"][0]["option_id"] == second["options"][0]["option_id"]


def test_international_transport_never_exposes_domestic_modes():
    section = transport_section_from_bundle(None, "return", "international", "2026-08-03")

    assert section["supported_modes"] == ["flight"]
    assert section["status"] == "needs_confirmation"
