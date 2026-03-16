"""Minimal Markdown blog engine with YAML frontmatter."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

try:
    import markdown as _md  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _md = None  # type: ignore[assignment]

_DEFAULT_BLOG_DIR = Path(__file__).resolve().parent.parent / "content" / "blog"

# Markdown extensions for nicer output
_MD_EXTENSIONS = ["fenced_code", "codehilite", "tables", "toc", "smarty"]


@dataclass
class BlogPost:
    title: str
    slug: str
    date: str
    description: str
    tags: List[str]
    html: str
    raw_markdown: str = ""
    reading_minutes: int = 1


def _estimate_reading_time(text: str) -> int:
    words = len(text.split())
    return max(1, round(words / 200))


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split YAML frontmatter from Markdown body.

    Expects the file to start with ``---`` on the first line.
    Returns (metadata_dict, markdown_body).
    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return meta, body


def _render_markdown(text: str) -> str:
    if _md is None:
        # Fallback: wrap in <pre> if markdown lib is missing
        return f"<pre>{text}</pre>"
    md = _md.Markdown(extensions=_MD_EXTENSIONS, output_format="html")
    return md.convert(text)


def load_posts(directory: str | Path | None = None) -> List[BlogPost]:
    """Scan *directory* for ``.md`` files and return parsed ``BlogPost`` list."""
    blog_dir = Path(directory) if directory else _DEFAULT_BLOG_DIR
    if not blog_dir.is_dir():
        return []

    posts: list[BlogPost] = []
    for filepath in sorted(blog_dir.glob("*.md")):
        raw = filepath.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        if not meta.get("title") or not meta.get("slug"):
            continue
        html = _render_markdown(body)
        posts.append(
            BlogPost(
                title=meta["title"],
                slug=meta["slug"],
                date=str(meta.get("date", "")),
                description=meta.get("description", ""),
                tags=meta.get("tags", []),
                html=html,
                raw_markdown=body,
                reading_minutes=_estimate_reading_time(body),
            )
        )

    # Sort newest-first
    posts.sort(key=lambda p: p.date, reverse=True)
    return posts


def get_all_posts(directory: str | Path | None = None) -> List[BlogPost]:
    """Return all posts sorted by date (newest first)."""
    return load_posts(directory)


def get_post(slug: str, directory: str | Path | None = None) -> Optional[BlogPost]:
    """Return a single post by slug, or ``None``."""
    for post in load_posts(directory):
        if post.slug == slug:
            return post
    return None
