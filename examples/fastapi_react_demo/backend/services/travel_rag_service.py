import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_KNOWLEDGE_PATH = PROJECT_ROOT / "data" / "travel_knowledge" / "cities.json"
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
        items = raw_data.get("items", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _chunk_from_item(item: dict) -> Optional[TravelKnowledgeChunk]:
    city = _safe_text(item.get("city"))
    source = _safe_text(item.get("source"))
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
    )


@lru_cache(maxsize=8)
def load_travel_knowledge(path: str = str(DEFAULT_KNOWLEDGE_PATH)) -> List[TravelKnowledgeChunk]:
    knowledge_path = Path(path)
    if not knowledge_path.exists():
        return []

    with knowledge_path.open("r", encoding="utf-8") as file:
        raw_data = json.load(file)

    chunks: List[TravelKnowledgeChunk] = []
    for item in _normalize_items(raw_data):
        chunk = _chunk_from_item(item)
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
    searchable_text = " ".join([chunk.city, chunk.title, *chunk.keywords, chunk.content])

    if _contains(query, chunk.city):
        score += 8.0
        matched_terms.append(chunk.city)

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
