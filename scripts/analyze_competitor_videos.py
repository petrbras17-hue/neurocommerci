#!/usr/bin/env python3
"""
Analyze competitor YouTube videos using Supadata API + AI.

Transcribes competitor videos, extracts structured competitive intelligence,
and saves results to a JSON file.

Primary target: GramGPT.io and similar Telegram SaaS tools.

Usage:
    # Одно видео
    python scripts/analyze_competitor_videos.py --url "https://youtu.be/VIDEO_ID"

    # Одно видео — только транскрипт (без AI анализа)
    python scripts/analyze_competitor_videos.py --url "https://youtu.be/VIDEO_ID" --dry-run

    # Все видео канала
    python scripts/analyze_competitor_videos.py --channel "https://www.youtube.com/@gramgpt"

    # С указанием конкурента и выходного файла
    python scripts/analyze_competitor_videos.py \\
        --channel "https://www.youtube.com/@gramgpt" \\
        --competitor "GramGPT" \\
        --output data/gramgpt_intel.json

Env vars:
    SUPADATA_API_KEY  — ключ Supadata API (обязателен)
    GEMINI_API_KEY    — ключ Gemini (для standalone AI анализа без DB)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Загрузка .env должна быть ДО импорта config/settings
from dotenv import load_dotenv
load_dotenv()

from core.youtube_intel import (
    YouTubeIntelService,
    CompetitorInsight,
    YouTubeIntelError,
    NoCaptionsError,
    VideoNotFoundError,
    SupadataAuthError,
    SupadataRateLimitError,
)
from utils.logger import log


# ---------------------------------------------------------------------------
# Форматирование вывода в консоль
# ---------------------------------------------------------------------------

def _print_insight(insight: CompetitorInsight) -> None:
    """Вывести инсайт в читаемом виде в консоль."""
    sep = "-" * 60
    print(f"\n{sep}")
    print(f"Видео: {insight.video_url}")
    print(f"Название: {insight.video_title}")
    print(f"Конкурент: {insight.competitor_name}")
    print(f"Угроза: {insight.threat_level.upper()}")
    print(f"Длина транскрипта: {insight.transcript_length} симв.")
    print(f"Проанализировано: {insight.analyzed_at.strftime('%Y-%m-%d %H:%M UTC')}")

    if insight.key_points:
        print("\nОсновные тезисы:")
        for pt in insight.key_points:
            print(f"  - {pt}")

    if insight.features_mentioned:
        print("\nФункции/фичи:")
        for f in insight.features_mentioned:
            print(f"  * {f}")

    if insight.pricing_info:
        print(f"\nЦены: {insight.pricing_info}")

    if insight.weaknesses:
        print("\nСлабые стороны:")
        for w in insight.weaknesses:
            print(f"  ! {w}")

    if insight.marketing_tactics:
        print("\nМаркетинговые приёмы:")
        for m in insight.marketing_tactics:
            print(f"  ~ {m}")

    if insight.seo_keywords:
        print(f"\nSEO-ключевые слова: {', '.join(insight.seo_keywords)}")

    print(sep)


def _print_transcript(url: str, transcript: str) -> None:
    """Вывести транскрипт в консоль (dry-run режим)."""
    print(f"\n{'=' * 60}")
    print(f"ТРАНСКРИПТ: {url}")
    print(f"Длина: {len(transcript)} символов")
    print(f"{'=' * 60}")
    # Показываем первые 2000 символов
    preview = transcript[:2000]
    if len(transcript) > 2000:
        preview += f"\n\n... [ещё {len(transcript) - 2000} символов] ..."
    print(preview)
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Сохранение результатов
# ---------------------------------------------------------------------------

def _save_results(
    insights: list[CompetitorInsight],
    output_path: str,
) -> None:
    """Сохранить результаты в JSON файл."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(insights),
        "insights": [i.to_dict() for i in insights],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nРезультаты сохранены: {path} ({len(insights)} инсайтов)")


# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------

async def run_single_url(
    service: YouTubeIntelService,
    url: str,
    competitor: str,
    dry_run: bool,
) -> CompetitorInsight | None:
    """Обработать одно видео."""
    if dry_run:
        print(f"\nDRY-RUN: получаем транскрипт для {url}")
        try:
            transcript = await service.get_transcript(url)
            _print_transcript(url, transcript)
            return None
        except (YouTubeIntelError, NoCaptionsError, VideoNotFoundError) as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return None
    else:
        try:
            insight = await service.process_video(url, competitor)
            _print_insight(insight)
            return insight
        except SupadataAuthError as exc:
            print(f"ОШИБКА АВТОРИЗАЦИИ: {exc}", file=sys.stderr)
            sys.exit(1)
        except (NoCaptionsError, VideoNotFoundError) as exc:
            print(f"ПРОПУСК: {exc}", file=sys.stderr)
            return None
        except YouTubeIntelError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return None


async def run_channel(
    service: YouTubeIntelService,
    channel_url: str,
    competitor: str,
    dry_run: bool,
    output: str | None,
) -> None:
    """Обработать все видео канала."""
    print(f"\nПолучаем список видео канала: {channel_url}")
    try:
        video_urls = await service.get_channel_videos(channel_url)
    except YouTubeIntelError as exc:
        print(f"ОШИБКА получения видео канала: {exc}", file=sys.stderr)
        sys.exit(1)

    if not video_urls:
        print("Видео не найдены для данного канала.", file=sys.stderr)
        sys.exit(1)

    print(f"Найдено {len(video_urls)} видео. Начинаем обработку...")

    if dry_run:
        for url in video_urls:
            try:
                transcript = await service.get_transcript(url)
                _print_transcript(url, transcript)
            except (YouTubeIntelError, NoCaptionsError, VideoNotFoundError) as exc:
                print(f"ПРОПУСК [{url}]: {exc}", file=sys.stderr)
        return

    insights = await service.batch_process(video_urls, competitor)

    for insight in insights:
        _print_insight(insight)

    if output and insights:
        _save_results(insights, output)
    elif insights:
        # Сохраняем в data/ по умолчанию
        safe_name = competitor.lower().replace(" ", "_")
        default_path = f"data/{safe_name}_intel_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        _save_results(insights, default_path)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Анализ YouTube видео конкурентов через Supadata API + AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--url",
        metavar="URL",
        help="URL одного видео YouTube для анализа",
    )
    group.add_argument(
        "--channel",
        metavar="CHANNEL_URL",
        help="URL канала YouTube — обработать все видео",
    )

    parser.add_argument(
        "--competitor",
        metavar="NAME",
        default="GramGPT",
        help='Название конкурента для контекста AI (по умолчанию: "GramGPT")',
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        default=None,
        help="Путь к JSON файлу для сохранения результатов",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Только получить транскрипт, без AI анализа",
    )
    parser.add_argument(
        "--api-key",
        metavar="KEY",
        default=None,
        help="Supadata API ключ (переопределяет SUPADATA_API_KEY из env)",
    )

    args = parser.parse_args()

    service = YouTubeIntelService(api_key=args.api_key)

    if not service._api_key:
        print(
            "ОШИБКА: SUPADATA_API_KEY не задан.\n"
            "Добавьте в .env: SUPADATA_API_KEY=ваш_ключ\n"
            "Или передайте --api-key KEY",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.url:
        insight = await run_single_url(
            service=service,
            url=args.url,
            competitor=args.competitor,
            dry_run=args.dry_run,
        )
        if insight and args.output:
            _save_results([insight], args.output)
        elif insight and not args.output:
            safe_name = args.competitor.lower().replace(" ", "_")
            default_path = (
                f"data/{safe_name}_intel_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            )
            _save_results([insight], default_path)

    elif args.channel:
        await run_channel(
            service=service,
            channel_url=args.channel,
            competitor=args.competitor,
            dry_run=args.dry_run,
            output=args.output,
        )


if __name__ == "__main__":
    asyncio.run(main())
