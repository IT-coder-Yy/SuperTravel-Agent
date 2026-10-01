import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[4]
KNOWLEDGE_PATH = PROJECT_ROOT / "data" / "travel_knowledge" / "cities.json"

INTERNATIONAL_DESTINATIONS: Dict[str, Dict[str, object]] = {
    "东京": {
        "aliases": ["Tokyo", "東京都"],
        "country": "日本",
        "classics": [
            "浅草寺", "东京国立博物馆", "明治神宫", "新宿御苑", "东京晴空塔", "皇居",
            "东京铁塔", "上野公园", "国立西洋美术馆", "江户东京博物馆", "东京国立近代美术馆",
            "森美术馆", "根津美术馆", "银座", "秋叶原", "涩谷", "新宿", "原宿", "丰洲市场",
            "六本木新城", "表参道", "神乐坂", "日比谷公园", "代代木公园", "滨离宫恩赐庭园",
            "六义园", "小石川后乐园", "东京站", "增上寺", "豪德寺", "深大寺",
            "三鹰之森吉卜力美术馆", "国立科学博物馆", "皇居东御苑", "迎宾馆赤坂离宫",
        ],
    },
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

INTERNATIONAL_DESTINATION_META: Dict[str, Dict[str, object]] = {
    "东京": {"name_en": "Tokyo", "country_code": "JP", "country": "日本", "timezone": "Asia/Tokyo", "currency": "JPY", "languages": ["日语"]},
    "京都": {"name_en": "Kyoto", "country_code": "JP", "country": "日本", "timezone": "Asia/Tokyo", "currency": "JPY", "languages": ["日语"]},
    "大阪": {"name_en": "Osaka", "country_code": "JP", "country": "日本", "timezone": "Asia/Tokyo", "currency": "JPY", "languages": ["日语"]},
    "首尔": {"name_en": "Seoul", "country_code": "KR", "country": "韩国", "timezone": "Asia/Seoul", "currency": "KRW", "languages": ["韩语"]},
    "新加坡": {"name_en": "Singapore", "country_code": "SG", "country": "新加坡", "timezone": "Asia/Singapore", "currency": "SGD", "languages": ["英语", "华语", "马来语", "泰米尔语"]},
    "曼谷": {"name_en": "Bangkok", "country_code": "TH", "country": "泰国", "timezone": "Asia/Bangkok", "currency": "THB", "languages": ["泰语"]},
    "巴黎": {"name_en": "Paris", "country_code": "FR", "country": "法国", "timezone": "Europe/Paris", "currency": "EUR", "languages": ["法语"]},
    "伦敦": {"name_en": "London", "country_code": "GB", "country": "英国", "timezone": "Europe/London", "currency": "GBP", "languages": ["英语"]},
    "罗马": {"name_en": "Rome", "country_code": "IT", "country": "意大利", "timezone": "Europe/Rome", "currency": "EUR", "languages": ["意大利语"]},
    "巴塞罗那": {"name_en": "Barcelona", "country_code": "ES", "country": "西班牙", "timezone": "Europe/Madrid", "currency": "EUR", "languages": ["西班牙语", "加泰罗尼亚语"]},
    "阿姆斯特丹": {"name_en": "Amsterdam", "country_code": "NL", "country": "荷兰", "timezone": "Europe/Amsterdam", "currency": "EUR", "languages": ["荷兰语"]},
    "维也纳": {"name_en": "Vienna", "country_code": "AT", "country": "奥地利", "timezone": "Europe/Vienna", "currency": "EUR", "languages": ["德语"]},
    "布拉格": {"name_en": "Prague", "country_code": "CZ", "country": "捷克", "timezone": "Europe/Prague", "currency": "CZK", "languages": ["捷克语"]},
    "迪拜": {"name_en": "Dubai", "country_code": "AE", "country": "阿联酋", "timezone": "Asia/Dubai", "currency": "AED", "languages": ["阿拉伯语", "英语"]},
    "纽约": {"name_en": "New York", "country_code": "US", "country": "美国", "timezone": "America/New_York", "currency": "USD", "languages": ["英语"]},
    "洛杉矶": {"name_en": "Los Angeles", "country_code": "US", "country": "美国", "timezone": "America/Los_Angeles", "currency": "USD", "languages": ["英语"]},
    "旧金山": {"name_en": "San Francisco", "country_code": "US", "country": "美国", "timezone": "America/Los_Angeles", "currency": "USD", "languages": ["英语"]},
    "悉尼": {"name_en": "Sydney", "country_code": "AU", "country": "澳大利亚", "timezone": "Australia/Sydney", "currency": "AUD", "languages": ["英语"]},
    "墨尔本": {"name_en": "Melbourne", "country_code": "AU", "country": "澳大利亚", "timezone": "Australia/Melbourne", "currency": "AUD", "languages": ["英语"]},
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
    "天津": ["天津之眼摩天轮", "天津古文化街", "五大道文化旅游区", "意大利风情旅游区", "瓷房子"],
    "重庆": ["洪崖洞民俗风貌区", "解放碑步行街", "长江索道", "磁器口古镇", "重庆中国三峡博物馆", "武隆天生三桥"],
    "深圳": ["世界之窗", "深圳湾公园", "莲花山公园", "大鹏所城", "深圳博物馆", "甘坑古镇"],
    "武汉": ["黄鹤楼", "武汉长江大桥", "湖北省博物馆", "东湖生态旅游风景区", "武汉大学", "昙华林", "汉口江滩"],
    "长沙": ["岳麓山", "橘子洲", "湖南博物院", "岳麓书院", "太平街", "天心阁"],
    "郑州": ["河南博物院", "郑州黄河文化公园", "二七纪念塔", "郑州商都遗址博物院", "嵩山少林寺", "只有河南·戏剧幻城"],
    "洛阳": ["龙门石窟", "洛阳博物馆", "白马寺", "洛邑古城", "隋唐洛阳城国家遗址公园", "老君山景区"],
    "开封": ["清明上河园", "开封府", "大相国寺", "龙亭公园", "铁塔公园", "中国翰园碑林"],
    "安阳": ["殷墟博物馆", "殷墟宫殿宗庙遗址", "中国文字博物馆", "曹操高陵遗址博物馆", "羑里城", "岳飞庙"],
    "石家庄": ["河北博物院", "正定古城", "隆兴寺", "西柏坡纪念馆", "赵州桥", "抱犊寨"],
    "太原": ["山西博物院", "晋祠博物馆", "双塔寺", "太原古县城", "蒙山大佛", "晋商博物院"],
    "济南": ["趵突泉景区", "大明湖景区", "千佛山风景名胜区", "山东博物馆", "曲水亭街", "黑虎泉"],
    "青岛": ["栈桥", "八大关风景区", "崂山风景区", "青岛啤酒博物馆", "五四广场", "小鱼山公园"],
    "大连": ["星海广场", "老虎滩海洋公园", "棒棰岛景区", "金石滩国家旅游度假区", "俄罗斯风情街", "大连博物馆"],
    "沈阳": ["沈阳故宫博物院", "张学良旧居陈列馆", "九一八历史博物馆", "辽宁省博物馆", "北陵公园", "中街步行街"],
    "长春": ["伪满皇宫博物院", "净月潭国家森林公园", "长影旧址博物馆", "吉林省博物院", "南湖公园", "长春世界雕塑园"],
    "哈尔滨": ["圣索菲亚教堂", "中央大街", "哈尔滨冰雪大世界", "太阳岛风景区", "黑龙江省博物馆", "侵华日军第七三一部队罪证陈列馆"],
    "呼和浩特": ["内蒙古博物院", "大召寺", "塞上老街", "五塔寺", "昭君博物院", "敕勒川草原文化旅游区"],
    "银川": ["宁夏博物馆", "西夏陵", "镇北堡西部影城", "贺兰山岩画", "银川鼓楼", "水洞沟旅游区"],
    "兰州": ["甘肃省博物馆", "中山桥", "白塔山公园", "黄河母亲雕塑", "水车博览园", "兰州老街"],
    "西宁": ["青海省博物馆", "塔尔寺", "东关清真大寺", "青藏高原自然博物馆", "青海藏文化博物院", "北山美丽园"],
    "乌鲁木齐": ["新疆维吾尔自治区博物馆", "红山公园", "新疆国际大巴扎", "天山大峡谷", "乌鲁木齐市博物馆", "南山风景区"],
    "拉萨": ["布达拉宫", "大昭寺", "八廓街", "罗布林卡", "西藏博物馆", "色拉寺"],
    "昆明": ["云南省博物馆", "滇池", "翠湖公园", "石林风景区", "云南民族村", "西山风景区"],
    "贵阳": ["青岩古镇", "甲秀楼", "贵州省博物馆", "黔灵山公园", "天河潭", "花溪夜郎谷"],
    "南宁": ["青秀山风景区", "广西民族博物馆", "三街两巷", "南湖公园", "广西壮族自治区博物馆", "方特东盟神画"],
    "海口": ["海口骑楼老街", "海南省博物馆", "雷琼海口火山群世界地质公园", "五公祠", "万绿园", "假日海滩"],
    "三亚": ["亚龙湾", "天涯海角游览区", "蜈支洲岛", "南山文化旅游区", "鹿回头风景区", "三亚千古情景区"],
    "福州": ["三坊七巷", "福建博物院", "鼓山风景区", "上下杭历史文化街区", "烟台山公园", "福州西湖公园"],
    "南昌": ["滕王阁", "江西省博物馆", "南昌八一起义纪念馆", "万寿宫历史文化街区", "秋水广场", "汉代海昏侯国遗址博物馆"],
    "合肥": ["安徽博物院", "李鸿章故居陈列馆", "包公园", "三河古镇", "渡江战役纪念馆", "合柴1972"],
    "宁波": ["天一阁博物院", "宁波博物院", "宁波老外滩", "东钱湖旅游度假区", "宁波鼓楼", "蒋氏故居"],
    "无锡": ["鼋头渚", "灵山胜境", "惠山古镇", "南长街", "蠡园", "无锡博物院"],
    "扬州": ["瘦西湖风景区", "个园", "何园", "大明寺", "扬州中国大运河博物馆", "东关街历史街区"],
    "绍兴": ["鲁迅故里", "沈园", "绍兴博物馆", "兰亭景区", "绍兴东湖", "书圣故里历史街区"],
    "桂林": ["漓江风景名胜区", "象鼻山景区", "龙脊梯田", "靖江王城", "两江四湖"],
    "丽江": ["丽江古城", "玉龙雪山", "束河古镇", "黑龙潭公园", "白沙古镇"],
    "张家界": ["张家界国家森林公园", "天门山国家森林公园", "张家界大峡谷", "黄龙洞", "宝峰湖"],
    "厦门": ["鼓浪屿", "厦门园林植物园", "南普陀寺", "厦门市博物馆", "环岛路", "胡里山炮台"],
}

# 目录仅在实时 POI 搜索、详情与地理编码都不可用时使用这些城市中心点。
# 它们不是景点的精确坐标；调用方应继续保留 catalog 身份，并优先按地点名导航。
DESTINATION_APPROXIMATE_CENTERS: Dict[str, Tuple[float, float]] = {
    "北京": (39.9042, 116.4074), "杭州": (30.2741, 120.1551), "上海": (31.2304, 121.4737),
    "成都": (30.5728, 104.0668), "西安": (34.3416, 108.9398), "南京": (32.0603, 118.7969),
    "苏州": (31.2989, 120.5853), "广州": (23.1291, 113.2644), "天津": (39.0842, 117.2009),
    "重庆": (29.5630, 106.5516), "深圳": (22.5431, 114.0579), "武汉": (30.5928, 114.3055),
    "长沙": (28.2282, 112.9388), "郑州": (34.7466, 113.6254), "洛阳": (34.6197, 112.4540),
    "开封": (34.7973, 114.3076), "安阳": (36.0976, 114.3928), "石家庄": (38.0428, 114.5149),
    "太原": (37.8706, 112.5489), "济南": (36.6512, 117.1201), "青岛": (36.0671, 120.3826),
    "大连": (38.9140, 121.6147), "沈阳": (41.8057, 123.4315), "长春": (43.8171, 125.3235),
    "哈尔滨": (45.8038, 126.5350), "呼和浩特": (40.8426, 111.7492), "银川": (38.4872, 106.2309),
    "兰州": (36.0611, 103.8343), "西宁": (36.6171, 101.7782), "乌鲁木齐": (43.8256, 87.6168),
    "拉萨": (29.6520, 91.1721), "昆明": (25.0389, 102.7183), "贵阳": (26.6470, 106.6302),
    "南宁": (22.8170, 108.3665), "海口": (20.0440, 110.1999), "三亚": (18.2528, 109.5119),
    "福州": (26.0745, 119.2965), "南昌": (28.6820, 115.8579), "合肥": (31.8206, 117.2272),
    "宁波": (29.8683, 121.5440), "无锡": (31.4912, 120.3119), "扬州": (32.3942, 119.4129),
    "绍兴": (30.0303, 120.5802), "桂林": (25.2742, 110.2991), "丽江": (26.8721, 100.2299),
    "张家界": (29.1171, 110.4792), "厦门": (24.4798, 118.0894),
    "东京": (35.6762, 139.6503), "京都": (35.0116, 135.7681), "大阪": (34.6937, 135.5023),
    "首尔": (37.5665, 126.9780), "新加坡": (1.3521, 103.8198), "曼谷": (13.7563, 100.5018),
    "巴黎": (48.8566, 2.3522), "伦敦": (51.5074, -0.1278), "罗马": (41.9028, 12.4964),
    "巴塞罗那": (41.3874, 2.1686), "阿姆斯特丹": (52.3676, 4.9041), "维也纳": (48.2082, 16.3738),
    "布拉格": (50.0755, 14.4378), "迪拜": (25.2048, 55.2708), "纽约": (40.7128, -74.0060),
    "洛杉矶": (34.0522, -118.2437), "旧金山": (37.7749, -122.4194), "悉尼": (-33.8688, 151.2093),
    "墨尔本": (-37.8136, 144.9631),
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


def approximate_coordinates_for_place(city: str, place_name: str) -> Optional[Tuple[float, float, str]]:
    """Return a deterministic city-level fallback point, never a claimed POI coordinate."""
    canonical = canonical_destination(city) or str(city or "").strip()
    center = DESTINATION_APPROXIMATE_CENTERS.get(canonical)
    if center is None:
        return None
    digest = hashlib.sha1(f"{canonical}|{place_name}".encode("utf-8")).digest()
    latitude_offset = (int.from_bytes(digest[:2], "big") % 2001 - 1000) / 100000
    longitude_offset = (int.from_bytes(digest[2:4], "big") % 2001 - 1000) / 100000
    coordinate_system = "WGS84" if canonical in INTERNATIONAL_DESTINATIONS else "BD09LL"
    return (
        round(center[0] + latitude_offset, 6),
        round(center[1] + longitude_offset, 6),
        coordinate_system,
    )


def is_invalid_poi_name(name: str) -> bool:
    return not str(name or "").strip() or bool(INVALID_POI_NAME_PATTERN.search(str(name).strip()))
