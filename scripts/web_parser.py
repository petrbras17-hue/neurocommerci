"""
Web Parser via Firecrawl → Pinecone Vector Pipeline

Парсит веб-контент через Firecrawl API, векторизует через Gemini
и сохраняет в Pinecone для семантического поиска.

Поддерживает:
- scrape: парсинг одной страницы
- crawl: обход всего сайта
- search: поиск по интернету + парсинг результатов
- map: карта ссылок сайта

Использование:
    # Спарсить одну страницу
    python scripts/web_parser.py scrape "https://vc.ru/marketing"

    # Поиск по теме и парсинг результатов
    python scripts/web_parser.py search "нейрокомментинг телеграм маркетинг"

    # Обойти весь сайт (до N страниц)
    python scripts/web_parser.py crawl "https://example.com" --limit 10

    # Карта ссылок сайта
    python scripts/web_parser.py map "https://example.com"

    # Поиск по Pinecone
    python scripts/web_parser.py find "маркетинг в телеграм"
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def get_env(key, default=None):
    """Load from .env file or environment."""
    val = os.environ.get(key)
    if val:
        return val
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return default


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "parsed"


def get_firecrawl():
    from firecrawl import FirecrawlApp
    return FirecrawlApp(api_key=get_env("FIRECRAWL_API_KEY"))


def get_pinecone_index():
    from pinecone import Pinecone
    pc = Pinecone(api_key=get_env("PINECONE_API_KEY"))
    return pc.Index("social-content")


def get_embedding(text: str) -> list:
    import google.generativeai as genai
    genai.configure(api_key=get_env("GEMINI_API_KEY"))
    result = genai.embed_content(
        model="models/gemini-embedding-001",
        content=text,
    )
    return result["embedding"]


def chunk_text(text: str, max_chars: int = 2000, overlap: int = 200) -> list:
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        if end < len(text):
            last_period = chunk.rfind(".")
            last_newline = chunk.rfind("\n")
            break_at = max(last_period, last_newline)
            if break_at > max_chars // 2:
                chunk = chunk[: break_at + 1]
                end = start + break_at + 1
        chunks.append(chunk.strip())
        start = end - overlap
    return chunks


def url_to_id(url: str) -> str:
    """Create a short stable ID from URL."""
    import hashlib
    return hashlib.md5(url.encode()).hexdigest()[:12]


def vectorize_and_store(text: str, metadata: dict, source_id: str):
    """Chunk, embed, and store text in Pinecone."""
    chunks = chunk_text(text)
    print(f"  Split into {len(chunks)} chunks")

    index = get_pinecone_index()
    vectors = []

    for i, chunk in enumerate(chunks):
        chunk_id = f"{source_id}_chunk_{i}"
        try:
            embedding = get_embedding(chunk)
            meta = {
                **metadata,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "text": chunk[:1000],
            }
            vectors.append({
                "id": chunk_id,
                "values": embedding,
                "metadata": meta,
            })
        except Exception as e:
            print(f"  Embedding failed for chunk {i}: {e}")

    if vectors:
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            index.upsert(vectors=vectors[i : i + batch_size])
        print(f"  Stored {len(vectors)} vectors in Pinecone")

    return len(vectors)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_scrape(url: str, vectorize: bool = True):
    """Scrape a single page."""
    print(f"\n{'='*60}")
    print(f"Scraping: {url}")
    print(f"{'='*60}")

    app = get_firecrawl()

    try:
        result = app.scrape(url, formats=["markdown"], only_main_content=True, wait_for=3000)
    except Exception as e:
        print(f"  Scrape failed: {e}")
        return None

    md = result.markdown or ""
    meta = result.metadata or {}

    title = getattr(meta, "title", "") or meta.get("title", "") if isinstance(meta, dict) else getattr(meta, "title", "")
    description = getattr(meta, "description", "") or ""

    print(f"  Title: {title}")
    print(f"  Content: {len(md)} chars")

    if not md or len(md) < 50:
        print("  WARNING: Very little content scraped. Site may block scraping.")
        return None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    page_id = url_to_id(url)
    domain = urlparse(url).netloc

    # Save local backup
    backup = {
        "source": "web",
        "url": url,
        "domain": domain,
        "title": title,
        "description": description,
        "content_length": len(md),
        "content_preview": md[:500],
    }
    backup_path = OUTPUT_DIR / f"web_{page_id}.json"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)

    # Also save full markdown
    md_path = OUTPUT_DIR / f"web_{page_id}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\nSource: {url}\n\n---\n\n{md}")

    if vectorize:
        print(f"\n  Vectorizing...")
        full_text = f"Title: {title}\nSource: {url}\n\n{md}"
        stored = vectorize_and_store(full_text, {
            "source": "web",
            "url": url,
            "domain": domain,
            "title": (title or "")[:200],
        }, f"web_{page_id}")
        print(f"\n  DONE: {stored} vectors stored")

    print(f"  Local: {backup_path}")
    return backup


def cmd_search(query: str, limit: int = 5, vectorize: bool = True):
    """Search the web and optionally parse+vectorize results."""
    print(f"\n{'='*60}")
    print(f"Searching: {query}")
    print(f"{'='*60}")

    app = get_firecrawl()
    results = app.search(query)

    web_results = results.web or []
    print(f"\n  Found {len(web_results)} results\n")

    parsed = []
    for i, r in enumerate(web_results[:limit]):
        print(f"  [{i+1}] {r.title}")
        print(f"      {r.url}")
        print(f"      {(r.description or '')[:150]}")
        parsed.append({
            "url": r.url,
            "title": r.title,
            "description": r.description,
        })
        print()

    # Save search results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    search_id = url_to_id(query)
    results_path = OUTPUT_DIR / f"search_{search_id}.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump({"query": query, "results": parsed}, f, ensure_ascii=False, indent=2)

    if vectorize and web_results:
        print(f"\n  Scraping and vectorizing top {min(limit, len(web_results))} results...")
        total_vectors = 0
        for i, r in enumerate(web_results[:limit]):
            print(f"\n  --- Scraping [{i+1}/{min(limit, len(web_results))}]: {r.url} ---")
            try:
                page = app.scrape(r.url, formats=["markdown"], only_main_content=True)
                md = page.markdown or ""
                if len(md) > 100:
                    page_id = url_to_id(r.url)
                    domain = urlparse(r.url).netloc
                    full_text = f"Title: {r.title}\nSource: {r.url}\n\n{md}"
                    stored = vectorize_and_store(full_text, {
                        "source": "web_search",
                        "url": r.url,
                        "domain": domain,
                        "title": (r.title or "")[:200],
                        "search_query": query[:200],
                    }, f"ws_{page_id}")
                    total_vectors += stored
                else:
                    print(f"  Skipped (too short: {len(md)} chars)")
            except Exception as e:
                print(f"  Failed: {e}")

            # Small delay to respect rate limits
            if i < min(limit, len(web_results)) - 1:
                time.sleep(1)

        print(f"\n{'='*60}")
        print(f"DONE: {total_vectors} total vectors from {min(limit, len(web_results))} pages")
        print(f"{'='*60}")

    return parsed


def cmd_crawl(url: str, limit: int = 10, vectorize: bool = True):
    """Crawl a website up to N pages."""
    print(f"\n{'='*60}")
    print(f"Crawling: {url} (limit: {limit} pages)")
    print(f"{'='*60}")

    app = get_firecrawl()

    result = app.crawl(url, limit=limit)

    pages = result.data if hasattr(result, "data") else result
    if not pages:
        print("  No pages crawled")
        return []

    print(f"  Crawled {len(pages)} pages")

    total_vectors = 0
    for i, page in enumerate(pages):
        md = page.markdown if hasattr(page, "markdown") else page.get("markdown", "")
        meta = page.metadata if hasattr(page, "metadata") else page.get("metadata", {})

        page_url = ""
        title = ""
        if hasattr(meta, "url"):
            page_url = meta.url
        elif isinstance(meta, dict):
            page_url = meta.get("url", meta.get("sourceURL", ""))

        if hasattr(meta, "title"):
            title = meta.title
        elif isinstance(meta, dict):
            title = meta.get("title", "")

        if not md or len(md) < 100:
            continue

        print(f"\n  [{i+1}] {title or page_url}")
        print(f"      {len(md)} chars")

        if vectorize:
            page_id = url_to_id(page_url or f"{url}/page{i}")
            domain = urlparse(url).netloc
            full_text = f"Title: {title}\nSource: {page_url}\n\n{md}"
            stored = vectorize_and_store(full_text, {
                "source": "web_crawl",
                "url": page_url or url,
                "domain": domain,
                "title": (title or "")[:200],
            }, f"wc_{page_id}")
            total_vectors += stored

    print(f"\n{'='*60}")
    print(f"DONE: {total_vectors} vectors from {len(pages)} pages")
    print(f"{'='*60}")

    return pages


def cmd_map(url: str):
    """Get a map of all links on a site."""
    print(f"\n  Mapping: {url}")
    app = get_firecrawl()
    result = app.map(url)

    links = result.links if hasattr(result, "links") else result
    if isinstance(links, list):
        print(f"  Found {len(links)} links\n")
        for i, link in enumerate(links[:30]):
            print(f"  [{i+1}] {link}")
    else:
        print(f"  Result: {result}")

    return links


def cmd_find(query: str, top_k: int = 10, source_filter: str = None):
    """Semantic search across all parsed content in Pinecone."""
    print(f"\nSearching Pinecone: '{query}'")

    embedding = get_embedding(query)
    index = get_pinecone_index()

    filter_dict = {}
    if source_filter:
        filter_dict["source"] = {"$eq": source_filter}

    results = index.query(
        vector=embedding,
        top_k=top_k,
        include_metadata=True,
        filter=filter_dict if filter_dict else None,
    )

    print(f"\nFound {len(results.matches)} results:\n")

    for i, match in enumerate(results.matches):
        meta = match.metadata
        print(f"  [{i+1}] Score: {match.score:.3f}")
        print(f"      Source: {meta.get('source', '?')} | {meta.get('title', '?')}")
        if meta.get("url"):
            print(f"      URL: {meta['url']}")
        text_preview = meta.get("text", "")[:200]
        print(f"      Text: {text_preview}...")
        print()

    return results.matches


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Firecrawl Web Parser → Pinecone")
    subparsers = parser.add_subparsers(dest="command")

    # scrape
    p_scrape = subparsers.add_parser("scrape", help="Scrape a single page")
    p_scrape.add_argument("url", help="URL to scrape")
    p_scrape.add_argument("--no-vectorize", action="store_true", help="Skip Pinecone storage")

    # search
    p_search = subparsers.add_parser("search", help="Search web + scrape results")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", type=int, default=5, help="Max pages to scrape")
    p_search.add_argument("--no-vectorize", action="store_true", help="Skip Pinecone storage")

    # crawl
    p_crawl = subparsers.add_parser("crawl", help="Crawl a website")
    p_crawl.add_argument("url", help="Starting URL")
    p_crawl.add_argument("--limit", type=int, default=10, help="Max pages")
    p_crawl.add_argument("--no-vectorize", action="store_true", help="Skip Pinecone storage")

    # map
    p_map = subparsers.add_parser("map", help="Map site links")
    p_map.add_argument("url", help="URL to map")

    # find
    p_find = subparsers.add_parser("find", help="Search Pinecone")
    p_find.add_argument("query", help="Search query")
    p_find.add_argument("--top-k", type=int, default=10, help="Number of results")
    p_find.add_argument("--source", help="Filter: web, web_search, web_crawl, youtube")

    args = parser.parse_args()

    if args.command == "scrape":
        cmd_scrape(args.url, vectorize=not args.no_vectorize)
    elif args.command == "search":
        cmd_search(args.query, limit=args.limit, vectorize=not args.no_vectorize)
    elif args.command == "crawl":
        cmd_crawl(args.url, limit=args.limit, vectorize=not args.no_vectorize)
    elif args.command == "map":
        cmd_map(args.url)
    elif args.command == "find":
        cmd_find(args.query, top_k=args.top_k, source_filter=args.source)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
