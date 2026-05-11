from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class SourceNote:
    file_id: str
    path: str
    text: str
    content_hash: str


@dataclass(frozen=True)
class AiWikiArtifact:
    title: str
    path: str
    text: str
    source_file_id: str
    source_path: str
    source_content_hash: str


@dataclass(frozen=True)
class AiWikiCompileResult:
    schema_version: str
    generated_at: str
    source_count: int
    artifact_count: int
    artifacts: list[AiWikiArtifact]
    index_path: str
    index_text: str


_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_WIKI_LINK_PATTERN = re.compile(r"\[\[([^\]\n]+)\]\]")
_SAFE_SLUG_PATTERN = re.compile(r"[^a-z0-9\u4e00-\u9fff._-]+")


def _note_title(path: str, text: str) -> str:
    for line in text.splitlines():
        match = _HEADING_PATTERN.match(line.strip())
        if match and len(match.group(1)) == 1:
            return match.group(2).strip()
    name = PurePosixPath(path).name
    return re.sub(r"\.(md|markdown)$", "", name, flags=re.IGNORECASE) or name


def _plain_line(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", value)
    value = value.replace("*", "").replace("_", "").replace("#", "")
    return value.strip()


def _summary(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("```"):
            continue
        plain = _plain_line(stripped)
        if plain:
            return plain[:240]
    return "No prose summary was found; this page was compiled from the source note structure."


def _outline(text: str) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _HEADING_PATTERN.match(line.strip())
        if match:
            items.append((len(match.group(1)), match.group(2).strip()))
    return items


def _wiki_links(text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for match in _WIKI_LINK_PATTERN.finditer(text):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if target and target not in seen:
            seen.add(target)
            links.append(target)
    return links


def _slug(title: str, source_path: str) -> str:
    normalized = title.strip().lower()
    normalized = _SAFE_SLUG_PATTERN.sub("-", normalized).strip("-._")
    if not normalized:
        normalized = "untitled"
    digest = hashlib.sha1(source_path.encode("utf-8")).hexdigest()[:8]
    return f"{normalized}-{digest}"


def _sources_hash(notes: list[SourceNote]) -> str:
    payload = "\n".join(
        f"{note.file_id}\t{note.path}\t{note.content_hash}"
        for note in sorted(notes, key=lambda item: item.path)
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _render_artifact(note: SourceNote, *, generated_at: str) -> AiWikiArtifact:
    title = _note_title(note.path, note.text)
    summary = _summary(note.text)
    outline = _outline(note.text)
    links = _wiki_links(note.text)
    path = f".ai/wiki/{_slug(title, note.path)}.md"
    body: list[str] = [
        "---",
        "schema_version: v1",
        "page_type: summary",
        f"title: {title}",
        "ai_generated: true",
        "user_edited: false",
        "locked: false",
        f"last_ai_compiled_at: {generated_at}",
        f"source_file_id: {note.file_id}",
        f"source_path: {note.path}",
        f"source_content_hash: {note.content_hash}",
        "---",
        "",
        f"# {title}",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Outline",
        "",
    ]
    if outline:
        body.extend(f"- {'  ' * max(level - 1, 0)}{heading}" for level, heading in outline)
    else:
        body.append("- No headings found.")
    body.extend(["", "## Related Wiki Links", ""])
    if links:
        body.extend(f"- [[{link}]]" for link in links)
    else:
        body.append("- No wiki links found.")
    body.extend(
        [
            "",
            "## Source",
            "",
            f"- Source note: [[{title}]]",
            f"- Path: `{note.path}`",
            f"- Content hash: `{note.content_hash}`",
            "",
        ]
    )
    return AiWikiArtifact(
        title=title,
        path=path,
        text="\n".join(body),
        source_file_id=note.file_id,
        source_path=note.path,
        source_content_hash=note.content_hash,
    )


def _render_index(artifacts: list[AiWikiArtifact], *, generated_at: str, sources_hash: str) -> str:
    body = [
        "---",
        "schema_version: v1",
        "page_type: summary",
        "title: AI Knowledge Index",
        "ai_generated: true",
        "user_edited: false",
        "locked: false",
        f"last_ai_compiled_at: {generated_at}",
        f"last_compiled_from_sources_hash: {sources_hash}",
        "---",
        "",
        "# AI Knowledge Index",
        "",
        f"Generated at: `{generated_at}`",
        f"Source hash: `{sources_hash}`",
        "",
        "## Pages",
        "",
    ]
    if artifacts:
        body.extend(f"- [[{artifact.title}]] - `{artifact.source_path}`" for artifact in artifacts)
    else:
        body.append("- No source notes were available.")
    body.append("")
    return "\n".join(body)


def compile_ai_wiki(notes: list[SourceNote], *, generated_at: str) -> AiWikiCompileResult:
    active_notes = sorted(notes, key=lambda item: item.path)
    artifacts = [_render_artifact(note, generated_at=generated_at) for note in active_notes]
    sources_hash = _sources_hash(active_notes)
    index_text = _render_index(artifacts, generated_at=generated_at, sources_hash=sources_hash)
    return AiWikiCompileResult(
        schema_version="v1",
        generated_at=generated_at,
        source_count=len(active_notes),
        artifact_count=len(artifacts),
        artifacts=artifacts,
        index_path=".ai/index.md",
        index_text=index_text,
    )
