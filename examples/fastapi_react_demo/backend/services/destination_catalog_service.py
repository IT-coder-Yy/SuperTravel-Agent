import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[4]
KNOWLEDGE_PATH = PROJECT_ROOT / "data" / "travel_knowledge" / "cities.json"

INTERNATIONAL_DESTINATIONS: Dict[str, Dict[str, object]] = {
    "东京": {"aliases": ["Tokyo", "東京都"], "country": "日本", "classics": ["浅草寺", "东京国立博物馆", "明治神宫", "新宿御苑", "东京晴空塔"]},
    "京都": {"aliases": ["Kyoto", "京都市"], "country": "日本", "classics": ["清水寺", "伏见稻荷大社", "金阁寺", "岚山", "二条城"]},
    "大阪": {"aliases": ["Osaka", "大阪市"], "country": "日本", "classics": ["大阪城", "道顿堀", "通天阁", "大阪历史博物馆", "梅田蓝天大厦"]},
    "首尔": {"aliases": ["Seoul", "서울"], "country": "韩国", "classics": ["景福宫", "北村韩屋村", "国立中央博物馆", "南山首尔塔", "广藏市场"]},
    "新加坡": {"aliases": ["Singapore"], "country": "新加坡", "classics": ["滨海湾花园", "新加坡国家博物馆", "牛车水", "圣淘沙", "新加坡植物园"]},
    "曼谷": {"aliases": ["Bangkok", "กรุงเทพมหานคร"], "country": "泰国", "classics": ["曼谷大皇宫", "卧佛寺", "郑王庙", "泰国国家博物馆", "恰图恰周末市场"]},
    "巴黎": {"aliases": ["Paris"], "country": "法国", "classics": ["卢浮宫", "埃菲尔铁塔", "奥赛博物馆", "凯旋门", "巴黎圣母院"]},
    "伦敦": {"aliases": ["London"], "country": "英国", "classics": ["大英博物馆", "伦敦塔", "国家美术馆", "威斯敏斯特教堂", "泰特现代美术馆"]},
    "罗马": {"aliases": ["Rome", "Roma"], "country": "意大利", "classics": ["罗马斗兽场", "古罗马广场", "万神殿", "博尔盖塞美术馆", "特雷维喷泉"]},
    "巴塞罗那": {"aliases": ["Barcelona"], "country": "西班牙", "classics": ["圣家堂", "古埃尔公园", "巴特罗之家", "加泰罗尼亚国家艺术博物馆", "哥特区"]},
    "阿姆斯特丹": {"aliases": ["Amsterdam"], "country": "荷兰", "classics": ["荷兰国立博物馆", "梵高博物馆", "安妮之家", "约旦区", "运河带"]},
    "维也纳": {"aliases": ["Vienna", "Wien"], "country": "奥地利", "classics": ["美泉宫", "艺术史博物馆", "霍夫堡", "维也纳国家歌剧院", "美景宫"]},
    "布拉格": {"aliases": ["Prague", "Praha"], "country": "捷克", "classics": ["布拉格城堡", "查理大桥", "老城广场", "圣维特大教堂", "犹太区"]},
    "迪拜": {"aliases": ["Dubai", "دبي"], "country": "阿联酋", "classics": ["哈利法塔", "迪拜博物馆", "迪拜河", "朱美拉清真寺", "迪拜购物中心"]},
    "纽约": {"aliases": ["New York", "New York City", "NYC"], "country": "美国", "classics": ["大都会艺术博物馆", "中央公园", "自由女神像", "现代艺术博物馆", "帝国大厦"]},
    "洛杉矶": {"aliases": ["Los Angeles", "LA"], "country": "美国", "classics": ["格里菲斯天文台", "盖蒂中心", "洛杉矶县艺术博物馆", "好莱坞星光大道", "圣莫尼卡码头"]},
    "旧金山": {"aliases": ["San Francisco", "SF"], "country": "美国", "classics": ["金门大桥", "恶魔岛", "旧金山现代艺术博物馆", "渡轮大厦", "渔人码头"]},
    "悉尼": {"aliases": ["Sydney"], "country": "澳大利亚", "classics": ["悉尼歌剧院", "悉尼海港大桥", "新南威尔士美术馆", "岩石区", "邦迪海滩"]},
    "墨尔本": {"aliases": ["Melbourne"], "country": "澳大利亚", "classics": ["维多利亚国家美术馆", "联邦广场", "皇家植物园", "维多利亚女王市场", "墨尔本博物馆"]},
}

DOMESTIC_CLASSICS: Dict[str, List[str]] = {
    "北京": ["故宫博物院", "天安门广场", "颐和园", "天坛公园", "慕田峪长城"],
    "杭州": ["西湖风景名胜区", "灵隐寺", "浙江省博物馆", "良渚博物院", "中国京杭大运河博物馆"],
    "上海": ["上海博物馆", "外滩", "豫园", "中华艺术宫", "武康路历史文化名街"],
    "成都": ["成都大熊猫繁育研究基地", "武侯祠", "杜甫草堂", "金沙遗址博物馆", "宽窄巷子"],
    "西安": ["秦始皇帝陵博物院", "陕西历史博物馆", "西安城墙", "大雁塔", "西安碑林博物馆"],
    "南京": ["南京博物院", "中山陵", "明孝陵", "侵华日军南京大屠杀遇难同胞纪念馆", "夫子庙秦淮风光带"],
    "苏州": ["拙政园", "苏州博物馆", "虎丘山风景名胜区", "留园", "平江历史街区"],
    "广州": ["广东省博物馆", "陈家祠", "广州塔", "南越王博物院", "沙面岛"],
    "桂林": ["漓江风景名胜区", "象鼻山景区", "龙脊梯田", "靖江王城", "两江四湖"] ,
    "丽江": ["丽江古城", "玉龙雪山", "束河古镇", "黑龙潭公园", "白沙古镇"],
    "张家界": ["张家界国家森林公园", "天门山国家森林公园", "张家界大峡谷", "黄龙洞", "宝峰湖"],
    "厦门": ["鼓浪屿", "厦门园林植物园", "南普陀寺", "厦门市博物馆", "环岛路"],
}

INVALID_POI_NAME_PATTERN = re.compile(
    r"^(?:规划)?道路$|路线规划|导航方案|道路信息|行政区划|地图检索",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def domestic_city_names() -> List[str]:
    try:
        payload = json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        cities = {str(item.get("city") or "").strip() for item in payload.get("items", []) if item.get("city")}
        cities.update(DOMESTIC_CLASSICS)
        return sorted(cities, key=len, reverse=True)
    except (OSError, ValueError, TypeError):
        return sorted(DOMESTIC_CLASSICS, key=len, reverse=True)


def destination_aliases() -> Dict[str, str]:
    result = {city.casefold(): city for city in domestic_city_names()}
    for city, payload in INTERNATIONAL_DESTINATIONS.items():
        result[city.casefold()] = city
        for alias in payload.get("aliases", []):
            result[str(alias).casefold()] = city
    return result


def canonical_destination(text: str) -> Optional[str]:
    query = str(text or "").casefold()
    if not query:
        return None
    matches = [(alias, city) for alias, city in destination_aliases().items() if alias in query]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


def classic_places_for_city(city: str) -> List[Tuple[str, int]]:
    canonical = canonical_destination(city) or str(city or "").strip()
    places = DOMESTIC_CLASSICS.get(canonical)
    if places is None:
        payload = INTERNATIONAL_DESTINATIONS.get(canonical, {})
        places = list(payload.get("classics", []))
    return [(name, (index // 3) + 1) for index, name in enumerate(places or [])]


def is_invalid_poi_name(name: str) -> bool:
    return not str(name or "").strip() or bool(INVALID_POI_NAME_PATTERN.search(str(name).strip()))
