"""Chunk runbooks by markdown section (## headers), not fixed-size windows.

Two reasons: citations should point somewhere meaningful ("...#diagnostic-steps",
not "...:chunk_7"), and each section is already a coherent unit — splitting by
character count would cut an explanation in half.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Chunk:
    doc_id: str            # e.g. "runbook-api-latency-spike"
    section_slug: str      # e.g. "diagnostic-steps"
    section_title: str     # e.g. "Diagnostic Steps"
    text: str
    source_ref: str        # e.g. "doc:runbook-api-latency-spike#diagnostic-steps"


def _slugify(title: str) -> str:
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug


def chunk_markdown_file(filepath: Path) -> list[Chunk]:
    """Split a runbook into one chunk per ## section. The doc title (# header) is
    prepended to each chunk so embeddings have some context, not a bare section."""

    text = filepath.read_text(encoding="utf-8")
    doc_id = filepath.stem.replace("_", "-")  # e.g. "runbook_api_latency_spike" -> "runbook-api-latency-spike"

    lines = text.split("\n")
    doc_title = lines[0].lstrip("# ").strip() if lines[0].startswith("#") else doc_id

    chunks: list[Chunk] = []
    current_title = None
    current_lines: list[str] = []

    def flush():
        if current_title is not None and current_lines:
            body = "\n".join(current_lines).strip()
            if body:
                slug = _slugify(current_title)
                chunks.append(
                    Chunk(
                        doc_id=doc_id,
                        section_slug=slug,
                        section_title=current_title,
                        text=f"# {doc_title}\n\n## {current_title}\n\n{body}",
                        source_ref=f"doc:{doc_id}#{slug}",
                    )
                )

    for line in lines[1:]:
        if line.startswith("## "):
            flush()
            current_title = line.lstrip("# ").strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()

    return chunks


def chunk_all_runbooks(runbooks_dir: Path) -> list[Chunk]:
    all_chunks: list[Chunk] = []
    for md_file in sorted(runbooks_dir.glob("*.md")):
        all_chunks.extend(chunk_markdown_file(md_file))
    return all_chunks
