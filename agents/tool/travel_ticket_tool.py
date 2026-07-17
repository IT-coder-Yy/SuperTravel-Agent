import datetime
import html
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from agents.tool.tool_base import ToolBase
from agents.utils.logger import logger


class TravelTicketTool(ToolBase):
    """Third-party realtime ticket lookup tool.

    Flight and coach ticket queries prefer no-login public web pages. Paid API
    providers are kept as fallback when web pages do not expose parseable data.
    """

    JUHE_FLIGHT_URL = "https://apis.juhe.cn/flight/query"
    JISU_BUS_URL = "https://api.jisuapi.com/bus/city2c"
    CTRIP_FLIGHT_URL = "https://flights.ctrip.com/online/list/oneway-{departure}-{arrival}?depdate={date}"
    CTRIP_BUS_URL = "https://bus.ctrip.com/schedule/{from_slug}-{to_slug}"

    WEB_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    CITY_AIRPORT_CODES = {
        "\u5317\u4eac": "BJS",
        "\u4e0a\u6d77": "SHA",
        "\u5e7f\u5dde": "CAN",
        "\u6df1\u5733": "SZX",
        "\u6842\u6797": "KWL",
        "\u676d\u5dde": "HGH",
        "\u6210\u90fd": "CTU",
        "\u91cd\u5e86": "CKG",
        "\u897f\u5b89": "SIA",
        "\u6606\u660e": "KMG",
        "\u6b66\u6c49": "WUH",
        "\u5357\u4eac": "NKG",
        "\u957f\u6c99": "CSX",
        "\u53a6\u95e8": "XMN",
        "\u9752\u5c9b": "TAO",
        "\u6d4e\u5357": "TNA",
        "\u90d1\u5dde": "CGO",
        "\u5929\u6d25": "TSN",
        "\u5927\u8fde": "DLC",
        "\u6c88\u9633": "SHE",
        "\u54c8\u5c14\u6ee8": "HRB",
        "\u957f\u6625": "CGQ",
        "\u6d77\u53e3": "HAK",
        "\u4e09\u4e9a": "SYX",
        "\u798f\u5dde": "FOC",
        "\u5357\u5b81": "NNG",
        "\u8d35\u9633": "KWE",
        "\u5408\u80a5": "HFE",
        "\u5357\u660c": "KHN",
        "\u592a\u539f": "TYN",
        "\u5170\u5dde": "LHW",
        "\u94f6\u5ddd": "INC",
        "\u547c\u548c\u6d69\u7279": "HET",
        "\u4e4c\u9c81\u6728\u9f50": "URC",
        "\u62c9\u8428": "LXA",
        "\u5b81\u6ce2": "NGB",
        "\u6e29\u5dde": "WNZ",
        "\u73e0\u6d77": "ZUH",
        "\u6e5b\u6c5f": "ZHA",
        "\u6c55\u5934": "SWA",
        "\u535a\u7f57": "HUZ",
        "\u60e0\u5dde": "HUZ",
        "\u4e1c\u4eac": "TYO",
        "Tokyo": "TYO",
        "\u4eac\u90fd": "KIX",
        "Kyoto": "KIX",
        "\u5927\u962a": "OSA",
        "Osaka": "OSA",
        "\u9996\u5c14": "SEL",
        "Seoul": "SEL",
        "\u65b0\u52a0\u5761": "SIN",
        "Singapore": "SIN",
        "\u66fc\u8c37": "BKK",
        "Bangkok": "BKK",
        "\u5409\u9686\u5761": "KUL",
        "Kuala Lumpur": "KUL",
        "\u5df4\u9ece": "PAR",
        "Paris": "PAR",
        "\u4f26\u6566": "LON",
        "London": "LON",
        "\u7f57\u9a6c": "ROM",
        "Rome": "ROM",
        "\u6089\u5c3c": "SYD",
        "Sydney": "SYD",
        "\u7ebd\u7ea6": "NYC",
        "New York": "NYC",
    }

    CITY_PINYIN_SLUGS = {
        "\u5317\u4eac": "beijing",
        "\u4e0a\u6d77": "shanghai",
        "\u5e7f\u5dde": "guangzhou",
        "\u6df1\u5733": "shenzhen",
        "\u6842\u6797": "guilin",
        "\u676d\u5dde": "hangzhou",
        "\u6210\u90fd": "chengdu",
        "\u91cd\u5e86": "chongqing",
        "\u897f\u5b89": "xian",
        "\u6606\u660e": "kunming",
        "\u6b66\u6c49": "wuhan",
        "\u5357\u4eac": "nanjing",
        "\u957f\u6c99": "changsha",
        "\u53a6\u95e8": "xiamen",
        "\u9752\u5c9b": "qingdao",
        "\u6d4e\u5357": "jinan",
        "\u90d1\u5dde": "zhengzhou",
        "\u5929\u6d25": "tianjin",
        "\u5927\u8fde": "dalian",
        "\u6c88\u9633": "shenyang",
        "\u54c8\u5c14\u6ee8": "haerbin",
        "\u957f\u6625": "changchun",
        "\u6d77\u53e3": "haikou",
        "\u4e09\u4e9a": "sanya",
        "\u798f\u5dde": "fuzhou",
        "\u5357\u5b81": "nanning",
        "\u8d35\u9633": "guiyang",
        "\u5408\u80a5": "hefei",
        "\u5357\u660c": "nanchang",
        "\u592a\u539f": "taiyuan",
        "\u5170\u5dde": "lanzhou",
        "\u94f6\u5ddd": "yinchuan",
        "\u547c\u548c\u6d69\u7279": "huhehaote",
        "\u4e4c\u9c81\u6728\u9f50": "wulumuqi",
        "\u62c9\u8428": "lasa",
        "\u5b81\u6ce2": "ningbo",
        "\u6e29\u5dde": "wenzhou",
        "\u73e0\u6d77": "zhuhai",
        "\u6e5b\u6c5f": "zhanjiang",
        "\u6c55\u5934": "shantou",
        "\u535a\u7f57": "boluo",
        "\u535a\u7f57\u53bf": "boluo",
        "\u60e0\u5dde": "huizhou",
    }

    def __init__(self):
        super().__init__()

    @ToolBase.tool()
    def query_flight_tickets(
        self,
        from_city: str,
        to_city: str,
        travel_date: str,
        max_results: int = 20,
        max_segments: int = 1,
    ) -> Dict[str, Any]:
        normalized_date = self._normalize_date(travel_date)
        departure = self._resolve_airport_code(from_city)
        arrival = self._resolve_airport_code(to_city)
        if not departure or not arrival:
            return self._error(
                source="ctrip_flight_web",
                message="\u65e0\u6cd5\u89e3\u6790\u57ce\u5e02\u673a\u573a\u4e09\u5b57\u7801\uff0c\u8bf7\u8865\u5145 CITY_AIRPORT_CODES \u6216\u76f4\u63a5\u4f20\u5165 IATA \u4e09\u5b57\u7801\u3002",
                query={"from_city": from_city, "to_city": to_city, "travel_date": normalized_date},
            )

        web_result = self._query_ctrip_flight_web(
            from_city=from_city,
            to_city=to_city,
            travel_date=normalized_date,
            departure=departure,
            arrival=arrival,
            max_results=max_results,
        )
        if web_result.get("status") == "success" and web_result.get("flights"):
            return web_result

        api_key = self._first_env("JUHE_FLIGHT_API_KEY", "JUHE_API_KEY")
        if not api_key:
            return self._error(
                source="ctrip_flight_web",
                message="\u643a\u7a0b\u673a\u7968\u7f51\u9875\u672a\u8fd4\u56de\u53ef\u89e3\u6790\u7ed3\u679c\uff0c\u4e14\u672a\u914d\u7f6e\u805a\u5408\u6570\u636e\u822a\u73ed\u63a5\u53e3 Key\u3002",
                query={"from_city": from_city, "to_city": to_city, "travel_date": normalized_date},
                raw_error=web_result if isinstance(web_result, dict) else None,
            )

        params = {
            "key": api_key,
            "departure": departure,
            "arrival": arrival,
            "departureDate": normalized_date,
            "flightNo": "",
            "maxSegments": str(max(0, int(max_segments))),
        }
        try:
            response = requests.get(self.JUHE_FLIGHT_URL, params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning(f"query_flight_tickets network failed: {exc}")
            return self._error(
                source="juhe_flight_query_api",
                message=f"\u805a\u5408\u6570\u636e\u822a\u73ed\u63a5\u53e3\u8bf7\u6c42\u5931\u8d25: {exc}",
                query={"from_city": from_city, "to_city": to_city, "travel_date": normalized_date},
            )

        error_code = payload.get("error_code") if isinstance(payload, dict) else None
        result = payload.get("result") if isinstance(payload, dict) else None
        if error_code not in (0, "0", None) or result in (None, "", []):
            return self._error(
                source="juhe_flight_query_api",
                message=self._safe_text(payload.get("reason")) if isinstance(payload, dict) else "\u805a\u5408\u6570\u636e\u822a\u73ed\u63a5\u53e3\u672a\u8fd4\u56de\u53ef\u7528\u7ed3\u679c\u3002",
                query={
                    "from_city": from_city,
                    "to_city": to_city,
                    "departure": departure,
                    "arrival": arrival,
                    "travel_date": normalized_date,
                },
                raw_error=payload if isinstance(payload, dict) else None,
            )

        raw_items = self._find_rows(result)
        flights = [
            row
            for row in (
                self._normalize_flight_item(item, from_city, to_city, normalized_date)
                for item in raw_items[: max(1, int(max_results))]
            )
            if row
        ]

        return {
            "status": "success",
            "source": "juhe_flight_query_api",
            "fallback_from": "ctrip_flight_web",
            "query": {
                "from_city": from_city,
                "to_city": to_city,
                "departure": departure,
                "arrival": arrival,
                "travel_date": normalized_date,
            },
            "total": len(flights),
            "flights": flights,
            "raw_total": len(raw_items),
        }

    @ToolBase.tool()
    def query_bus_tickets(
        self,
        from_city: str,
        to_city: str,
        travel_date: str,
        max_results: int = 20,
    ) -> Dict[str, Any]:
        normalized_date = self._normalize_date(travel_date)

        web_result = self._query_ctrip_bus_web(
            from_city=from_city,
            to_city=to_city,
            travel_date=normalized_date,
            max_results=max_results,
        )
        if web_result.get("status") == "success" and web_result.get("buses"):
            return web_result

        api_key = self._first_env("JISU_BUS_API_KEY", "JISU_API_KEY")
        if not api_key:
            return self._error(
                source="ctrip_bus_web",
                message="\u643a\u7a0b\u6c7d\u8f66\u7968\u7f51\u9875\u672a\u8fd4\u56de\u53ef\u89e3\u6790\u7ed3\u679c\uff0c\u4e14\u672a\u914d\u7f6e\u6781\u901f\u6570\u636e\u957f\u9014\u6c7d\u8f66\u63a5\u53e3 Key\u3002",
                query={"from_city": from_city, "to_city": to_city, "travel_date": normalized_date},
                raw_error=web_result if isinstance(web_result, dict) else None,
            )

        params = {
            "appkey": api_key,
            "start": self._clean_city_name(from_city),
            "end": self._clean_city_name(to_city),
        }
        try:
            response = requests.get(self.JISU_BUS_URL, params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.warning(f"query_bus_tickets network failed: {exc}")
            return self._error(
                source="jisu_bus_city2c_api",
                message=f"\u6781\u901f\u6570\u636e\u957f\u9014\u6c7d\u8f66\u63a5\u53e3\u8bf7\u6c42\u5931\u8d25: {exc}",
                query={"from_city": from_city, "to_city": to_city, "travel_date": normalized_date},
            )

        if str(payload.get("status")) != "0":
            return self._error(
                source="jisu_bus_city2c_api",
                message=self._safe_text(payload.get("msg")) or "\u6781\u901f\u6570\u636e\u957f\u9014\u6c7d\u8f66\u63a5\u53e3\u672a\u8fd4\u56de\u53ef\u7528\u7ed3\u679c\u3002",
                query={"from_city": from_city, "to_city": to_city, "travel_date": normalized_date},
                raw_error=payload,
            )

        result = payload.get("result")
        raw_items = result if isinstance(result, list) else []
        buses = [
            row
            for row in (
                self._normalize_bus_item(item, from_city, to_city, normalized_date)
                for item in raw_items[: max(1, int(max_results))]
            )
            if row
        ]

        return {
            "status": "success",
            "source": "jisu_bus_city2c_api",
            "fallback_from": "ctrip_bus_web",
            "query": {"from_city": from_city, "to_city": to_city, "travel_date": normalized_date},
            "total": len(buses),
            "buses": buses,
            "raw_total": len(raw_items),
        }

    def _query_ctrip_flight_web(
        self,
        from_city: str,
        to_city: str,
        travel_date: str,
        departure: str,
        arrival: str,
        max_results: int,
    ) -> Dict[str, Any]:
        url = self.CTRIP_FLIGHT_URL.format(
            departure=quote(departure),
            arrival=quote(arrival),
            date=quote(travel_date),
        )
        try:
            response = requests.get(url, headers=self.WEB_HEADERS, timeout=18)
            response.raise_for_status()
            next_data = self._extract_next_data(response.text)
            raw_items = self._find_rows(next_data)
            flights = [
                row
                for row in (
                    self._normalize_flight_item(item, from_city, to_city, travel_date)
                    for item in raw_items[: max(1, int(max_results))]
                )
                if row
            ]
            if not flights:
                return self._error(
                    source="ctrip_flight_web",
                    message="\u643a\u7a0b\u673a\u7968\u7f51\u9875\u672a\u8fd4\u56de\u53ef\u89e3\u6790\u822a\u73ed\u5217\u8868\u3002",
                    query={"from_city": from_city, "to_city": to_city, "travel_date": travel_date, "url": url},
                )
            return {
                "status": "success",
                "source": "ctrip_flight_web",
                "query": {
                    "from_city": from_city,
                    "to_city": to_city,
                    "departure": departure,
                    "arrival": arrival,
                    "travel_date": travel_date,
                    "url": url,
                },
                "total": len(flights),
                "flights": flights,
                "raw_total": len(raw_items),
            }
        except Exception as exc:
            logger.warning(f"ctrip flight web scrape failed: {exc}")
            return self._error(
                source="ctrip_flight_web",
                message=f"\u643a\u7a0b\u673a\u7968\u7f51\u9875\u6293\u53d6\u5931\u8d25: {exc}",
                query={"from_city": from_city, "to_city": to_city, "travel_date": travel_date, "url": url},
            )

    def _query_ctrip_bus_web(
        self,
        from_city: str,
        to_city: str,
        travel_date: str,
        max_results: int,
    ) -> Dict[str, Any]:
        from_slug = self._resolve_city_slug(from_city)
        to_slug = self._resolve_city_slug(to_city)
        if not from_slug or not to_slug:
            return self._error(
                source="ctrip_bus_web",
                message="\u65e0\u6cd5\u89e3\u6790\u643a\u7a0b\u6c7d\u8f66\u7968\u7f51\u9875\u57ce\u5e02 slug\u3002",
                query={"from_city": from_city, "to_city": to_city, "travel_date": travel_date},
            )

        url = self.CTRIP_BUS_URL.format(from_slug=quote(from_slug), to_slug=quote(to_slug))
        try:
            response = requests.get(url, headers=self.WEB_HEADERS, timeout=18)
            response.raise_for_status()
            next_data = self._extract_next_data(response.text)
            initial_state = (
                next_data.get("props", {}).get("pageProps", {}).get("initialState", {})
                if isinstance(next_data, dict)
                else {}
            )

            raw_items: List[Any] = []
            if isinstance(initial_state, dict):
                for key in ("lines", "displayLines", "list", "fromStations"):
                    value = initial_state.get(key)
                    if isinstance(value, list) and value:
                        raw_items = value
                        break
            if not raw_items:
                raw_items = self._find_bus_rows(next_data)

            buses = [
                row
                for row in (
                    self._normalize_bus_item(item, from_city, to_city, travel_date)
                    for item in raw_items[: max(1, int(max_results))]
                )
                if row
            ]
            if not buses:
                return self._error(
                    source="ctrip_bus_web",
                    message="\u643a\u7a0b\u6c7d\u8f66\u7968\u7f51\u9875\u672a\u8fd4\u56de\u53ef\u89e3\u6790\u73ed\u6b21\u5217\u8868\u3002",
                    query={"from_city": from_city, "to_city": to_city, "travel_date": travel_date, "url": url},
                )

            return {
                "status": "success",
                "source": "ctrip_bus_web",
                "query": {"from_city": from_city, "to_city": to_city, "travel_date": travel_date, "url": url},
                "total": len(buses),
                "buses": buses,
                "raw_total": len(raw_items),
            }
        except Exception as exc:
            logger.warning(f"ctrip bus web scrape failed: {exc}")
            return self._error(
                source="ctrip_bus_web",
                message=f"\u643a\u7a0b\u6c7d\u8f66\u7968\u7f51\u9875\u6293\u53d6\u5931\u8d25: {exc}",
                query={"from_city": from_city, "to_city": to_city, "travel_date": travel_date, "url": url},
            )

    def _normalize_flight_item(
        self,
        item: Any,
        from_city: str,
        to_city: str,
        travel_date: str,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None

        segments = self._first_list(item, ["segments", "segmentInfos", "flightSegments", "legs", "flightList"])
        segment_items = segments if segments else [item]
        first_segment = segment_items[0] if isinstance(segment_items[0], dict) else item
        last_segment = segment_items[-1] if isinstance(segment_items[-1], dict) else item

        flight_numbers = [
            self._pick(segment, ["flightNo", "flight_no", "flightNumber", "flight_number", "fnum", "code"])
            for segment in segment_items
            if isinstance(segment, dict)
        ]
        flight_numbers = [value for value in flight_numbers if value]
        flight_no = " + ".join(flight_numbers) or self._pick(
            item,
            ["flightNo", "flight_no", "flightNumber", "flight_number", "fnum", "code"],
        )
        if not flight_no:
            return None

        airline = self._pick(first_segment, ["airlineName", "airline", "carrierName", "carrier", "company"])
        depart_time = self._pick(
            first_segment,
            ["departureTime", "departTime", "depTime", "planDepTime", "takeoffTime", "start_time"],
        )
        arrive_time = self._pick(
            last_segment,
            ["arrivalTime", "arriveTime", "arrTime", "planArrTime", "landingTime", "end_time"],
        )
        depart_date = self._pick(first_segment, ["departureDate", "departDate", "depDate", "date"], default=travel_date)
        arrive_date = self._pick(last_segment, ["arrivalDate", "arriveDate", "arrDate"])

        return {
            "flightNo": flight_no,
            "airline": airline,
            "fromAirport": self._pick(
                first_segment,
                ["departureAirportName", "depAirportName", "depAirport", "fromAirport"],
                default=from_city,
            ),
            "toAirport": self._pick(
                last_segment,
                ["arrivalAirportName", "arrAirportName", "arrAirport", "toAirport"],
                default=to_city,
            ),
            "departureDate": depart_date,
            "departureTime": depart_time,
            "arrivalDate": arrive_date,
            "arrivalTime": arrive_time,
            "duration": self._pick(item, ["duration", "flyDuration", "totalDuration", "elapsed_time"]),
            "cabinClass": self._pick(
                item,
                ["cabinClass", "cabin", "cabinName", "seatClass"],
                default="\u7ecf\u6d4e\u8231",
            ),
            "price": self._pick(
                item,
                ["price", "ticketPrice", "adultPrice", "salePrice", "totalPrice", "lowestPrice"],
            )
            or self._pick_nested_value(
                item,
                ["price", "ticketPrice", "adultPrice", "salePrice", "totalPrice", "lowestPrice"],
            ),
            "note": self._pick(item, ["note", "discount", "refundChangeRule"], default="-"),
            "transferNum": self._pick(
                item,
                ["transferNum", "stopCount", "stops"],
                default=str(max(0, len(segment_items) - 1)),
            ),
        }

    def _normalize_bus_item(
        self,
        item: Any,
        from_city: str,
        to_city: str,
        travel_date: str,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None

        start_time = self._pick(
            item,
            ["starttime", "startTime", "depart_time", "departureTime", "fromTime", "beginTime"],
        )
        if not start_time:
            return None

        start_station = self._pick(
            item,
            ["startstation", "startStation", "from_station", "fromStation", "fromStationName"],
            default=from_city,
        )
        end_station = self._pick(
            item,
            ["endstation", "endStation", "to_station", "toStation", "toStationName", "toCityName"],
            default=to_city,
        )
        bus_type = self._pick(item, ["bustype", "busType", "vehicle_type"], default="-")
        distance = self._pick(item, ["distance", "mileage"], default="")
        note_parts: List[str] = []
        book_info = item.get("bookInfo") if isinstance(item.get("bookInfo"), dict) else {}
        bookable = self._safe_text(book_info.get("bookable")) if book_info else ""
        forecast_msg = self._safe_text(book_info.get("forecastMsg")) if book_info else ""
        if bookable == "0":
            note_parts.append("\u65e0\u7968")
        elif bookable:
            note_parts.append("\u53ef\u9884\u8ba2")
        if forecast_msg:
            note_parts.append(forecast_msg)
        if not note_parts:
            note_parts.append("\u5e73\u53f0\u672a\u8fd4\u56de\u4f59\u7968")
        if distance:
            note_parts.append(f"\u8ddd\u79bb{distance}")

        return {
            "busNo": self._pick(
                item,
                ["busNo", "bus_no", "busNumber", "scheduleId", "line_no"],
                default=f"{start_station}-{end_station}",
            ),
            "from_station": start_station,
            "to_station": end_station,
            "depart_date": self._pick(
                item,
                ["fromDate", "startDate", "depart_date", "departureDate"],
                default=travel_date,
            ),
            "depart_time": start_time,
            "duration": self._pick(item, ["duration", "runTime", "runtime", "useMinutes"], default="-"),
            "bus_type": bus_type,
            "price": self._pick(item, ["salePrice", "fullPrice", "price", "ticketPrice", "amount"]),
            "note": "\uff0c".join(note_parts),
        }

    def _first_env(self, *names: str) -> str:
        for name in names:
            value = os.getenv(name, "").strip()
            if value:
                return value
        return ""

    def _normalize_date(self, value: str) -> str:
        text = (value or "").strip()
        today = datetime.date.today()
        if not text:
            return today.strftime("%Y-%m-%d")
        if text == "\u4eca\u5929":
            return today.strftime("%Y-%m-%d")
        if text == "\u660e\u5929":
            return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if text == "\u540e\u5929":
            return (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")

        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
        if match:
            return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3))).strftime("%Y-%m-%d")
        raise ValueError(f"invalid date format: {value}")

    def _resolve_airport_code(self, value: str) -> str:
        text = self._clean_city_name(value).upper()
        if re.fullmatch(r"[A-Z]{3}", text):
            return text
        return self.CITY_AIRPORT_CODES.get(self._clean_city_name(value), "")

    def _clean_city_name(self, value: str) -> str:
        text = self._safe_text(value)
        text = re.sub(
            r"(\u5e02|\u706b\u8f66\u7ad9|\u7ad9|\u673a\u573a|\u56fd\u9645\u673a\u573a|\u767d\u4e91|\u4e24\u6c5f|\u5357|\u5317|\u4e1c|\u897f)$",
            "",
            text,
        )
        return text.strip()

    def _find_rows(self, value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, list):
            direct_rows = [item for item in value if isinstance(item, dict) and self._looks_like_flight_item(item)]
            if direct_rows:
                return direct_rows
            rows: List[Dict[str, Any]] = []
            for item in value:
                rows.extend(self._find_rows(item))
            return rows
        if not isinstance(value, dict):
            return []

        for key in [
            "flights",
            "flightList",
            "flightInfoList",
            "flightItineraryList",
            "itineraryList",
            "recommendFlightList",
            "owFlightList",
            "items",
            "list",
            "rows",
            "data",
            "routes",
            "results",
        ]:
            nested = value.get(key)
            rows = self._find_rows(nested)
            if rows:
                return rows

        if self._looks_like_flight_item(value):
            return [value]

        rows: List[Dict[str, Any]] = []
        for nested in value.values():
            rows.extend(self._find_rows(nested))
            if len(rows) >= 80:
                break
        return rows

    def _find_bus_rows(self, value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, list):
            direct_rows = [item for item in value if isinstance(item, dict) and self._looks_like_bus_item(item)]
            if direct_rows:
                return direct_rows
            rows: List[Dict[str, Any]] = []
            for item in value:
                rows.extend(self._find_bus_rows(item))
            return rows
        if not isinstance(value, dict):
            return []

        if self._looks_like_bus_item(value):
            return [value]

        rows: List[Dict[str, Any]] = []
        for nested in value.values():
            rows.extend(self._find_bus_rows(nested))
            if len(rows) >= 80:
                break
        return rows

    def _looks_like_flight_item(self, item: Dict[str, Any]) -> bool:
        if self._pick(item, ["flightNo", "flight_no", "flightNumber", "flight_number", "fnum", "code"]):
            return True
        segments = self._first_list(item, ["segments", "segmentInfos", "flightSegments", "legs", "flightList"])
        return any(
            isinstance(segment, dict)
            and self._pick(segment, ["flightNo", "flight_no", "flightNumber", "flight_number", "fnum", "code"])
            for segment in segments
        )

    def _looks_like_bus_item(self, item: Dict[str, Any]) -> bool:
        return bool(
            self._pick(
                item,
                ["starttime", "startTime", "depart_time", "departureTime", "fromTime", "beginTime"],
            )
            and self._pick(
                item,
                ["startstation", "startStation", "from_station", "fromStation", "fromStationName"],
            )
        )

    def _first_list(self, item: Dict[str, Any], keys: List[str]) -> List[Any]:
        for key in keys:
            value = item.get(key)
            if isinstance(value, list) and value:
                return value
        return []

    def _pick(self, item: Dict[str, Any], keys: List[str], default: str = "") -> str:
        for key in keys:
            value = item.get(key)
            text = self._safe_text(value)
            if text:
                return text
        return default

    def _pick_nested_value(self, value: Any, keys: List[str]) -> str:
        if isinstance(value, dict):
            for key in keys:
                text = self._safe_text(value.get(key))
                if text:
                    return text
            for nested in value.values():
                text = self._pick_nested_value(nested, keys)
                if text:
                    return text
        elif isinstance(value, list):
            for nested in value:
                text = self._pick_nested_value(nested, keys)
                if text:
                    return text
        return ""

    def _extract_next_data(self, html_text: str) -> Dict[str, Any]:
        match = re.search(
            r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>([\s\S]*?)</script>",
            html_text or "",
            re.IGNORECASE,
        )
        if not match:
            raise ValueError("page does not contain __NEXT_DATA__")
        return self._loads_json(html.unescape(match.group(1)))

    def _loads_json(self, text: str) -> Dict[str, Any]:
        import json

        data = json.loads(text)
        return data if isinstance(data, dict) else {}

    def _resolve_city_slug(self, value: str) -> str:
        text = self._clean_city_name(value)
        if re.fullmatch(r"[a-zA-Z][a-zA-Z-]+", text):
            return text.lower()
        return self.CITY_PINYIN_SLUGS.get(text, "")

    def _safe_text(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _error(
        self,
        source: str,
        message: str,
        query: Dict[str, Any],
        raw_error: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "error",
            "error": True,
            "error_type": "ticket_provider_unavailable",
            "source": source,
            "message": message,
            "query": query,
        }
        if raw_error:
            payload["raw_error"] = raw_error
        return payload
