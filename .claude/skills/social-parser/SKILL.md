---
name: social-parser
description: "Parse social media content (YouTube, Telegram, TikTok, Instagram, Twitter) into vectorized knowledge in Pinecone. Use this skill whenever the user wants to parse, scrape, transcribe, analyze, or search social media content. Also triggers when the user shares a YouTube URL, asks to transcribe a video, wants to do market research or competitor analysis on social content, or mentions Pinecone/vector search on parsed data. Even if the user just pastes a YouTube link without explanation, use this skill."
---

# Social Media Parser

Parse content from social media platforms, transcribe video/audio, vectorize via Gemini embeddings, and store in Pinecone for semantic search. This is the foundation of the NEURO COMMENTING market intelligence pipeline.

## Why this exists

The NEURO COMMENTING SaaS product helps brands grow through Telegram marketing. Understanding what competitors, influencers, and the market are saying across social platforms is core to the product's value. This skill turns unstructured social content into searchable vector knowledge.

## Architecture

```
URL/Channel → Fetch metadata → Get transcript/text → Chunk → Embed (Gemini) → Pinecone
                                                                                  ↓
                                                              Semantic search ← Query
```

## Available commands

The main script is `scripts/social_parser.py`. Activate the project venv before running.

### Parse YouTube video
```bash
cd "$PROJECT_DIR" && source venv/bin/activate
python scripts/social_parser.py youtube --url "URL" --subs-only
```

`--subs-only` skips Whisper audio download (faster, free). Omit it to fall back to Whisper transcription when no subtitles exist.

### Search parsed content
```bash
python scripts/social_parser.py search --query "your search" --top-k 5
```

Optional: `--source youtube` to filter by platform.

## How to handle user requests

### User shares a YouTube URL
1. Run the youtube parser with `--subs-only` first (fast path)
2. Show the user: title, channel, duration, transcript length, chunks stored
3. If no subtitles found, ask if they want Whisper transcription (slower, downloads audio)
4. After parsing, offer to search or analyze the content

### User asks to search parsed content
1. Run the search command with their query
2. Present top results with scores and text previews
3. Offer to dig deeper into specific results or parse more content

### User wants to analyze competitors / market
1. Ask which channels/videos/accounts to parse
2. Parse them in sequence (or parallel if multiple URLs)
3. Run search queries to surface insights
4. Summarize findings

### User asks to parse Telegram channels
Telegram parsing uses existing project session infrastructure. Read `channels/monitor.py` and `channels/discovery.py` for the current channel monitoring system. Telegram content can be vectorized using the same Pinecone pipeline — extract text from messages, chunk, embed, upsert.

## Infrastructure details

- **Pinecone index**: `social-content` (dimension 3072, cosine metric, AWS us-east-1)
- **Embedding model**: `gemini-embedding-001` (3072 dimensions)
- **Chunking**: 2000 chars max with 200 char overlap, breaking at sentence boundaries
- **Transcript sources** (priority order):
  1. `youtube-transcript-api` — fastest, gets auto-generated and manual subtitles
  2. `yt-dlp` subtitle extraction — fallback
  3. Whisper transcription — last resort, requires audio download
- **Local backups**: `output/parsed/yt_{video_id}.json`

## Environment variables

These must be set in `.env`:
- `PINECONE_API_KEY` — Pinecone vector DB access
- `GEMINI_API_KEY` — Gemini embeddings

## Python dependencies

Already installed in project venv:
- `pinecone`
- `yt-dlp`
- `youtube-transcript-api`
- `openai-whisper` (for audio transcription fallback)
- `google-generativeai` (for embeddings)

## Extending to other platforms

The pipeline pattern is the same for any platform:
1. Fetch content (API or scraper)
2. Extract text
3. Chunk with `chunk_text()`
4. Get embeddings with `get_embedding()`
5. Upsert to Pinecone with source-specific metadata

When adding a new platform, add a new function in `scripts/social_parser.py` following the `parse_youtube` pattern, and register it as a CLI subcommand.

## Known limitations

- YouTube audio download may fail with 403 (SABR protection) — this is why subtitle extraction is the primary path
- Pinecone free tier has limits on vector count and index count
- Gemini embedding API has rate limits — batch large jobs with small delays
- Whisper `base` model works for Russian but `medium` or `large` gives better accuracy
