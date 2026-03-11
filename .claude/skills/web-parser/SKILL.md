---
name: web-parser
description: "Parse any website, article, or web page using Firecrawl into vectorized knowledge in Pinecone. Use this skill whenever the user wants to scrape a website, crawl a site, parse an article, do web research, analyze competitors, collect market intelligence, or find information across the internet. Triggers when the user shares a URL (not YouTube — use social-parser for that), asks to 'scrape', 'crawl', 'parse a site', 'research competitors', 'find articles about X', or wants to build a knowledge base from web content. Also use when the user asks to search their parsed knowledge base across all sources."
---

# Web Parser (Firecrawl → Pinecone)

Scrape, crawl, and search the web using Firecrawl API. Vectorize content via Gemini embeddings and store in Pinecone for cross-source semantic search alongside YouTube transcripts, Telegram content, and anything else in the knowledge base.

## Why this exists

NEURO COMMENTING needs market intelligence — what competitors say, how influencers position, what channels publish about. This skill turns the entire web into a searchable knowledge base that powers marketing decisions.

## Available commands

The script is `scripts/web_parser.py`. Activate the project venv before running.

### Scrape a single page
```bash
source venv/bin/activate
python scripts/web_parser.py scrape "https://example.com/article"
```
Fetches the page, extracts clean markdown, vectorizes, stores in Pinecone. Saves local backup to `output/parsed/`.

### Search the web + parse results
```bash
python scripts/web_parser.py search "нейрокомментинг телеграм маркетинг" --limit 5
```
Uses Firecrawl search to find relevant pages, then scrapes and vectorizes the top N results. This is the most powerful command for building knowledge bases on a topic.

### Crawl an entire site
```bash
python scripts/web_parser.py crawl "https://vc.ru/marketing" --limit 10
```
Follows links from the starting URL, scrapes up to N pages, vectorizes all of them.

### Map site links
```bash
python scripts/web_parser.py map "https://example.com"
```
Returns all discoverable links on a site. Useful for planning what to crawl.

### Search your knowledge base
```bash
python scripts/web_parser.py find "ваш запрос" --top-k 10
```
Semantic search across ALL parsed content — web, YouTube, Telegram, everything stored in Pinecone.

Optional: `--source web` or `--source youtube` to filter by platform.

## How to handle user requests

### User shares a URL (not YouTube)
1. Run `scrape` on the URL
2. Report: title, content length, vectors stored
3. Offer to search or analyze the content

### User asks to research a topic
1. Run `search` with their topic as query
2. Show the found results (titles, URLs, descriptions)
3. Confirm which ones to scrape and vectorize
4. After vectorization, run `find` to show what's now searchable

### User wants competitor analysis
1. Identify competitor URLs/topics
2. Run `search` for each competitor or topic
3. Run `crawl` on competitor websites
4. Use `find` to cross-reference insights

### User wants to parse Telegram channels
Telegram channels can be partially accessed via their web previews (`t.me/s/channelname`), but Telegram blocks most scraping. For deep Telegram parsing, use:
- The project's built-in Telegram session infrastructure (`channels/monitor.py`)
- Third-party aggregators: tgstat.ru, telemetr.io (scrape these with Firecrawl)
- The `social-parser` skill for YouTube content from the same creators

Recommended approach for Telegram channel intelligence:
1. Find the channel's web presence (website, vc.ru posts, YouTube videos)
2. Scrape those with this skill
3. Use Telegram sessions for actual channel message parsing
4. Vectorize everything into the same Pinecone index

## Firecrawl capabilities and limits

- **scrape**: works on most sites. Some block it (X/Twitter, some social media)
- **search**: web search + returns structured results with titles/descriptions/URLs
- **crawl**: follows links, good for documentation sites and blogs
- **map**: fast link discovery without scraping content
- **Credits**: current plan has limited credits. `search` costs 1 credit per query. `scrape` costs 1 credit per page. Monitor with `app.get_credit_usage()`

## Sites that work well
- vc.ru, habr.com, forbes.ru — Russian tech/business media
- Medium, dev.to, docs sites — English content
- Company blogs, landing pages, documentation
- tgstat.ru — Telegram channel analytics (partial)

## Sites that DON'T work
- x.com / twitter.com — blocked by Firecrawl
- Direct t.me pages — Telegram blocks scraping
- Sites behind heavy Cloudflare protection

## Environment variables (in .env)
- `FIRECRAWL_API_KEY` — Firecrawl API access
- `PINECONE_API_KEY` — Pinecone vector DB
- `GEMINI_API_KEY` — Gemini embeddings

## Data flow
```
Firecrawl scrape → Markdown → Chunk (2000 chars) → Gemini embedding → Pinecone upsert
                                                                           ↕
                                                     Semantic search ← User query
```

All sources (web, YouTube, Telegram) share the same Pinecone index (`social-content`), so a single `find` query searches across everything.
