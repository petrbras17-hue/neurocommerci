#!/usr/bin/env python3
"""
transcribe_media.py — CLI wrapper for MediaPipeline.

Usage examples:

  # Transcribe a YouTube video
  python scripts/transcribe_media.py --url "https://youtu.be/xyz"

  # Transcribe a local file
  python scripts/transcribe_media.py --file /tmp/voice_message.ogg

  # Batch mode from a file with one URL per line
  python scripts/transcribe_media.py --batch /tmp/urls.txt --output /tmp/transcripts.txt

  # Use a better model
  python scripts/transcribe_media.py --url "https://youtu.be/xyz" --model medium

  # Run AI analysis after transcription
  python scripts/transcribe_media.py --url "https://youtu.be/xyz" --analyze
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# Allow running from repo root without installing the package
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from core.media_pipeline import MediaPipeline, MediaResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AI analysis (optional, uses existing ai_router infrastructure)
# ---------------------------------------------------------------------------

async def _analyze_transcript(transcript: str) -> str:
    """
    Send transcript to the platform AI router for a brief summary/analysis.

    This is optional and skipped gracefully if the router is unavailable
    (e.g. no API keys configured in dev environments).
    """
    try:
        from core.ai_router import route_ai_task  # type: ignore
        result = await route_ai_task(
            task_type="assistant_reply",
            prompt=(
                "Проанализируй следующую транскрипцию видео. "
                "Выдели ключевые тезисы, тему и тональность.\n\n"
                f"ТРАНСКРИПЦИЯ:\n{transcript[:4000]}"
            ),
            tenant_id=None,
        )
        return str(result)
    except Exception as exc:
        return f"[AI analysis unavailable: {exc}]"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write_output(text: str, output_path: Optional[str]) -> None:
    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        log.info("Transcript saved to: %s", output_path)
    else:
        print("\n" + "=" * 60)
        print(text)
        print("=" * 60 + "\n")


def _results_to_text(results: list) -> str:
    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(f"--- [{i}] {r.source_url} ---")
        if r.duration_seconds:
            lines.append(f"Duration: {r.duration_seconds:.1f}s")
        lines.append(r.transcript)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

# Fix typing import for older Python
from typing import Optional


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="CLI media transcription tool (yt-dlp + ffmpeg + whisper)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--url",
        metavar="URL",
        help="YouTube / Telegram / Instagram URL to download and transcribe",
    )
    source_group.add_argument(
        "--file",
        metavar="PATH",
        help="Local audio or video file to transcribe",
    )
    source_group.add_argument(
        "--batch",
        metavar="FILE",
        help="Text file with one URL per line for batch processing",
    )

    parser.add_argument(
        "--language",
        default="ru",
        metavar="LANG",
        help="Language code for Whisper (default: ru). Examples: en, uk, kk",
    )
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: base). larger = slower but more accurate",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write transcript to this file instead of stdout",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run AI analysis on the transcript after transcription",
    )
    parser.add_argument(
        "--check-deps",
        action="store_true",
        help="Check that yt-dlp, ffmpeg, and whisper are installed and exit",
    )
    parser.add_argument(
        "--tmp-dir",
        default="/tmp/neuro_media",
        help="Temporary directory for intermediate files (default: /tmp/neuro_media)",
    )

    args = parser.parse_args()

    pipeline = MediaPipeline(tmp_dir=args.tmp_dir)

    # ------------------------------------------------------------------
    # --check-deps mode
    # ------------------------------------------------------------------
    if args.check_deps:
        deps = await pipeline.check_dependencies()
        print("\nDependency check:")
        for tool, version in deps.items():
            status = version if version else "NOT FOUND"
            mark = "OK" if version else "MISSING"
            print(f"  [{mark}] {tool}: {status}")
        missing = [t for t, v in deps.items() if not v]
        if missing:
            print(f"\nMissing tools: {', '.join(missing)}")
            return 1
        print("\nAll dependencies satisfied.")
        return 0

    # ------------------------------------------------------------------
    # Require at least one source
    # ------------------------------------------------------------------
    if not (args.url or args.file or args.batch):
        parser.print_help()
        return 1

    # ------------------------------------------------------------------
    # Single URL mode
    # ------------------------------------------------------------------
    if args.url:
        log.info("Starting pipeline for URL: %s", args.url)
        result: MediaResult = await pipeline.full_pipeline(
            args.url,
            language=args.language,
            model=args.model,
        )
        output_text = result.transcript
        if result.duration_seconds:
            log.info("Video duration: %.1fs", result.duration_seconds)

        if args.analyze:
            log.info("Running AI analysis...")
            analysis = await _analyze_transcript(result.transcript)
            output_text = f"=== ТРАНСКРИПЦИЯ ===\n{result.transcript}\n\n=== AI АНАЛИЗ ===\n{analysis}"

        _write_output(output_text, args.output)
        return 0

    # ------------------------------------------------------------------
    # Local file mode
    # ------------------------------------------------------------------
    if args.file:
        if not os.path.exists(args.file):
            log.error("File not found: %s", args.file)
            return 1

        log.info("Transcribing local file: %s", args.file)
        transcript = await pipeline.transcribe_local_file(
            args.file,
            language=args.language,
            model=args.model,
        )

        output_text = transcript
        if args.analyze:
            log.info("Running AI analysis...")
            analysis = await _analyze_transcript(transcript)
            output_text = f"=== ТРАНСКРИПЦИЯ ===\n{transcript}\n\n=== AI АНАЛИЗ ===\n{analysis}"

        _write_output(output_text, args.output)
        return 0

    # ------------------------------------------------------------------
    # Batch mode
    # ------------------------------------------------------------------
    if args.batch:
        if not os.path.exists(args.batch):
            log.error("Batch file not found: %s", args.batch)
            return 1

        with open(args.batch, encoding="utf-8") as fh:
            urls = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

        if not urls:
            log.error("No URLs found in batch file: %s", args.batch)
            return 1

        log.info("Batch mode: %d URLs to process", len(urls))
        results = await pipeline.batch_transcribe(
            urls,
            language=args.language,
            model=args.model,
        )

        output_text = _results_to_text(results)
        _write_output(output_text, args.output)

        errors = sum(1 for r in results if r.transcript.startswith("[ERROR:"))
        if errors:
            log.warning("%d URLs failed during batch processing.", errors)
            return 2  # partial failure

        return 0

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
