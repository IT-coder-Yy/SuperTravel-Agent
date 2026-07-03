import argparse
import html
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "travel_knowledge" / "cities.json"
BAIDU_URL = "https://baike.baidu.com/item/{name}"
FETCH_TIMEOUT_SECONDS = 8
DEFAULT_WORKERS = 2
REQUEST_DELAY_SECONDS = 0.15


PREFECTURE_CITIES: Dict[str, List[str]] = {
    "河北省": ["石家庄市", "唐山市", "秦皇岛市", "邯郸市", "邢台市", "保定市", "张家口市", "承德市", "沧州市", "廊坊市", "衡水市"],
    "山西省": ["太原市", "大同市", "阳泉市", "长治市", "晋城市", "朔州市", "晋中市", "运城市", "忻州市", "临汾市", "吕梁市"],
    "内蒙古自治区": ["呼和浩特市", "包头市", "乌海市", "赤峰市", "通辽市", "鄂尔多斯市", "呼伦贝尔市", "巴彦淖尔市", "乌兰察布市"],
    "辽宁省": ["沈阳市", "大连市", "鞍山市", "抚顺市", "本溪市", "丹东市", "锦州市", "营口市", "阜新市", "辽阳市", "盘锦市", "铁岭市", "朝阳市", "葫芦岛市"],
    "吉林省": ["长春市", "吉林市", "四平市", "辽源市", "通化市", "白山市", "松原市", "白城市"],
    "黑龙江省": ["哈尔滨市", "齐齐哈尔市", "鸡西市", "鹤岗市", "双鸭山市", "大庆市", "伊春市", "佳木斯市", "七台河市", "牡丹江市", "黑河市", "绥化市"],
    "江苏省": ["南京市", "无锡市", "徐州市", "常州市", "苏州市", "南通市", "连云港市", "淮安市", "盐城市", "扬州市", "镇江市", "泰州市", "宿迁市"],
    "浙江省": ["杭州市", "宁波市", "温州市", "嘉兴市", "湖州市", "绍兴市", "金华市", "衢州市", "舟山市", "台州市", "丽水市"],
    "安徽省": ["合肥市", "芜湖市", "蚌埠市", "淮南市", "马鞍山市", "淮北市", "铜陵市", "安庆市", "黄山市", "滁州市", "阜阳市", "宿州市", "六安市", "亳州市", "池州市", "宣城市"],
    "福建省": ["福州市", "厦门市", "莆田市", "三明市", "泉州市", "漳州市", "南平市", "龙岩市", "宁德市"],
    "江西省": ["南昌市", "景德镇市", "萍乡市", "九江市", "新余市", "鹰潭市", "赣州市", "吉安市", "宜春市", "抚州市", "上饶市"],
    "山东省": ["济南市", "青岛市", "淄博市", "枣庄市", "东营市", "烟台市", "潍坊市", "济宁市", "泰安市", "威海市", "日照市", "临沂市", "德州市", "聊城市", "滨州市", "菏泽市"],
    "河南省": ["郑州市", "开封市", "洛阳市", "平顶山市", "安阳市", "鹤壁市", "新乡市", "焦作市", "濮阳市", "许昌市", "漯河市", "三门峡市", "南阳市", "商丘市", "信阳市", "周口市", "驻马店市"],
    "湖北省": ["武汉市", "黄石市", "十堰市", "宜昌市", "襄阳市", "鄂州市", "荆门市", "孝感市", "荆州市", "黄冈市", "咸宁市", "随州市"],
    "湖南省": ["长沙市", "株洲市", "湘潭市", "衡阳市", "邵阳市", "岳阳市", "常德市", "张家界市", "益阳市", "郴州市", "永州市", "怀化市", "娄底市"],
    "广东省": ["广州市", "韶关市", "深圳市", "珠海市", "汕头市", "佛山市", "江门市", "湛江市", "茂名市", "肇庆市", "惠州市", "梅州市", "汕尾市", "河源市", "阳江市", "清远市", "东莞市", "中山市", "潮州市", "揭阳市", "云浮市"],
    "广西壮族自治区": ["南宁市", "柳州市", "桂林市", "梧州市", "北海市", "防城港市", "钦州市", "贵港市", "玉林市", "百色市", "贺州市", "河池市", "来宾市", "崇左市"],
    "海南省": ["海口市", "三亚市", "三沙市", "儋州市"],
    "四川省": ["成都市", "自贡市", "攀枝花市", "泸州市", "德阳市", "绵阳市", "广元市", "遂宁市", "内江市", "乐山市", "南充市", "眉山市", "宜宾市", "广安市", "达州市", "雅安市", "巴中市", "资阳市"],
    "贵州省": ["贵阳市", "六盘水市", "遵义市", "安顺市", "毕节市", "铜仁市"],
    "云南省": ["昆明市", "曲靖市", "玉溪市", "保山市", "昭通市", "丽江市", "普洱市", "临沧市"],
    "西藏自治区": ["拉萨市", "日喀则市", "昌都市", "林芝市", "山南市", "那曲市"],
    "陕西省": ["西安市", "铜川市", "宝鸡市", "咸阳市", "渭南市", "汉中市", "延安市", "榆林市", "安康市", "商洛市"],
    "甘肃省": ["兰州市", "嘉峪关市", "金昌市", "白银市", "天水市", "武威市", "张掖市", "平凉市", "酒泉市", "庆阳市", "定西市", "陇南市"],
    "青海省": ["西宁市", "海东市"],
    "宁夏回族自治区": ["银川市", "石嘴山市", "吴忠市", "固原市", "中卫市"],
    "新疆维吾尔自治区": ["乌鲁木齐市", "克拉玛依市", "吐鲁番市", "哈密市"],
}


REGION_BY_PROVINCE = {
    "河北省": "华北",
    "山西省": "华北",
    "内蒙古自治区": "华北",
    "辽宁省": "东北",
    "吉林省": "东北",
    "黑龙江省": "东北",
    "江苏省": "华东",
    "浙江省": "华东",
    "安徽省": "华东",
    "福建省": "华东",
    "江西省": "华东",
    "山东省": "华东",
    "河南省": "华中",
    "湖北省": "华中",
    "湖南省": "华中",
    "广东省": "华南",
    "广西壮族自治区": "华南",
    "海南省": "华南",
    "四川省": "西南",
    "贵州省": "西南",
    "云南省": "西南",
    "西藏自治区": "西南",
    "陕西省": "西北",
    "甘肃省": "西北",
    "青海省": "西北",
    "宁夏回族自治区": "西北",
    "新疆维吾尔自治区": "西北",
}


def iter_cities() -> Iterable[Tuple[str, str]]:
    for province, cities in PREFECTURE_CITIES.items():
        for city in cities:
            yield province, city


def short_city_name(official_name: str) -> str:
    return official_name[:-1] if official_name.endswith("市") else official_name


def normalize_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[[0-9]+\]", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip("：:，,、;；。")


def extract_meta_description(page: str) -> str:
    match = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page, re.IGNORECASE)
    if not match:
        match = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', page, re.IGNORECASE)
    return normalize_text(match.group(1)) if match else ""


def extract_basic_info(page: str, field_name: str) -> str:
    pattern = (
        r"<dt[^>]*>\s*"
        + re.escape(field_name)
        + r"\s*</dt>\s*<dd[^>]*>([\s\S]*?)</dd>"
    )
    match = re.search(pattern, page, re.IGNORECASE)
    return normalize_text(match.group(1)) if match else ""


def extract_location_from_description(description: str) -> str:
    for marker in ("位于", "地处", "坐落于"):
        index = description.find(marker)
        if index >= 0:
            end_candidates = [description.find(token, index + 2) for token in "，。；;"]
            end_candidates = [value for value in end_candidates if value > index]
            end = min(end_candidates) if end_candidates else min(len(description), index + 60)
            return description[index:end].strip("，。；;")
    return ""


def split_attractions(value: str) -> List[str]:
    if not value:
        return []
    normalized = value.replace("等", "")
    parts = re.split(r"[、，,；; ]+", normalized)
    return [part for part in parts if 1 < len(part) <= 12][:8]


def fetch_baidu_page(official_name: str) -> Tuple[str, str]:
    url = BAIDU_URL.format(name=quote(official_name))
    page, curl_error = fetch_baidu_page_with_curl(url)
    if page:
        return page, url
    return "", curl_error


def fetch_baidu_page_with_curl(url: str) -> Tuple[str, str]:
    time.sleep(REQUEST_DELAY_SECONDS)
    command = [
        "curl.exe",
        "-L",
        "-k",
        "-sS",
        "--compressed",
        "--retry",
        "1",
        "--retry-delay",
        "1",
        "--max-time",
        str(FETCH_TIMEOUT_SECONDS),
        "-A",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=FETCH_TIMEOUT_SECONDS + 5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"curl failed: {exc}"

    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="ignore").strip()
        return "", f"curl exit {completed.returncode}: {error}"

    page = completed.stdout.decode("utf-8", errors="ignore")
    if "<meta" not in page or "百度百科" not in page:
        return "", "curl returned unexpected page"
    return page, ""


def collect_city_data(province: str, official_name: str, skip_fetch: bool = False) -> dict:
    page = ""
    source_url = BAIDU_URL.format(name=quote(official_name))
    fetch_error = "skip_fetch" if skip_fetch else ""
    if not skip_fetch:
        page, fetch_result = fetch_baidu_page(official_name)
        if page:
            source_url = fetch_result
        else:
            fetch_error = fetch_result

    description = extract_meta_description(page)
    geo = extract_basic_info(page, "地理位置") or extract_location_from_description(description)
    climate = extract_basic_info(page, "气候条件")
    attractions = split_attractions(extract_basic_info(page, "著名景点"))

    if not geo:
        geo = f"{REGION_BY_PROVINCE.get(province, '中国')}地区、{province}"

    return {
        "province": province,
        "official_name": official_name,
        "city": short_city_name(official_name),
        "source_url": source_url,
        "description": description,
        "geo": geo,
        "climate": climate,
        "attractions": attractions,
        "fetch_error": fetch_error,
    }


def build_items(city_data: List[dict]) -> List[dict]:
    items = []
    generated_on = date.today().isoformat()

    for data in city_data:
        city = data["city"]
        official_name = data["official_name"]
        province = data["province"]
        region = REGION_BY_PROVINCE.get(province, "中国")
        geo = data["geo"]
        climate = data["climate"]
        attractions = data["attractions"]
        source_url = data["source_url"]
        source_status = "fetched" if not data["fetch_error"] else "fallback"
        source_note = (
            "已从百度百科词条抽取摘要与基础信息字段。"
            if source_status == "fetched"
            else "百度百科批量访问可能触发安全验证；已保留词条链接，并用地级市清单、行政归属和通用旅行规则生成兜底摘要。"
        )
        attraction_text = "、".join(attractions[:5]) if attractions else "城区地标、历史文化街区、自然景观和地方美食"
        climate_text = f"，常见气候特征为{climate}" if climate else ""

        base_keywords = [city, official_name, province, "地级市", region]

        items.append(
            {
                "city": city,
                "official_name": official_name,
                "province": province,
                "source": "baidu_intro",
                "source_url": source_url,
                "source_status": source_status,
                "source_note": source_note,
                "generated_on": generated_on,
                "title": f"{city}百度百科介绍摘要",
                "keywords": base_keywords + ["百度百科", "百度介绍", "城市介绍", "百科摘要"],
                "content": f"百度百科口径摘要：{official_name}是{province}下辖地级市。该片段面向本地 RAG 问答，重点提供行政归属、地理环境、历史文化、交通经济、风景名胜和地方特产等城市基础介绍背景。",
            }
        )

        items.append(
            {
                "city": city,
                "official_name": official_name,
                "province": province,
                "source": "geo_location",
                "source_url": source_url,
                "source_status": source_status,
                "source_note": source_note,
                "generated_on": generated_on,
                "title": f"{city}地理位置",
                "keywords": base_keywords + ["地理位置", "在哪", "区位", "所属地区", geo],
                "content": f"{official_name}位于{geo}，行政上隶属{province}，行政区类别为地级市{climate_text}。旅行规划时可以把它作为{region}目的地，结合到达交通、城区距离和周边城市安排路线。",
            }
        )

        items.append(
            {
                "city": city,
                "official_name": official_name,
                "province": province,
                "source": "travel_guide",
                "source_url": source_url,
                "source_status": source_status,
                "source_note": source_note,
                "generated_on": generated_on,
                "title": f"{city}旅游攻略",
                "keywords": base_keywords + ["旅游攻略", "两日游", "三日游", "景点", "美食"] + attractions[:5],
                "content": f"{city}旅游攻略建议安排2到3天弹性行程。可优先围绕{attraction_text}组织路线：第一天看城区核心景点、博物馆或老街，第二天串联近郊山水、古镇或主题景区，第三天用于慢游、美食和周边补充。旺季提前核对门票、开放时间和交通接驳。",
            }
        )

    return items


def write_knowledge_file(items: List[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fetched_city_count = len(
        {
            item["city"]
            for item in items
            if item.get("source") == "baidu_intro" and item.get("source_status") == "fetched"
        }
    )
    payload = {
        "metadata": {
            "scope": "中国大陆地级市",
            "city_count": len({item["city"] for item in items}),
            "item_count": len(items),
            "baidu_fetched_city_count": fetched_city_count,
            "baidu_fallback_city_count": len({item["city"] for item in items}) - fetched_city_count,
            "baidu_note": "批量访问百度百科可能触发安全验证；生成器不会绕过验证，未成功抽取的城市使用结构化兜底摘要并保留百度词条链接。",
            "sources": [
                "中华人民共和国地级市列表",
                "百度百科城市词条链接与可访问时的基础信息字段",
            ],
            "generated_on": date.today().isoformat(),
        },
        "items": items,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local travel RAG knowledge for prefecture-level cities.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-fetch", action="store_true", help="Generate generic entries without requesting Baidu Baike.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()

    expected_count = sum(len(cities) for cities in PREFECTURE_CITIES.values())
    if expected_count != 293:
        print(f"Expected 293 prefecture-level cities, got {expected_count}.", file=sys.stderr)
        return 1

    city_rows = list(iter_cities())
    collected = []
    if args.skip_fetch:
        collected = [collect_city_data(province, city, skip_fetch=True) for province, city in city_rows]
    else:
        worker_count = max(1, args.workers)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(collect_city_data, province, city, False): (province, city)
                for province, city in city_rows
            }
            for future in as_completed(future_map):
                collected.append(future.result())

    province_rank = {province: index for index, province in enumerate(PREFECTURE_CITIES)}
    city_rank = {
        city: index
        for province, cities in PREFECTURE_CITIES.items()
        for index, city in enumerate(cities)
    }
    collected.sort(key=lambda item: (province_rank[item["province"]], city_rank[item["official_name"]]))

    items = build_items(collected)
    write_knowledge_file(items, args.output)

    fetched = sum(1 for item in collected if not item["fetch_error"])
    with_geo = sum(1 for item in collected if item["geo"])
    with_attractions = sum(1 for item in collected if item["attractions"])
    print(
        json.dumps(
            {
                "cities": len(collected),
                "items": len(items),
                "fetched_baidu_pages": fetched,
                "with_geo": with_geo,
                "with_attractions": with_attractions,
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
