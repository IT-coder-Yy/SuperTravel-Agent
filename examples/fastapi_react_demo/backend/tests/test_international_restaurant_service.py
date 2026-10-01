import unittest
import importlib
from unittest.mock import Mock, patch

import requests

from backend.services.international_restaurant_service import (
    _fetch_nominatim_payload,
    fetch_osm_restaurants,
    request_nominatim_places,
)


class InternationalRestaurantServiceTests(unittest.TestCase):
    def setUp(self):
        _fetch_nominatim_payload.cache_clear()

    def tearDown(self):
        _fetch_nominatim_payload.cache_clear()

    @staticmethod
    def restaurant(osm_id=1):
        return {"osm_type": "node", "osm_id": osm_id, "lat": "35.6727104", "lon": "139.7640108",
                "category": "amenity", "type": "restaurant", "name": f"Tokyo Restaurant {osm_id}",
                "display_name": f"Tokyo Restaurant {osm_id}, Tokyo, Japan", "address": {"city": "Tokyo", "country_code": "jp"}}

    @patch("backend.services.international_restaurant_service.requests.get")
    def test_failed_requests_and_invalid_payloads_are_not_cached(self, get_mock):
        for failure in [requests.Timeout(), requests.HTTPError(), {"error": "temporary provider failure"}]:
            with self.subTest(failure=type(failure).__name__):
                _fetch_nominatim_payload.cache_clear()
                failed_response = Mock()
                failed_response.json.return_value = failure
                response = Mock()
                response.json.return_value = [self.restaurant()]
                get_mock.reset_mock()
                get_mock.side_effect = [failure if isinstance(failure, Exception) else failed_response, response]
                self.assertEqual([], fetch_osm_restaurants("东京", limit=20))
                self.assertEqual(1, len(fetch_osm_restaurants("东京", limit=20)))
                self.assertEqual(1, len(fetch_osm_restaurants("东京", limit=20)))
                self.assertEqual(2, get_mock.call_count)

    @patch("backend.services.international_restaurant_service.requests.get")
    def test_keeps_twenty_real_pois_and_rejects_wrong_city_and_coordinates(self, get_mock):
        response = Mock()
        valid = [self.restaurant(index) for index in range(1, 21)]
        response.json.return_value = [
            {**self.restaurant(100), "address": {"city": "Yokohama", "country_code": "jp"}, "display_name": "Tokyo Restaurant, Yokohama, Japan"},
            {**self.restaurant(101), "lat": "34.6937", "lon": "135.5023"},
            {**self.restaurant(102), "lat": "NaN"},
            *valid,
        ]
        get_mock.return_value = response
        items = fetch_osm_restaurants("东京", limit=20)
        self.assertEqual(20, len(items))
        self.assertTrue(all(item["destination_bound"] and item["requested_destination"] == "东京" for item in items))
        self.assertFalse(any(item["poi_id"] in {"osm_node_100", "osm_node_101", "osm_node_102"} for item in items))
        self.assertEqual(20, get_mock.call_args.kwargs["params"]["limit"])

    def test_restaurant_and_hotel_requests_share_the_same_throttle(self):
        shared = importlib.import_module(request_nominatim_places.__module__)
        response = Mock()
        response.json.return_value = []
        with patch.object(shared, "_last_request_at", 100.0), \
             patch.object(shared.time, "monotonic", side_effect=[100.25, 100.25, 100.75, 100.75]), \
             patch.object(shared.time, "sleep") as sleep, \
             patch.object(shared.requests, "get", return_value=response):
            request_nominatim_places("restaurant", "东京", 20)
            request_nominatim_places("hotel", "东京", 10)
        self.assertEqual([0.75, 0.5], [call.args[0] for call in sleep.call_args_list])

    @patch("backend.services.international_restaurant_service.requests.get")
    def test_returns_only_destination_restaurant_pois_and_caches_query(self, get_mock):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {
                "osm_type": "node",
                "osm_id": 5003892422,
                "lat": "35.6727104",
                "lon": "139.7640108",
                "type": "restaurant",
                "name": "Sukiyabashi Jiro",
                "display_name": "Sukiyabashi Jiro, Tokyo, Japan",
                "address": {"country_code": "jp", "amenity": "Sukiyabashi Jiro"},
                "extratags": {"opening_hours": "11:30-20:30"},
                "namedetails": {"name:en": "Sukiyabashi Jiro"},
            },
            {
                "osm_type": "node",
                "osm_id": 2,
                "lat": "35.0",
                "lon": "139.0",
                "type": "museum",
                "name": "Not Food",
                "address": {"country_code": "jp"},
            },
            {
                "osm_type": "node",
                "osm_id": 3,
                "lat": "37.0",
                "lon": "127.0",
                "type": "restaurant",
                "name": "Wrong Country",
                "address": {"country_code": "kr"},
            },
        ]
        get_mock.return_value = response

        first = fetch_osm_restaurants("东京", limit=10)
        second = fetch_osm_restaurants("东京", limit=10)

        self.assertEqual(1, len(first))
        self.assertEqual("餐厅", first[0]["category"])
        self.assertEqual("osm_node_5003892422", first[0]["poi_id"])
        self.assertEqual("WGS84", first[0]["coordinate_system"])
        self.assertEqual(first, second)
        get_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
