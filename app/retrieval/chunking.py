"""
Chunking for the RAG corpus.

Deliberately chunks by markdown section (## headers), not fixed-size
windows. This matters for two reasons:

1. Citations need to point somewhere meaningful. "doc:runbook-api-latency
   -spike#diagnostic-steps" is useful to a human reading a postmortem;
   "doc:runbook-api-latency-spike:chunk_7" is not.
2. Sections in these runbooks are semantically coherent units (symptoms,
   root causes, diagnostics, remediation) — splitting mid-section by
   character count would cut a root-cause explanation in half.
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
    """Split a runbook markdown file into one chunk per ## section.
    The document title (# header) is prepended to every chunk's text
    for context, since embeddings do better with some surrounding
    context than a bare section in isolation."""

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
