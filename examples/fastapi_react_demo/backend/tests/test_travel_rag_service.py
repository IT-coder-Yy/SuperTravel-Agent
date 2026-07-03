import sys
import unittest
import json
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from services.travel_rag_service import (  # noqa: E402
    DEFAULT_KNOWLEDGE_PATH,
    TravelKnowledgeChunk,
    build_travel_rag_context,
    load_travel_knowledge,
    search_travel_knowledge,
)


class TravelRagServiceTests(unittest.TestCase):
    def test_search_returns_city_related_chunks(self):
        chunks = [
            TravelKnowledgeChunk(
                city="杭州",
                source="poi",
                title="西湖",
                keywords=["杭州", "西湖"],
                content="西湖适合半日游。",
            ),
            TravelKnowledgeChunk(
                city="北京",
                source="poi",
                title="故宫",
                keywords=["北京", "故宫"],
                content="故宫适合历史文化游。",
            ),
        ]

        matches = search_travel_knowledge("帮我规划杭州西湖两日游", chunks=chunks, top_k=2)

        self.assertEqual(matches[0].chunk.city, "杭州")
        self.assertGreater(matches[0].score, 0)

    def test_build_context_returns_empty_for_unrelated_query(self):
        chunks = [
            TravelKnowledgeChunk(
                city="杭州",
                source="poi",
                title="西湖",
                keywords=["杭州", "西湖"],
                content="西湖适合半日游。",
            ),
        ]

        context = build_travel_rag_context("解释一下 Python GIL", chunks=chunks)

        self.assertEqual(context, "")

    def test_default_knowledge_covers_prefecture_level_cities(self):
        chunks = load_travel_knowledge()
        cities = {chunk.city for chunk in chunks}

        self.assertEqual(len(cities), 293)
        self.assertEqual(len(chunks), 879)

        for city in ["苏州", "深圳", "成都", "西安", "哈尔滨", "拉萨"]:
            self.assertIn(city, cities)
            sources = {chunk.source for chunk in chunks if chunk.city == city}
            self.assertEqual(sources, {"baidu_intro", "geo_location", "travel_guide"})

        for non_prefecture_city in [
            "北京",
            "上海",
            "天津",
            "重庆",
            "香港",
            "澳门",
            "恩施土家族苗族自治州",
            "锡林郭勒盟",
            "阿克苏地区",
        ]:
            self.assertNotIn(non_prefecture_city, cities)

    def test_default_knowledge_records_baidu_fallback_status(self):
        with DEFAULT_KNOWLEDGE_PATH.open("r", encoding="utf-8") as file:
            raw_data = json.load(file)

        metadata = raw_data["metadata"]
        self.assertEqual(metadata["city_count"], 293)
        self.assertEqual(metadata["item_count"], 879)
        self.assertEqual(
            metadata["baidu_fetched_city_count"] + metadata["baidu_fallback_city_count"],
            293,
        )
        self.assertIn("安全验证", metadata["baidu_note"])

    def test_default_knowledge_matches_new_city_guide_geo_and_baidu_intro(self):
        guide_matches = search_travel_knowledge("帮我规划苏州两日游")
        geo_matches = search_travel_knowledge("苏州在哪个省，地理位置是什么")
        baidu_matches = search_travel_knowledge("苏州百度介绍")

        self.assertEqual(guide_matches[0].chunk.city, "苏州")
        self.assertEqual(guide_matches[0].chunk.source, "travel_guide")
        self.assertEqual(geo_matches[0].chunk.city, "苏州")
        self.assertEqual(geo_matches[0].chunk.source, "geo_location")
        self.assertEqual(baidu_matches[0].chunk.city, "苏州")
        self.assertEqual(baidu_matches[0].chunk.source, "baidu_intro")


if __name__ == "__main__":
    unittest.main()
