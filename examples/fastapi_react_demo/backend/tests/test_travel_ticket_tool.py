import json
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

from agents.tool.travel_ticket_tool import TravelTicketTool
from agents.tool.tool_manager import ToolManager


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self.payload = payload
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class TravelTicketToolTests(unittest.TestCase):
    def test_tool_registers_flight_and_bus_query_methods(self):
        tool = TravelTicketTool()

        self.assertIn("query_flight_tickets", tool.tools)
        self.assertIn("query_bus_tickets", tool.tools)

    def test_tool_manager_executes_bound_flight_method(self):
        html = """
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"initialState":{"flights":[{"flightNo":"CZ3907","airlineName":"南航","departureAirportName":"广州白云","arrivalAirportName":"桂林两江","departureDate":"2026-05-28","departureTime":"08:00","arrivalDate":"2026-05-28","arrivalTime":"09:30","duration":"1小时30分钟","cabinClass":"经济舱","price":"580"}]}}}}
        </script>
        """
        manager = ToolManager(is_auto_discover=False)
        manager.register_tool_class(TravelTicketTool)

        with patch.dict("os.environ", {}, clear=True):
            with patch("agents.tool.travel_ticket_tool.requests.get", return_value=FakeResponse(text=html)):
                raw_result = manager.run_tool(
                    "query_flight_tickets",
                    messages=[],
                    session_id="s-1",
                    from_city="广州",
                    to_city="桂林",
                    travel_date="2026-05-28",
                )

        result = json.loads(json.loads(raw_result)["content"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["flights"][0]["flightNo"], "CZ3907")

    def test_query_flight_tickets_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("agents.tool.travel_ticket_tool.requests.get", return_value=FakeResponse(text="<html></html>")):
                result = TravelTicketTool().query_flight_tickets(
                    from_city="广州",
                    to_city="桂林",
                    travel_date="2026-05-28",
                )

        self.assertTrue(result["error"])
        self.assertEqual(result["source"], "ctrip_flight_web")
        self.assertIn("未配置聚合数据航班接口 Key", result["message"])

    def test_query_flight_tickets_prefers_ctrip_web_without_api_key(self):
        html = """
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"initialState":{"flights":[{"flightNo":"CZ3907","airlineName":"南航","departureAirportName":"广州白云","arrivalAirportName":"桂林两江","departureDate":"2026-05-28","departureTime":"08:00","arrivalDate":"2026-05-28","arrivalTime":"09:30","duration":"1小时30分钟","cabinClass":"经济舱","price":"580"}]}}}}
        </script>
        """

        with patch.dict("os.environ", {}, clear=True):
            with patch("agents.tool.travel_ticket_tool.requests.get", return_value=FakeResponse(text=html)):
                result = TravelTicketTool().query_flight_tickets(
                    from_city="广州",
                    to_city="桂林",
                    travel_date="2026-05-28",
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], "ctrip_flight_web")
        self.assertEqual(result["flights"][0]["flightNo"], "CZ3907")
        self.assertEqual(result["flights"][0]["price"], "580")

    def test_query_flight_tickets_calls_juhe_and_normalizes_rows(self):
        payload = {
            "error_code": 0,
            "reason": "success",
            "result": {
                "flights": [
                    {
                        "flightNo": "CZ3907",
                        "airlineName": "南航",
                        "departureAirportName": "广州白云",
                        "arrivalAirportName": "桂林两江",
                        "departureDate": "2026-05-28",
                        "departureTime": "08:00",
                        "arrivalDate": "2026-05-28",
                        "arrivalTime": "09:30",
                        "duration": "1小时30分钟",
                        "cabinClass": "经济舱",
                        "price": "580",
                    }
                ]
            },
        }

        with patch.dict("os.environ", {"JUHE_FLIGHT_API_KEY": "test-key"}, clear=True):
            with patch(
                "agents.tool.travel_ticket_tool.requests.get",
                side_effect=[FakeResponse(text="<html></html>"), FakeResponse(payload=payload)],
            ) as mocked_get:
                result = TravelTicketTool().query_flight_tickets(
                    from_city="广州",
                    to_city="桂林",
                    travel_date="2026-05-28",
                    max_results=5,
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["query"]["departure"], "CAN")
        self.assertEqual(result["query"]["arrival"], "KWL")
        self.assertEqual(result["flights"][0]["flightNo"], "CZ3907")
        self.assertEqual(result["flights"][0]["fromAirport"], "广州白云")
        self.assertEqual(mocked_get.call_args_list[-1].kwargs["params"]["departure"], "CAN")

    def test_query_bus_tickets_calls_jisu_and_normalizes_rows(self):
        payload = {
            "status": "0",
            "msg": "ok",
            "result": [
                {
                    "startcity": "广州",
                    "endcity": "桂林",
                    "startstation": "省汽车站",
                    "endstation": "桂林汽车站",
                    "starttime": "09:00",
                    "price": "180",
                    "bustype": "大型高一",
                    "distance": "500",
                }
            ],
        }

        with patch.dict("os.environ", {"JISU_BUS_API_KEY": "test-key"}, clear=True):
            with patch(
                "agents.tool.travel_ticket_tool.requests.get",
                side_effect=[FakeResponse(text="<html></html>"), FakeResponse(payload=payload)],
            ) as mocked_get:
                result = TravelTicketTool().query_bus_tickets(
                    from_city="广州",
                    to_city="桂林",
                    travel_date="2026-05-28",
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["buses"][0]["from_station"], "省汽车站")
        self.assertEqual(result["buses"][0]["to_station"], "桂林汽车站")
        self.assertEqual(result["buses"][0]["depart_date"], "2026-05-28")
        self.assertIn("平台未返回余票", result["buses"][0]["note"])
        self.assertEqual(mocked_get.call_args_list[-1].kwargs["params"]["start"], "广州")


    def test_query_bus_tickets_prefers_ctrip_web_without_api_key(self):
        payload = {
            "props": {
                "pageProps": {
                    "initialState": {
                        "lines": [
                            {
                                "fromCityName": "\u5e7f\u5dde",
                                "toCityName": "\u535a\u7f57",
                                "fromStationName": "\u5929\u6cb3\u6c7d\u8f66\u5ba2\u8fd0\u7ad9",
                                "toStationName": "\u535a\u7f57\u9f99\u6eaa\u8def\u53e3",
                                "busNumber": "CSL19303",
                                "fullPrice": 46,
                                "fromDate": "2026-05-29",
                                "fromTime": "08:00",
                                "busType": "\u7a7a\u8c03/\u5750\u5e2d",
                                "bookInfo": {"bookable": "2"},
                            }
                        ]
                    }
                }
            }
        }
        html = (
            '<script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(payload)}"
            "</script>"
        )

        with patch.dict("os.environ", {}, clear=True):
            with patch("agents.tool.travel_ticket_tool.requests.get", return_value=FakeResponse(text=html)):
                result = TravelTicketTool().query_bus_tickets(
                    from_city="\u5e7f\u5dde",
                    to_city="\u535a\u7f57\u53bf",
                    travel_date="2026-05-29",
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], "ctrip_bus_web")
        self.assertEqual(result["buses"][0]["busNo"], "CSL19303")
        self.assertEqual(result["buses"][0]["from_station"], "\u5929\u6cb3\u6c7d\u8f66\u5ba2\u8fd0\u7ad9")
        self.assertEqual(result["buses"][0]["depart_date"], "2026-05-29")
        self.assertEqual(result["buses"][0]["price"], "46")
        self.assertIn("\u53ef\u9884\u8ba2", result["buses"][0]["note"])


if __name__ == "__main__":
    unittest.main()
