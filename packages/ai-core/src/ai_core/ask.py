from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AiWikiPage:
    file_id: str
    path: str
    title: str
    text: str


@dataclass(frozen=True)
class AiWikiCitation:
    file_id: str
    path: str
    title: str
    excerpt: str
    score: int


@dataclass(frozen=True)
class AiWikiAnswer:
    schema_version: str
    question: str
    answer: str
    citation_count: int
    citations: list[AiWikiCitation]
    model_status: str


_TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokens(value: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in _TOKEN_PATTERN.finditer(value.lower()):
        token = match.group(0)
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _plain_text(markdown: str) -> str:
    text = re.sub(r"\A---\n.*?\n---", "", markdown, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\[\[([^\]\n]+)\]\]", r"\1", text)
    text = text.replace("*", "").replace("_", "").replace("#", "")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _score_page(page: AiWikiPage, query_tokens: list[str]) -> int:
    haystack_title = page.title.lower()
    haystack_text = page.text.lower()
    score = 0
    for token in query_tokens:
        if token in haystack_title:
            score += 6
        score += min(haystack_text.count(token), 5)
    return score


def _excerpt(page: AiWikiPage, query_tokens: list[str]) -> str:
    lines = _plain_text(page.text).splitlines()
    for line in lines:
        lower_line = line.lower()
        if any(token in lower_line for token in query_tokens):
            return line[:280]
    for line in lines:
        if line and not line.startswith("---"):
            return line[:280]
    return page.title


def answer_ai_wiki(question: str, pages: list[AiWikiPage], *, limit: int = 5) -> AiWikiAnswer:
    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("AI wiki question must be non-empty")

    query_tokens = _tokens(normalized_question)
    if not query_tokens:
        query_tokens = [normalized_question.lower()]

    scored_pages = [
        (score, page)
        for page in pages
        if (score := _score_page(page, query_tokens)) > 0
    ]
    scored_pages.sort(key=lambda item: (-item[0], item[1].path))
    citations = [
        AiWikiCitation(
            file_id=page.file_id,
            path=page.path,
            title=page.title,
            excerpt=_excerpt(page, query_tokens),
            score=score,
        )
        for score, page in scored_pages[:limit]
    ]

    if not pages:
        answer = "AI Wiki has not been compiled yet. Compile the knowledge base first, then ask again."
    elif not citations:
        answer = (
            "No directly matching AI Wiki page was found. Try compiling the wiki again or ask with more specific "
            "keywords from your notes."
        )
    else:
        lines = [
            "Based on the local AI Wiki, the most relevant references are:",
            "",
        ]
        lines.extend(
            f"{index}. {citation.title}: {citation.excerpt}"
            for index, citation in enumerate(citations, start=1)
        )
        lines.extend(
            [
                "",
                "This is a deterministic local answer skeleton. A real model provider can later use the same citations "
                "to generate a fuller response.",
            ]
        )
        answer = "\n".join(lines)

    return AiWikiAnswer(
        schema_version="v1",
        question=normalized_question,
        answer=answer,
        citation_count=len(citations),
        citations=citations,
        model_status="local_deterministic",
    )
