from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from services.travel_rag_service import TravelKnowledgeChunk, load_travel_knowledge, search_travel_knowledge


GENERIC_KEYWORDS = {
    "百度百科",
    "百度介绍",
    "百科摘要",
    "城市介绍",
    "地级市",
    "旅游攻略",
    "两日游",
    "三日游",
    "景点",
    "美食",
    "在哪",
    "区位",
    "所属地区",
    "地理位置",
}

SOURCE_ORDER = {
    "baidu_intro": 0,
    "geo_location": 1,
    "travel_guide": 2,
}


def _truncate_text(text: str, max_chars: int) -> str:
    value = text.strip()
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars].rstrip()}..."


def _preferred_chunk(chunks: Iterable[TravelKnowledgeChunk], source: str) -> Optional[TravelKnowledgeChunk]:
    for chunk in chunks:
        if chunk.source == source:
            return chunk
    return None


def _collect_tags(chunks: List[TravelKnowledgeChunk], city: str, official_name: str, province: str) -> List[str]:
    blocked = {city, official_name, province, *GENERIC_KEYWORDS}
    tags: List[str] = []

    for chunk in sorted(chunks, key=lambda item: SOURCE_ORDER.get(item.source, 99)):
        for keyword in chunk.keywords:
            clean_keyword = keyword.strip()
            if not clean_keyword or clean_keyword in blocked or clean_keyword in tags:
                continue
            tags.append(clean_keyword)
            if len(tags) >= 6:
                return tags

    return tags


def build_travel_knowledge_cities_response() -> Dict[str, object]:
    chunks = load_travel_knowledge()
    grouped: Dict[str, List[TravelKnowledgeChunk]] = defaultdict(list)

    for chunk in chunks:
        grouped[chunk.city].append(chunk)

    cities = []
    for city, city_chunks in grouped.items():
        ordered_chunks = sorted(city_chunks, key=lambda item: SOURCE_ORDER.get(item.source, 99))
        intro = _preferred_chunk(ordered_chunks, "baidu_intro") or ordered_chunks[0]
        geo = _preferred_chunk(ordered_chunks, "geo_location")
        guide = _preferred_chunk(ordered_chunks, "travel_guide")
        official_name = intro.official_name or city
        province = intro.province or (geo.province if geo else "")

        cities.append({
            "city": city,
            "official_name": official_name,
            "province": province,
            "country_code": intro.country_code,
            "country_name": intro.country_name,
            "city_en": intro.city_en,
            "local_names": intro.local_names,
            "region": intro.region,
            "scope": "domestic" if intro.country_code == "CN" else "international",
            "updated_at": max((chunk.updated_at for chunk in ordered_chunks), default=""),
            "knowledge_types": sorted({chunk.knowledge_type for chunk in ordered_chunks if chunk.knowledge_type}),
            "tags": _collect_tags(ordered_chunks, city, official_name, province),
            "summary": _truncate_text(intro.content.replace("百度百科摘要整理：", ""), 150),
            "best_for": _truncate_text(guide.content if guide else intro.content, 190),
            "tip": _truncate_text(geo.content if geo else intro.content, 140),
            "chunks": [
                {
                    "title": chunk.title,
                    "source": chunk.source,
                    "content": chunk.content,
                    "knowledge_type": chunk.knowledge_type,
                    "updated_at": chunk.updated_at,
                }
                for chunk in ordered_chunks
            ],
        })

    cities.sort(key=lambda item: (str(item["province"]), str(item["city"])))
    return {
        "total_cities": len(cities),
        "total_chunks": len(chunks),
        "cities": cities,
    }


def build_travel_knowledge_search_response(query: str, top_k: int = 8) -> Dict[str, object]:
    query_text = query.strip()
    matches = search_travel_knowledge(query_text, top_k=max(1, min(top_k, 20))) if query_text else []

    items = []
    for match in matches:
        chunk = match.chunk
        items.append({
            "city": chunk.city,
            "official_name": chunk.official_name or chunk.city,
            "province": chunk.province,
            "country_code": chunk.country_code,
            "country_name": chunk.country_name,
            "city_en": chunk.city_en,
            "local_names": chunk.local_names,
            "region": chunk.region,
            "scope": "domestic" if chunk.country_code == "CN" else "international",
            "knowledge_type": chunk.knowledge_type,
            "updated_at": chunk.updated_at,
            "title": chunk.title,
            "source": chunk.source,
            "source_url": chunk.source_url,
            "snippet": _truncate_text(chunk.content, 240),
            "score": round(match.score, 3),
            "matched_terms": match.matched_terms,
        })

    return {
        "query": query_text,
        "top_k": max(1, min(top_k, 20)),
        "items": items,
    }
