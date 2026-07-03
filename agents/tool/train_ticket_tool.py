import datetime
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests

from agents.tool.tool_base import ToolBase
from agents.tool.ticket_query_utils import clean_route_name, extract_route_from_query
from agents.utils.logger import logger


class TrainTicketTool(ToolBase):
    """12306实时车票查询工具。"""

    STATION_JS_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
    INDEX_URL = "https://www.12306.cn/index/"
    LEFT_TICKET_INIT_URL = "https://kyfw.12306.cn/otn/leftTicket/init"
    LEFT_TICKET_URL = "https://kyfw.12306.cn/otn/leftTicket/query"
    LEFT_TICKET_ENDPOINTS = [
        "https://kyfw.12306.cn/otn/leftTicket/query",
        "https://kyfw.12306.cn/otn/leftTicket/queryG",
        "https://kyfw.12306.cn/otn/leftTicket/queryZ",
        "https://kyfw.12306.cn/otn/leftTicket/queryO",
    ]
    PRICE_URL = "https://kyfw.12306.cn/otn/leftTicket/queryTicketPrice"

    _station_lock = threading.Lock()
    _station_name_to_code: Dict[str, str] = {}
    _station_cache_time: Optional[datetime.datetime] = None

    SEAT_LEFT_INDEX = {
        "商务座": 32,
        "特等座": 25,
        "一等座": 31,
        "二等座": 30,
        "高级软卧": 20,
        "软卧": 23,
        "硬卧": 28,
        "软座": 24,
        "硬座": 29,
        "无座": 26,
    }

    SEAT_PRICE_KEYS = {
        "商务座": ["A9", "9", "SWZ"],
        "特等座": ["P", "TZ"],
        "一等座": ["M", "ZY"],
        "二等座": ["O", "ZE"],
        "高级软卧": ["A6", "GG"],
        "软卧": ["A4", "RW"],
        "硬卧": ["A3", "YW"],
        "软座": ["A2", "RZ"],
        "硬座": ["A1", "YZ"],
        "无座": ["WZ"],
    }

    def __init__(self):
        super().__init__()

    @ToolBase.tool()
    def query_12306_realtime_tickets(
        self,
        from_station: str,
        to_station: str,
        travel_date: str,
        purpose_codes: str = "ADULT",
        max_results: int = 20,
    ) -> Dict[str, Any]:
        """查询12306官网实时车次、余票和参考票价。

        Args:
            from_station (str): 出发站中文名，如"安阳"或"安阳东"。
            to_station (str): 到达站中文名，如"杭州"或"杭州东"。
            travel_date (str): 出发日期，支持YYYY-MM-DD、YYYY/MM/DD、今天、明天、后天。
            purpose_codes (str): 乘车人类型，默认ADULT，可用ADULT/STUDENT。
            max_results (int): 返回最大车次数，默认20，范围1-50。

        Returns:
            Dict[str, Any]: 结构化查询结果，包含tickets列表和markdown_table。
        """
        try:
            normalized_date = self._normalize_date(travel_date)
            limit = max(1, min(int(max_results), 50))

            from_code = self._resolve_station_code(from_station)
            to_code = self._resolve_station_code(to_station)
            if not from_code or not to_code:
                return {
                    "status": "error",
                    "source": "12306_official_api",
                    "message": "站点解析失败，请使用更准确的站名，例如 安阳东 / 杭州东。",
                    "query": {
                        "from_station": from_station,
                        "to_station": to_station,
                        "travel_date": normalized_date,
                    },
                }

            payload = self._fetch_left_tickets(
                from_code=from_code,
                to_code=to_code,
                travel_date=normalized_date,
                purpose_codes=purpose_codes,
            )

            raw_rows = payload.get("result", []) if isinstance(payload, dict) else []
            code_to_name = payload.get("map", {}) if isinstance(payload, dict) else {}

            tickets: List[Dict[str, Any]] = []
            for row in raw_rows:
                fields = row.split("|")
                if len(fields) < 36:
                    continue

                train_code = self._safe_field(fields, 3)
                from_name = code_to_name.get(self._safe_field(fields, 6), from_station)
                to_name = code_to_name.get(self._safe_field(fields, 7), to_station)

                if not train_code:
                    continue

                price_data = self._fetch_ticket_price(
                    train_no=self._safe_field(fields, 2),
                    from_station_no=self._safe_field(fields, 16),
                    to_station_no=self._safe_field(fields, 17),
                    seat_types=self._safe_field(fields, 35),
                    travel_date=normalized_date,
                )

                seats = self._build_seat_snapshot(fields, price_data)
                primary = self._pick_primary_offer(seats)

                ticket = {
                    "train_code": train_code,
                    "from_station": from_name,
                    "to_station": to_name,
                    "depart_time": self._safe_field(fields, 8) or "-",
                    "arrive_time": self._safe_field(fields, 9) or "-",
                    "duration": self._safe_field(fields, 10) or "-",
                    "can_web_buy": self._safe_field(fields, 11) == "Y",
                    "seat_recommendation": primary.get("seat", "-"),
                    "seat_left": primary.get("left", "-"),
                    "price": primary.get("price", "-"),
                    "seats": seats,
                }
                tickets.append(ticket)

                if len(tickets) >= limit:
                    break

            markdown_table = self._build_markdown_table(tickets)

            return {
                "status": "success",
                "source": "12306_official_api",
                "query": {
                    "from_station": from_station,
                    "to_station": to_station,
                    "from_station_code": from_code,
                    "to_station_code": to_code,
                    "travel_date": normalized_date,
                    "purpose_codes": purpose_codes,
                },
                "total": len(tickets),
                "tickets": tickets,
                "markdown_table": markdown_table,
                "message": "已从12306官方接口获取实时车次信息。",
            }
        except Exception as e:
            logger.error(f"query_12306_realtime_tickets failed: {e}")
            return {
                "status": "error",
                "source": "12306_official_api",
                "message": f"查询失败: {e}",
                "fallback": "当前环境可能无法直接访问12306查询接口，请优先调用12306-mcp工具获取实时票务。",
                "query": {
                    "from_station": from_station,
                    "to_station": to_station,
                    "travel_date": travel_date,
                },
            }

    @ToolBase.tool()
    def query_12306_tickets_by_query(
        self,
        query: str,
        travel_date: str = "",
        purpose_codes: str = "ADULT",
        max_results: int = 20,
    ) -> Dict[str, Any]:
        """根据自然语言问题自动解析站点并查询12306实时车票。

        Args:
            query (str): 用户原始问题，例如"安阳到杭州明天高铁票"。
            travel_date (str): 可选日期；不填时自动从query解析，解析失败则默认今天。
            purpose_codes (str): 乘车人类型，默认ADULT，可用ADULT/STUDENT。
            max_results (int): 返回最大车次数，默认20，范围1-50。

        Returns:
            Dict[str, Any]: 结构化查询结果，包含解析出的路线与tickets列表。
        """
        route = self._extract_route_from_query(query)
        if not route:
            return {
                "status": "error",
                "source": "12306_official_api",
                "message": "未能从问题中识别出出发地和到达地，请使用“安阳到杭州”这类表达。",
                "query_text": query,
            }

        parsed_date = travel_date.strip() if travel_date else self._extract_date_from_query(query)
        if not parsed_date:
            parsed_date = datetime.date.today().strftime("%Y-%m-%d")

        result = self.query_12306_realtime_tickets(
            from_station=route[0],
            to_station=route[1],
            travel_date=parsed_date,
            purpose_codes=purpose_codes,
            max_results=max_results,
        )
        result["parsed_route"] = {
            "from_station": route[0],
            "to_station": route[1],
            "travel_date": parsed_date,
        }
        result["query_text"] = query
        return result

    def _normalize_date(self, value: str) -> str:
        text = (value or "").strip()
        today = datetime.date.today()
        if not text:
            return today.strftime("%Y-%m-%d")

        if text == "今天":
            return today.strftime("%Y-%m-%d")
        if text == "明天":
            return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if text == "后天":
            return (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")

        date_patterns = [
            (r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", True),
            (r"^(\d{1,2})[-/](\d{1,2})$", False),
            (r"^(\d{1,2})月(\d{1,2})日?$", False),
        ]

        for pattern, has_year in date_patterns:
            match = re.match(pattern, text)
            if not match:
                continue
            groups = match.groups()
            if has_year:
                y, m, d = int(groups[0]), int(groups[1]), int(groups[2])
            else:
                y, m, d = today.year, int(groups[0]), int(groups[1])
            dt = datetime.date(y, m, d)
            return dt.strftime("%Y-%m-%d")

        raise ValueError(f"无法识别日期格式: {value}")

    def _extract_date_from_query(self, query: str) -> str:
        text = (query or "").strip()
        if not text:
            return ""

        if "今天" in text:
            return self._normalize_date("今天")
        if "明天" in text:
            return self._normalize_date("明天")
        if "后天" in text:
            return self._normalize_date("后天")

        full_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", text)
        if full_match:
            return self._normalize_date(full_match.group(1))

        month_day_match = re.search(r"(\d{1,2}月\d{1,2}日?)", text)
        if month_day_match:
            return self._normalize_date(month_day_match.group(1))

        return ""

    def _extract_route_from_query(self, query: str) -> Optional[Tuple[str, str]]:
        return extract_route_from_query(query)

    def _clean_route_name(self, value: str) -> str:
        return clean_route_name(value)

    def _fetch_left_tickets(
        self,
        from_code: str,
        to_code: str,
        travel_date: str,
        purpose_codes: str,
    ) -> Dict[str, Any]:
        params = {
            "leftTicketDTO.train_date": travel_date,
            "leftTicketDTO.from_station": from_code,
            "leftTicketDTO.to_station": to_code,
            "purpose_codes": purpose_codes,
        }
        session = self._create_12306_session()
        candidate_urls = list(self.LEFT_TICKET_ENDPOINTS)
        attempted_urls = set()
        errors: List[str] = []

        while candidate_urls:
            url = candidate_urls.pop(0)
            if url in attempted_urls:
                continue
            attempted_urls.add(url)

            try:
                response = session.get(
                    url,
                    params=params,
                    headers=self._build_headers(referer=self.LEFT_TICKET_INIT_URL),
                    timeout=12,
                )
                response.raise_for_status()
                data = self._parse_12306_json_response(response)
            except Exception as e:
                errors.append(f"{url}: {e}")
                continue

            if not isinstance(data, dict):
                errors.append(f"{url}: 12306返回内容不是JSON对象")
                continue

            c_url = data.get("c_url")
            if c_url:
                redirected_url = self._build_12306_url(str(c_url))
                if redirected_url and redirected_url not in attempted_urls:
                    candidate_urls.insert(0, redirected_url)

            if data.get("status", False):
                return data.get("data", {}) or {}

            errors.append(f"{url}: 12306接口返回异常: {data}")

        detail = "；".join(errors[-4:]) if errors else "无可用12306余票查询接口"
        raise RuntimeError(f"12306余票查询失败，可能被网关拦截或接口路径已变化: {detail}")

    def _fetch_ticket_price(
        self,
        train_no: str,
        from_station_no: str,
        to_station_no: str,
        seat_types: str,
        travel_date: str,
    ) -> Dict[str, str]:
        if not train_no or not from_station_no or not to_station_no:
            return {}

        params = {
            "train_no": train_no,
            "from_station_no": from_station_no,
            "to_station_no": to_station_no,
            "seat_types": seat_types,
            "train_date": travel_date,
        }

        try:
            session = self._create_12306_session()
            response = session.get(
                self.PRICE_URL,
                params=params,
                headers=self._build_headers(referer=self.LEFT_TICKET_INIT_URL),
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            price_data = payload.get("data", {}) if isinstance(payload, dict) else {}
            if not isinstance(price_data, dict):
                return {}
            return {k: self._format_price(v) for k, v in price_data.items()}
        except Exception as e:
            logger.warning(f"queryTicketPrice failed for {train_no}: {e}")
            return {}

    def _build_seat_snapshot(self, fields: List[str], price_data: Dict[str, str]) -> Dict[str, Dict[str, str]]:
        seats: Dict[str, Dict[str, str]] = {}
        for seat_name, idx in self.SEAT_LEFT_INDEX.items():
            left_raw = self._safe_field(fields, idx)
            price = self._resolve_seat_price(seat_name, price_data)

            left = left_raw or "-"
            if left in {"--", ""} and price == "-":
                continue

            seats[seat_name] = {
                "left": left,
                "price": price,
            }
        return seats

    def _pick_primary_offer(self, seats: Dict[str, Dict[str, str]]) -> Dict[str, str]:
        if not seats:
            return {"seat": "-", "left": "-", "price": "-"}

        preferred_order = [
            "二等座",
            "硬座",
            "无座",
            "一等座",
            "硬卧",
            "软卧",
            "商务座",
            "特等座",
            "高级软卧",
            "软座",
        ]

        candidates: List[Tuple[float, str, str, str]] = []
        for seat_name in preferred_order:
            seat_info = seats.get(seat_name)
            if not seat_info:
                continue
            price_text = seat_info.get("price", "-")
            price_num = self._parse_price_number(price_text)
            candidates.append((price_num if price_num is not None else 1e9, seat_name, seat_info.get("left", "-"), price_text))

        if not candidates:
            first_name = next(iter(seats.keys()))
            first_info = seats[first_name]
            return {
                "seat": first_name,
                "left": first_info.get("left", "-"),
                "price": first_info.get("price", "-"),
            }

        candidates.sort(key=lambda item: item[0])
        _, seat, left, price = candidates[0]
        return {
            "seat": seat,
            "left": left,
            "price": price,
        }

    def _build_markdown_table(self, tickets: List[Dict[str, Any]]) -> str:
        if not tickets:
            return ""

        lines = [
            "| 车次 | 出发地 | 到达地 | 出发时间 | 到达时间 | 历时 | 推荐席别 | 余票 | 参考价 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in tickets:
            lines.append(
                f"| {row.get('train_code', '-')} | {row.get('from_station', '-')} | {row.get('to_station', '-')} | "
                f"{row.get('depart_time', '-')} | {row.get('arrive_time', '-')} | {row.get('duration', '-')} | "
                f"{row.get('seat_recommendation', '-')} | {row.get('seat_left', '-')} | {row.get('price', '-')} |"
            )
        return "\n".join(lines)

    def _resolve_seat_price(self, seat_name: str, price_data: Dict[str, str]) -> str:
        for key in self.SEAT_PRICE_KEYS.get(seat_name, []):
            value = price_data.get(key)
            if value:
                return value
        return "-"

    def _parse_price_number(self, price: str) -> Optional[float]:
        if not price or price == "-":
            return None
        match = re.search(r"(\d+(?:\.\d+)?)", price)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    def _format_price(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "-"
        text = text.replace("￥", "").replace("¥", "")
        if text == "--":
            return "-"
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return f"{text}元"
        return text

    def _safe_field(self, fields: List[str], idx: int) -> str:
        if idx < 0 or idx >= len(fields):
            return ""
        return (fields[idx] or "").strip()

    def _resolve_station_code(self, station_name: str) -> Optional[str]:
        station_map = self._load_station_name_to_code()
        for variant in self._station_name_variants(station_name):
            if variant in station_map:
                return station_map[variant]

        normalized_variants = {self._normalize_station_text(v) for v in self._station_name_variants(station_name)}
        for name, code in station_map.items():
            if self._normalize_station_text(name) in normalized_variants:
                return code

        return None

    def _station_name_variants(self, station_name: str) -> List[str]:
        text = self._normalize_station_text(station_name)
        if not text:
            return []

        variants = [text]

        if text.endswith("火车站"):
            variants.append(text.replace("火车站", "站"))
        if text.endswith("高铁站"):
            variants.append(text.replace("高铁站", "站"))
        if text.endswith("站"):
            variants.append(text[:-1])
        else:
            variants.append(text + "站")

        if text.endswith("市"):
            variants.append(text[:-1])

        if re.search(r"[东南西北]$", text):
            variants.append(text + "站")

        deduped: List[str] = []
        seen = set()
        for item in variants:
            if item and item not in seen:
                deduped.append(item)
                seen.add(item)
        return deduped

    def _normalize_station_text(self, value: str) -> str:
        text = (value or "").strip()
        text = re.sub(r"\s+", "", text)
        text = text.replace("（", "").replace("）", "")
        text = text.replace("(", "").replace(")", "")
        return text

    def _load_station_name_to_code(self) -> Dict[str, str]:
        with self._station_lock:
            now = datetime.datetime.now()
            if (
                self._station_name_to_code
                and self._station_cache_time
                and (now - self._station_cache_time).total_seconds() < 24 * 3600
            ):
                return self._station_name_to_code

            session = self._create_12306_session()
            response = session.get(
                self.STATION_JS_URL,
                headers=self._build_headers(referer=self.LEFT_TICKET_INIT_URL),
                timeout=12,
            )
            response.raise_for_status()
            text = response.text

            match = re.search(r"station_names\s*=\s*'([^']+)';", text)
            if not match:
                raise RuntimeError("无法解析12306站点编码表")

            station_blob = match.group(1)
            station_map: Dict[str, str] = {}
            for row in station_blob.strip("@").split("@"):
                parts = row.split("|")
                if len(parts) < 3:
                    continue
                name = parts[1].strip()
                code = parts[2].strip()
                if not name or not code:
                    continue
                station_map[name] = code

                simplified = name[:-1] if name.endswith("站") else name
                if simplified:
                    station_map.setdefault(simplified, code)

            self._station_name_to_code = station_map
            self._station_cache_time = now
            return self._station_name_to_code

    def _create_12306_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(self._build_headers(referer=self.LEFT_TICKET_INIT_URL))
        self._prime_12306_session(session)
        return session

    def _prime_12306_session(self, session: requests.Session) -> None:
        for url in (self.LEFT_TICKET_INIT_URL, self.INDEX_URL):
            try:
                session.get(
                    url,
                    headers=self._build_headers(referer=self.INDEX_URL),
                    timeout=12,
                )
                if session.cookies:
                    return
            except Exception as e:
                logger.warning(f"prime 12306 session failed for {url}: {e}")

    def _parse_12306_json_response(self, response: requests.Response) -> Dict[str, Any]:
        content_type = (response.headers.get("content-type") or "").lower()
        text = (response.text or "").lstrip()
        if "json" not in content_type and not text.startswith(("{", "[")):
            raise RuntimeError(f"12306返回非JSON内容({content_type})，可能被网关拦截")
        return response.json()

    def _build_12306_url(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        if text.startswith("http://") or text.startswith("https://"):
            return text
        if text.startswith("/"):
            return f"https://kyfw.12306.cn{text}"
        if text.startswith("otn/"):
            return f"https://kyfw.12306.cn/{text}"
        return f"https://kyfw.12306.cn/otn/{text}"

    def _build_headers(self, referer: str = "https://kyfw.12306.cn/") -> Dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": referer,
            "Origin": "https://kyfw.12306.cn",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
