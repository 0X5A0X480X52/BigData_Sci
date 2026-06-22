"""OpenAlex query planning for research questions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from research_agent.adapters.llm_chat import OpenAICompatibleChatClient
from research_agent.core.utils import simple_tokenize


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "based", "by", "for", "from", "in",
    "into", "is", "of", "on", "or", "paper", "papers", "related", "review",
    "study", "survey", "the", "to", "with", "write",
    "帮", "我", "写", "一", "篇", "相关", "综", "述", "的", "和", "与", "及",
    "研究", "调研", "综述", "论文", "领域", "请", "生成",
}


@dataclass
class OpenAlexQueryPlan:
    """A normalized query plan consumed by corpus-building skills."""

    original_question: str
    primary_query: str
    alternate_queries: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    method: str = "rule_based"
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "original_question": self.original_question,
            "primary_query": self.primary_query,
            "alternate_queries": list(self.alternate_queries),
            "keywords": list(self.keywords),
            "method": self.method,
            "warnings": list(self.warnings),
        }


def plan_openalex_query(question: str, use_llm: bool = False) -> OpenAlexQueryPlan:
    """Return an OpenAlex-friendly query, using an LLM when configured."""
    fallback = _rule_based_query_plan(question)
    if not use_llm:
        return fallback

    try:
        client = OpenAICompatibleChatClient()
        response = client.complete_json(
            system=(
                "You convert user research requests into OpenAlex Works search queries. "
                "Return concise English scholarly search terms, not instructions. "
                "Preserve important technical phrases such as Transformer, RAG, PageRank, or Neo4j."
            ),
            user=str({
                "question": question,
                "rule_based_fallback": fallback.as_dict(),
                "constraints": [
                    "primary_query must be English or widely used technical terms",
                    "do not include words like write, review, survey, help me",
                    "alternate_queries should be short variants for retry",
                ],
            }),
            schema_hint=(
                '{"primary_query":"transformer neural networks attention",'
                '"alternate_queries":["attention is all you need transformer",'
                '"transformer architecture natural language processing"],'
                '"keywords":["transformer","attention","neural networks"],'
                '"rationale":"short reason"}'
            ),
        )
        primary = _clean_query(str(response.get("primary_query") or ""))
        if not primary:
            raise ValueError("LLM returned an empty primary_query")
        alternates = [
            _clean_query(str(item))
            for item in response.get("alternate_queries", [])
            if _clean_query(str(item))
        ][:5]
        keywords = [
            _clean_query(str(item))
            for item in response.get("keywords", [])
            if _clean_query(str(item))
        ][:12]
        return OpenAlexQueryPlan(
            original_question=question,
            primary_query=primary,
            alternate_queries=_unique([q for q in alternates if q != primary]),
            keywords=keywords or fallback.keywords,
            method="llm",
        )
    except Exception as exc:
        fallback.method = "rule_based_after_llm_failure"
        fallback.warnings.append(f"LLM query planning failed; used rule-based query: {exc}")
        return fallback


def _rule_based_query_plan(question: str) -> OpenAlexQueryPlan:
    english_phrases = re.findall(r"[A-Za-z][A-Za-z0-9_\-]*(?:\s+[A-Za-z][A-Za-z0-9_\-]*){0,4}", question)
    terms: List[str] = []
    for phrase in english_phrases:
        for token in simple_tokenize(phrase):
            if _keep_token(token):
                terms.append(token)
    for token in simple_tokenize(question):
        if _keep_token(token):
            terms.append(token)

    keywords = _unique(terms)[:12]
    if not keywords:
        keywords = [question.strip()]

    primary = _clean_query(" ".join(keywords[:6])) or question.strip()
    alternates = []
    if len(keywords) > 3:
        alternates.append(_clean_query(" ".join(keywords[:3])))
    if len(keywords) > 6:
        alternates.append(_clean_query(" ".join(keywords[3:9])))
    return OpenAlexQueryPlan(
        original_question=question,
        primary_query=primary,
        alternate_queries=_unique([q for q in alternates if q and q != primary]),
        keywords=keywords,
    )


def _keep_token(token: str) -> bool:
    token = token.strip().lower()
    if not token or token in _STOPWORDS:
        return False
    if re.fullmatch(r"[\u4e00-\u9fff]", token):
        return False
    return len(token) > 1 or token.isdigit()


def _clean_query(value: str) -> str:
    value = re.sub(r"[“”\"'`]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:240]


def _unique(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
