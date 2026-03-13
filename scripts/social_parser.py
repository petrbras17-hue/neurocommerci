"""
Social Media Parser → Pinecone Vector Pipeline

Парсит контент из соцсетей, транскрибирует видео,
векторизует через Gemini embeddings и сохраняет в Pinecone.

Использование:
    # Парсинг YouTube видео по URL
    python scripts/social_parser.py youtube --url "https://youtube.com/watch?v=..."

    # Парсинг YouTube видео (только субтитры, без скачивания)
    python scripts/social_parser.py youtube --url "..." --subs-only

    # Поиск по сохранённому контенту
    python scripts/social_parser.py search --query "маркетинг в телеграм"
"""

import argparse
import hashlib
import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path

# Add project root to path
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


# ---------------------------------------------------------------------------
# Pinecone helpers
# ---------------------------------------------------------------------------

def get_pinecone_index():
    from pinecone import Pinecone
    pc = Pinecone(api_key=get_env("PINECONE_API_KEY"))
    return pc.Index("social-content")


# ---------------------------------------------------------------------------
# Gemini embeddings
# ---------------------------------------------------------------------------

def get_embedding(text: str) -> list[float]:
    """Get embedding vector from Gemini."""
    import google.generativeai as genai
    genai.configure(api_key=get_env("GEMINI_API_KEY"))
    result = genai.embed_content(
        model="models/gemini-embedding-001",
        content=text,
    )
    return result["embedding"]


def chunk_text(text: str, max_chars: int = 2000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks for embedding."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        # Try to break at sentence boundary
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


# ---------------------------------------------------------------------------
# YouTube parser
# ---------------------------------------------------------------------------

def parse_youtube(url: str, subs_only: bool = False, whisper_model: str = "base", whisper_lang: str = None) -> dict:
    """
    Parse a YouTube video: download metadata, subtitles/audio, transcribe, vectorize.

    Returns dict with video info and chunks stored in Pinecone.
    """
    import yt_dlp

    print(f"\n{'='*60}")
    print(f"Parsing YouTube: {url}")
    print(f"{'='*60}")

    output_dir = Path(__file__).resolve().parent.parent / "output" / "parsed"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Get video info + subtitles
    print("\n[1/4] Fetching video metadata...")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["ru", "en"],
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_id = info.get("id", "unknown")
    title = info.get("title", "Untitled")
    channel = info.get("channel", info.get("uploader", "Unknown"))
    duration = info.get("duration", 0)
    description = info.get("description", "")
    view_count = info.get("view_count", 0)
    upload_date = info.get("upload_date", "")
    tags = info.get("tags", []) or []
    categories = info.get("categories", []) or []

    print(f"  Title: {title}")
    print(f"  Channel: {channel}")
    print(f"  Duration: {duration // 60}m {duration % 60}s")
    print(f"  Views: {view_count:,}")

    # Step 2: Get transcript
    transcript = ""

    # Try youtube-transcript-api first (most reliable for subtitles)
    print("\n[2/4] Getting transcript...")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt = YouTubeTranscriptApi()
        # Try Russian first, then English
        for lang in ["ru", "en"]:
            try:
                result = ytt.fetch(video_id, languages=[lang])
                transcript = " ".join(item.text for item in result)
                sub_lang = f"{lang} ({'auto' if result.is_generated else 'manual'})"
                print(f"  Got transcript ({sub_lang}): {len(transcript)} chars")
                break
            except Exception:
                continue
    except ImportError:
        pass

    # Fallback: try yt-dlp subtitles
    if not transcript:
        subs = info.get("subtitles", {})
        auto_subs = info.get("automatic_captions", {})

        sub_data = None
        sub_lang = None
        for lang in ["ru", "en"]:
            if lang in subs:
                sub_data = subs[lang]
                sub_lang = lang
                break
            if lang in auto_subs:
                sub_data = auto_subs[lang]
                sub_lang = f"{lang} (auto)"
                break

        if sub_data:
            sub_url = None
            for fmt in sub_data:
                if fmt.get("ext") in ("json3", "vtt", "srv1"):
                    sub_url = fmt.get("url")
                    break
            if not sub_url and sub_data:
                sub_url = sub_data[0].get("url")

            if sub_url:
                import urllib.request
                try:
                    with urllib.request.urlopen(sub_url) as resp:
                        raw = resp.read().decode("utf-8", errors="replace")
                    lines = []
                    for line in raw.splitlines():
                        line = line.strip()
                        if not line or "-->" in line or line.startswith("WEBVTT") or line.isdigit():
                            continue
                        clean = re.sub(r"<[^>]+>", "", line)
                        if clean:
                            lines.append(clean)
                    transcript = " ".join(lines)
                    print(f"  Got subtitles via yt-dlp ({sub_lang}): {len(transcript)} chars")
                except Exception as e:
                    print(f"  Subtitle download failed: {e}")

    # If no subs and not subs_only, download audio and transcribe with Whisper
    if not transcript and not subs_only:
        print("  No subtitles found. Downloading audio for Whisper transcription...")
        audio_path = output_dir / f"{video_id}.mp4"

        # Try pytubefix first (bypasses SABR 403), then yt-dlp fallback
        downloaded = False
        try:
            from pytubefix import YouTube as PTYouTube
            yt = PTYouTube(url)
            stream = yt.streams.filter(only_audio=True).first()
            if stream:
                stream.download(output_path=str(output_dir), filename=f"{video_id}.mp4")
                downloaded = audio_path.exists()
                if downloaded:
                    print(f"  Audio downloaded via pytubefix: {audio_path.stat().st_size // 1024}KB")
        except Exception as e:
            print(f"  pytubefix failed: {e}")

        if not downloaded:
            ydl_audio_opts = {
                "quiet": True,
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "64",
                }],
                "outtmpl": str(output_dir / f"{video_id}.%(ext)s"),
            }
            try:
                with yt_dlp.YoutubeDL(ydl_audio_opts) as ydl:
                    ydl.download([url])
                audio_path = output_dir / f"{video_id}.mp3"
                downloaded = audio_path.exists()
            except Exception as e:
                print(f"  yt-dlp audio download failed: {e}")

        if downloaded and audio_path.exists():
            print(f"  Transcribing with Whisper ({whisper_model})...")
            try:
                import whisper
                model = whisper.load_model(whisper_model)
                result = model.transcribe(str(audio_path), language=whisper_lang)
                transcript = result["text"]

                # Save segments with timestamps
                segments_data = [
                    {"start": s["start"], "end": s["end"], "text": s["text"]}
                    for s in result.get("segments", [])
                ]
                whisper_backup = output_dir / f"yt_{video_id}_whisper.json"
                with open(whisper_backup, "w", encoding="utf-8") as wf:
                    json.dump({"text": transcript, "segments": segments_data}, wf, ensure_ascii=False, indent=2)

                print(f"  Transcribed: {len(transcript)} chars ({len(segments_data)} segments)")
                print(f"  Whisper backup: {whisper_backup}")
            except Exception as e:
                print(f"  Whisper transcription failed: {e}")
            finally:
                audio_path.unlink(missing_ok=True)
        elif not downloaded:
            print(f"  Audio download failed — no transcript available.")

    if not transcript:
        if subs_only:
            print("  No subtitles available. Use without --subs-only for Whisper transcription.")
        else:
            print("  WARNING: No transcript obtained.")
        transcript = description  # Fallback to description

    # Step 3: Vectorize and store in Pinecone
    print(f"\n[3/4] Vectorizing content...")

    # Combine metadata for rich context
    full_text = f"Title: {title}\nChannel: {channel}\n"
    if tags:
        full_text += f"Tags: {', '.join(tags[:10])}\n"
    full_text += f"\n{transcript}"

    chunks = chunk_text(full_text)
    print(f"  Split into {len(chunks)} chunks")

    print(f"\n[4/4] Storing in Pinecone...")
    index = get_pinecone_index()

    vectors = []
    for i, chunk in enumerate(chunks):
        chunk_id = f"yt_{video_id}_chunk_{i}"
        try:
            embedding = get_embedding(chunk)
            vectors.append({
                "id": chunk_id,
                "values": embedding,
                "metadata": {
                    "source": "youtube",
                    "video_id": video_id,
                    "title": title[:200],
                    "channel": channel[:100],
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                    "text": chunk[:1000],  # Pinecone metadata limit
                    "duration_sec": duration,
                    "view_count": view_count,
                    "upload_date": upload_date,
                    "url": url,
                },
            })
        except Exception as e:
            print(f"  Embedding failed for chunk {i}: {e}")

    if vectors:
        # Upsert in batches of 100
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i : i + batch_size]
            index.upsert(vectors=batch)
        print(f"  Stored {len(vectors)} vectors in Pinecone")

    # Save local JSON backup
    result = {
        "source": "youtube",
        "video_id": video_id,
        "url": url,
        "title": title,
        "channel": channel,
        "duration_sec": duration,
        "view_count": view_count,
        "upload_date": upload_date,
        "tags": tags[:20],
        "categories": categories,
        "transcript_length": len(transcript),
        "transcript_text": transcript,
        "word_count": len(transcript.split()),
        "chunks_stored": len(vectors),
        "description": description[:500],
    }

    backup_path = output_dir / f"yt_{video_id}.json"
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n  Local backup: {backup_path}")

    print(f"\n{'='*60}")
    print(f"DONE: {title}")
    print(f"  {len(vectors)} vectors stored in Pinecone 'social-content'")
    print(f"{'='*60}\n")

    return result


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

def search_content(query: str, top_k: int = 10, source_filter: str = None) -> list[dict]:
    """Search parsed content in Pinecone."""
    print(f"\nSearching: '{query}'")

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
        score = match.score
        print(f"  [{i+1}] Score: {score:.3f}")
        print(f"      Source: {meta.get('source', '?')} | {meta.get('title', '?')}")
        print(f"      Channel: {meta.get('channel', '?')}")
        text_preview = meta.get("text", "")[:150]
        print(f"      Text: {text_preview}...")
        print()

    return [
        {
            "score": m.score,
            "metadata": dict(m.metadata),
        }
        for m in results.matches
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Social Media → Pinecone Parser")
    subparsers = parser.add_subparsers(dest="command")

    # youtube
    yt_parser = subparsers.add_parser("youtube", help="Parse YouTube video")
    yt_parser.add_argument("--url", required=True, help="YouTube video URL")
    yt_parser.add_argument("--subs-only", action="store_true", help="Only use subtitles, skip Whisper")
    yt_parser.add_argument("--whisper-model", default="base", help="Whisper model size (tiny/base/small/medium)")
    yt_parser.add_argument("--lang", default=None, help="Force Whisper language (ru/en/de/etc). Auto-detect if omitted")

    # search
    search_parser = subparsers.add_parser("search", help="Search parsed content")
    search_parser.add_argument("--query", required=True, help="Search query")
    search_parser.add_argument("--top-k", type=int, default=10, help="Number of results")
    search_parser.add_argument("--source", help="Filter by source (youtube/telegram/etc)")

    args = parser.parse_args()

    if args.command == "youtube":
        parse_youtube(args.url, subs_only=args.subs_only, whisper_model=args.whisper_model, whisper_lang=args.lang)
    elif args.command == "search":
        search_content(args.query, top_k=args.top_k, source_filter=args.source)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
