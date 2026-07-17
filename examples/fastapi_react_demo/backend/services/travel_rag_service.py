import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_KNOWLEDGE_PATH = PROJECT_ROOT / "data" / "travel_knowledge" / "cities.json"
INTERNATIONAL_KNOWLEDGE_PATH = PROJECT_ROOT / "data" / "travel_knowledge" / "international_destinations.json"
MIN_CONTEXT_SCORE = 4.0
DEFAULT_TOP_K = 5
MAX_CONTEXT_CHARS = 1800

CHINESE_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}")
STOP_CHARS = set("的一是在和与及或也就都很到去从把给帮我想要可以如何怎么什么一下一个安排规划旅游旅行行程日天")


@dataclass(frozen=True)
class TravelKnowledgeChunk:
    city: str
    source: str
    title: str
    keywords: List[str]
    content: str
    official_name: str = ""
    province: str = ""
    source_url: str = ""
    country_code: str = "CN"
    country_name: str = "中国"
    city_en: str = ""
    local_names: List[str] = field(default_factory=list)
    region: str = ""
    knowledge_type: str = ""
    updated_at: str = ""
    valid_until: str = ""

@dataclass(frozen=True)
class TravelKnowledgeMatch:
    chunk: TravelKnowledgeChunk
    score: float
    matched_terms: List[str]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_items(raw_data: Any) -> List[dict]:
    if isinstance(raw_data, list):
        return [item for item in raw_data if isinstance(item, dict)]
    if isinstance(raw_data, dict):
        destinations = raw_data.get("destinations", [])
        if isinstance(destinations, list) and destinations:
            items: List[dict] = []
            labels = {
                "destination_intro": "目的地介绍", "local_transport": "城市交通", "food": "典型饮食",
                "payment": "支付", "communication": "通信", "etiquette": "礼仪",
                "safety": "安全", "emergency": "紧急信息",
            }
            for destination in destinations:
                if not isinstance(destination, dict):
                    continue
                facts = destination.get("facts", {})
                if not isinstance(facts, dict):
                    continue
                aliases = [destination.get("city"), destination.get("city_en"), *(destination.get("local_names") or [])]
                for knowledge_type, content in facts.items():
                    items.append({
                        **destination,
                        "source": "international_official",
                        "title": f"{destination.get('city', '')}{labels.get(knowledge_type, knowledge_type)}",
                        "keywords": [str(item) for item in aliases if item] + [labels.get(knowledge_type, knowledge_type)],
                        "content": content,
                        "knowledge_type": knowledge_type,
                    })
            return items
        items = raw_data.get("items", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _chunk_from_item(item: dict) -> Optional[TravelKnowledgeChunk]:
    city = _safe_text(item.get("city"))
    official_name = _safe_text(item.get("official_name"))
    province = _safe_text(item.get("province"))
    source = _safe_text(item.get("source"))
    source_url = _safe_text(item.get("source_url"))
    title = _safe_text(item.get("title"))
    content = _safe_text(item.get("content"))
    raw_keywords = item.get("keywords", [])
    keywords = [_safe_text(value) for value in raw_keywords if _safe_text(value)] if isinstance(raw_keywords, list) else []

    if not city or not source or not title or not content:
        return None

    return TravelKnowledgeChunk(
        city=city,
        source=source,
        title=title,
        keywords=keywords,
        content=content,
        official_name=official_name,
        province=province,
        source_url=source_url,
        country_code=_safe_text(item.get("country_code")) or "CN",
        country_name=_safe_text(item.get("country_name_zh")) or "中国",
        city_en=_safe_text(item.get("city_en")),
        local_names=[_safe_text(value) for value in item.get("local_names", []) if _safe_text(value)],
        region=_safe_text(item.get("region")),
        knowledge_type=_safe_text(item.get("knowledge_type")) or source,
        updated_at=_safe_text(item.get("updated_at")),
        valid_until=_safe_text(item.get("valid_until")),
    )


@lru_cache(maxsize=8)
def load_travel_knowledge(path: str = str(DEFAULT_KNOWLEDGE_PATH)) -> List[TravelKnowledgeChunk]:
    knowledge_path = Path(path)
    if not knowledge_path.exists():
        return []

    with knowledge_path.open("r", encoding="utf-8") as file:
        raw_data = json.load(file)

    chunks: List[TravelKnowledgeChunk] = []
    payloads = [raw_data]
    if knowledge_path.resolve() == DEFAULT_KNOWLEDGE_PATH.resolve() and INTERNATIONAL_KNOWLEDGE_PATH.exists():
        try:
            payloads.append(json.loads(INTERNATIONAL_KNOWLEDGE_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass
    for payload in payloads:
        for item in _normalize_items(payload):
            try:
                chunk = _chunk_from_item(item)
            except (TypeError, ValueError):
                continue
            if chunk is not None:
                chunks.append(chunk)
    return chunks


def _contains(text: str, term: str) -> bool:
    return bool(text and term and term.lower() in text.lower())


def _query_tokens(query: str) -> set:
    return {token.lower() for token in TOKEN_PATTERN.findall(query) if len(token.strip()) >= 2}


def _meaningful_chinese_chars(text: str) -> set:
    return {char for char in CHINESE_CHAR_PATTERN.findall(text) if char not in STOP_CHARS}


def _score_chunk(query: str, chunk: TravelKnowledgeChunk) -> TravelKnowledgeMatch:
    score = 0.0
    matched_terms: List[str] = []
    searchable_text = " ".join([chunk.city, chunk.city_en, *chunk.local_names, chunk.title, *chunk.keywords, chunk.content])

    if _contains(query, chunk.city):
        score += 8.0
        matched_terms.append(chunk.city)

    aliases = [chunk.city_en, *chunk.local_names]
    if any(_contains(query, alias) for alias in aliases if alias):
        score += 8.0
        matched_terms.extend(alias for alias in aliases if alias and _contains(query, alias))

    if _contains(query, chunk.title):
        score += 6.0
        matched_terms.append(chunk.title)

    for keyword in chunk.keywords:
        if _contains(query, keyword):
            score += 4.0
            matched_terms.append(keyword)

    query_tokens = _query_tokens(query)
    chunk_tokens = _query_tokens(searchable_text)
    token_overlap = query_tokens.intersection(chunk_tokens)
    if token_overlap:
        score += len(token_overlap) * 1.5
        matched_terms.extend(sorted(token_overlap))

    query_chars = _meaningful_chinese_chars(query)
    chunk_chars = _meaningful_chinese_chars(searchable_text)
    char_overlap = query_chars.intersection(chunk_chars)
    if char_overlap:
        score += min(len(char_overlap), 8) * 0.25

    unique_terms = []
    seen = set()
    for term in matched_terms:
        if term and term not in seen:
            unique_terms.append(term)
            seen.add(term)

    return TravelKnowledgeMatch(chunk=chunk, score=score, matched_terms=unique_terms)


def search_travel_knowledge(
    query: str,
    chunks: Optional[Sequence[TravelKnowledgeChunk]] = None,
    top_k: int = DEFAULT_TOP_K,
) -> List[TravelKnowledgeMatch]:
    query_text = _safe_text(query)
    if not query_text:
        return []

    source_chunks = list(chunks) if chunks is not None else load_travel_knowledge()
    matches = [_score_chunk(query_text, chunk) for chunk in source_chunks]
    ranked = sorted(
        [match for match in matches if match.score > 0],
        key=lambda item: item.score,
        reverse=True,
    )
    return ranked[:max(1, top_k)]


def _format_context(matches: Iterable[TravelKnowledgeMatch], max_chars: int) -> str:
    lines = [
        "旅行知识库检索结果：",
        "使用规则：优先参考这些城市介绍、攻略和 POI 信息；不要把它们说成实时数据；不要暴露内部检索字段或文件路径。",
    ]

    for index, match in enumerate(matches, start=1):
        chunk = match.chunk
        lines.append(
            f"{index}. [{chunk.city} / {chunk.source} / {chunk.title}] {chunk.content}"
        )

    context = "\n".join(lines)
    return context[:max_chars].rstrip()


def build_travel_rag_context(
    query: str,
    chunks: Optional[Sequence[TravelKnowledgeChunk]] = None,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = MIN_CONTEXT_SCORE,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    matches = [
        match
        for match in search_travel_knowledge(query=query, chunks=chunks, top_k=top_k)
        if match.score >= min_score
    ]
    if not matches:
        return ""
    return _format_context(matches, max_chars=max_chars)
