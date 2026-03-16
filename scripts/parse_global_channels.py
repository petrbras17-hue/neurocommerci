"""
CLI script: глобальный парсер Telegram-каналов для Channel Map globe.

Источники данных (по приоритету):
    1. TGStat API (tgstat.ru/api) — основной, требует TGSTAT_API_TOKEN
    2. Telemetr.io scraping — резервный
    3. Публичные каталоги Telegram-каналов
    4. Прямой поиск через Telethon — последний вариант

Использование:
    # TGStat API (основной источник)
    python scripts/parse_global_channels.py --source tgstat --language RU,EN --category tech,crypto --limit 5000

    # Telemetr.io scraping
    python scripts/parse_global_channels.py --source telemetr --language RU,EN,ES --limit 3000

    # Telethon поиск (требует подключённую сессию)
    python scripts/parse_global_channels.py --source telethon --language RU,EN --category tech --limit 1000

    # Dry-run: показать без записи в БД
    python scripts/parse_global_channels.py --source tgstat --language RU --dry-run

    # Все языки, все категории
    python scripts/parse_global_channels.py --source tgstat --limit 10000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Ensure project root is on the path when run directly.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import ChannelMapEntry
from storage.sqlite_db import init_db, async_session
from utils.helpers import utcnow
from utils.logger import log


# ---------------------------------------------------------------------------
# Координаты стран для размещения каналов на глобусе react-globe.gl
# Формат: country_code -> (lat, lng)
# ---------------------------------------------------------------------------

COUNTRY_COORDS: Dict[str, tuple] = {
    # СНГ
    "RU": (55.75, 37.62),     # Москва
    "UA": (50.45, 30.52),     # Киев
    "BY": (53.90, 27.57),     # Минск
    "KZ": (51.17, 71.43),     # Астана
    "UZ": (41.30, 69.28),     # Ташкент
    "KG": (42.87, 74.59),     # Бишкек
    "TJ": (38.56, 68.77),     # Душанбе
    "AZ": (40.41, 49.87),     # Баку
    "GE": (41.72, 44.79),     # Тбилиси
    "AM": (40.18, 44.51),     # Ереван
    "MD": (47.01, 28.86),     # Кишинёв
    # Европа
    "GB": (51.51, -0.13),     # Лондон
    "DE": (52.52, 13.41),     # Берлин
    "FR": (48.86, 2.35),      # Париж
    "ES": (40.42, -3.70),     # Мадрид
    "IT": (41.90, 12.50),     # Рим
    "PT": (38.72, -9.14),     # Лиссабон
    "NL": (52.37, 4.90),      # Амстердам
    "PL": (52.23, 21.01),     # Варшава
    "SE": (59.33, 18.07),     # Стокгольм
    "NO": (59.91, 10.75),     # Осло
    "FI": (60.17, 24.94),     # Хельсинки
    "DK": (55.68, 12.57),     # Копенгаген
    "CH": (46.95, 7.45),      # Берн
    "AT": (48.21, 16.37),     # Вена
    "BE": (50.85, 4.35),      # Брюссель
    "CZ": (50.08, 14.44),     # Прага
    "RO": (44.43, 26.10),     # Бухарест
    "GR": (37.98, 23.73),     # Афины
    "HU": (47.50, 19.04),     # Будапешт
    "BG": (42.70, 23.32),     # София
    "RS": (44.79, 20.47),     # Белград
    "HR": (45.81, 15.98),     # Загреб
    "IE": (53.35, -6.26),     # Дублин
    # Америка
    "US": (40.71, -74.01),    # Нью-Йорк
    "BR": (-23.55, -46.63),   # Сан-Паулу
    "MX": (19.43, -99.13),    # Мехико
    "AR": (-34.60, -58.38),   # Буэнос-Айрес
    "CO": (4.71, -74.07),     # Богота
    "CL": (-33.45, -70.67),   # Сантьяго
    "PE": (-12.05, -77.04),   # Лима
    "VE": (10.48, -66.88),    # Каракас
    "CA": (43.65, -79.38),    # Торонто
    # Азия
    "CN": (39.90, 116.40),    # Пекин
    "JP": (35.68, 139.69),    # Токио
    "KR": (37.57, 126.98),    # Сеул
    "IN": (28.61, 77.21),     # Дели
    "ID": (-6.21, 106.85),    # Джакарта
    "TH": (13.76, 100.50),    # Бангкок
    "VN": (21.03, 105.85),    # Ханой
    "PH": (14.60, 120.98),    # Манила
    "MY": (3.14, 101.69),     # Куала-Лумпур
    "SG": (1.35, 103.82),     # Сингапур
    "PK": (33.69, 73.04),     # Исламабад
    "BD": (23.81, 90.41),     # Дакка
    "TW": (25.03, 121.57),    # Тайпей
    "HK": (22.32, 114.17),    # Гонконг
    # Ближний Восток
    "TR": (41.01, 28.98),     # Стамбул
    "AE": (25.20, 55.27),     # Дубай
    "SA": (24.69, 46.72),     # Эр-Рияд
    "IR": (35.69, 51.39),     # Тегеран
    "IQ": (33.31, 44.37),     # Багдад
    "IL": (32.07, 34.78),     # Тель-Авив
    "EG": (30.04, 31.24),     # Каир
    "QA": (25.29, 51.53),     # Доха
    "KW": (29.38, 47.99),     # Кувейт
    "JO": (31.95, 35.93),     # Амман
    "LB": (33.89, 35.50),     # Бейрут
    # Африка
    "NG": (6.52, 3.38),       # Лагос
    "ZA": (-33.93, 18.42),    # Кейптаун
    "KE": (-1.29, 36.82),     # Найроби
    "ET": (9.02, 38.75),      # Аддис-Абеба
    "MA": (33.97, -6.85),     # Рабат
    "DZ": (36.74, 3.09),      # Алжир
    "TN": (36.81, 10.18),     # Тунис
    "GH": (5.56, -0.20),      # Аккра
    # Океания
    "AU": (-33.87, 151.21),   # Сидней
    "NZ": (-36.85, 174.76),   # Окленд
}

# Маппинг язык -> страна по умолчанию (для определения координат)
LANGUAGE_TO_COUNTRY: Dict[str, str] = {
    "ru": "RU",
    "en": "US",
    "es": "ES",
    "pt": "BR",
    "ar": "SA",
    "de": "DE",
    "fr": "FR",
    "zh": "CN",
    "hi": "IN",
    "tr": "TR",
    "id": "ID",
    "ko": "KR",
    "ja": "JP",
    "uk": "UA",
    "kz": "KZ",
    "uz": "UZ",
    "by": "BY",
    "it": "IT",
    "pl": "PL",
    "nl": "NL",
    "th": "TH",
    "vi": "VN",
    "fa": "IR",
    "he": "IL",
    "bn": "BD",
    "ms": "MY",
    "tg": "TJ",
    "ky": "KG",
    "az": "AZ",
    "ka": "GE",
    "hy": "AM",
    "ro": "RO",
    "bg": "BG",
    "sr": "RS",
    "hr": "HR",
    "cs": "CZ",
    "hu": "HU",
    "el": "GR",
    "sv": "SE",
    "da": "DK",
    "fi": "FI",
    "no": "NO",
}

# Поддерживаемые языки и категории
SUPPORTED_LANGUAGES = [
    "RU", "EN", "ES", "PT", "AR", "DE", "FR", "ZH", "HI", "TR", "ID", "KO", "JA",
]

SUPPORTED_CATEGORIES = [
    "tech", "crypto", "news", "entertainment", "education",
    "business", "lifestyle", "politics", "gaming", "science",
]

# Ключевые слова для поиска каналов по языкам и категориям
# Используются для Telethon-поиска и TGStat
SEARCH_KEYWORDS: Dict[str, Dict[str, List[str]]] = {
    "RU": {
        "tech": ["технологии", "IT новости", "программирование", "разработка", "стартапы", "нейросети", "AI"],
        "crypto": ["криптовалюта", "биткоин", "трейдинг крипто", "DeFi", "блокчейн", "NFT"],
        "news": ["новости", "срочные новости", "политика", "аналитика", "мировые новости"],
        "entertainment": ["мемы", "юмор", "кино", "сериалы", "музыка", "развлечения"],
        "education": ["обучение", "курсы", "саморазвитие", "книги", "наука"],
        "business": ["бизнес", "предпринимательство", "маркетинг", "продажи", "финансы"],
        "lifestyle": ["лайфстайл", "путешествия", "еда", "здоровье", "фитнес", "мода"],
        "politics": ["политика", "геополитика", "экономика", "выборы"],
        "gaming": ["игры", "киберспорт", "Dota 2", "CS2", "Minecraft"],
        "science": ["наука", "космос", "физика", "биология", "медицина"],
    },
    "EN": {
        "tech": ["technology", "tech news", "programming", "software", "AI", "startups", "coding"],
        "crypto": ["cryptocurrency", "bitcoin", "ethereum", "trading crypto", "DeFi", "web3"],
        "news": ["breaking news", "world news", "politics", "analysis", "daily news"],
        "entertainment": ["memes", "funny", "movies", "music", "entertainment", "viral"],
        "education": ["education", "learning", "courses", "books", "self improvement"],
        "business": ["business", "entrepreneurship", "marketing", "finance", "investing"],
        "lifestyle": ["lifestyle", "travel", "food", "health", "fitness", "fashion"],
        "politics": ["politics", "geopolitics", "elections", "government"],
        "gaming": ["gaming", "esports", "game news", "PC gaming", "mobile games"],
        "science": ["science", "space", "physics", "biology", "medicine", "research"],
    },
    "ES": {
        "tech": ["tecnologia", "noticias tech", "programacion", "desarrollo", "inteligencia artificial"],
        "crypto": ["criptomonedas", "bitcoin", "trading", "blockchain", "DeFi"],
        "news": ["noticias", "ultima hora", "politica", "actualidad"],
        "entertainment": ["memes", "humor", "peliculas", "musica", "entretenimiento"],
        "education": ["educacion", "cursos", "libros", "aprendizaje"],
        "business": ["negocios", "emprendimiento", "marketing", "finanzas"],
        "lifestyle": ["viajes", "comida", "salud", "fitness", "moda"],
        "politics": ["politica", "elecciones", "gobierno", "geopolitica"],
        "gaming": ["juegos", "esports", "gaming", "videojuegos"],
        "science": ["ciencia", "espacio", "investigacion", "medicina"],
    },
    "PT": {
        "tech": ["tecnologia", "programacao", "desenvolvimento", "startups", "IA"],
        "crypto": ["criptomoedas", "bitcoin", "trading", "blockchain"],
        "news": ["noticias", "politica", "atualidades", "mundo"],
        "entertainment": ["memes", "humor", "filmes", "musica"],
        "education": ["educacao", "cursos", "livros", "aprendizado"],
        "business": ["negocios", "empreendedorismo", "marketing", "financas"],
        "lifestyle": ["viagens", "comida", "saude", "fitness"],
        "politics": ["politica", "governo", "eleicoes"],
        "gaming": ["jogos", "esports", "games"],
        "science": ["ciencia", "espaco", "pesquisa", "medicina"],
    },
    "AR": {
        "tech": ["تكنولوجيا", "برمجة", "ذكاء اصطناعي", "تقنية"],
        "crypto": ["عملات رقمية", "بيتكوين", "تداول", "بلوكتشين"],
        "news": ["اخبار", "عاجل", "سياسة", "عالم"],
        "entertainment": ["ترفيه", "كوميديا", "افلام", "موسيقى"],
        "education": ["تعليم", "دورات", "كتب", "علم"],
        "business": ["اعمال", "تسويق", "مال", "ريادة"],
        "lifestyle": ["سفر", "صحة", "رياضة", "طبخ"],
        "politics": ["سياسة", "جيوسياسة", "حكومة"],
        "gaming": ["العاب", "قيمنق", "رياضات الكترونية"],
        "science": ["علوم", "فضاء", "طب", "بحث"],
    },
    "DE": {
        "tech": ["Technologie", "Programmierung", "Software", "KI", "Tech News"],
        "crypto": ["Kryptowahrung", "Bitcoin", "Trading", "Blockchain"],
        "news": ["Nachrichten", "Politik", "Aktuelles", "Welt"],
        "entertainment": ["Memes", "Humor", "Filme", "Musik", "Unterhaltung"],
        "education": ["Bildung", "Kurse", "Bucher", "Lernen"],
        "business": ["Business", "Unternehmertum", "Marketing", "Finanzen"],
        "lifestyle": ["Reisen", "Essen", "Gesundheit", "Fitness"],
        "politics": ["Politik", "Wahlen", "Regierung", "Geopolitik"],
        "gaming": ["Spiele", "Esports", "Gaming"],
        "science": ["Wissenschaft", "Weltraum", "Forschung", "Medizin"],
    },
    "FR": {
        "tech": ["technologie", "programmation", "developpement", "IA", "startups"],
        "crypto": ["cryptomonnaie", "bitcoin", "trading", "blockchain"],
        "news": ["actualites", "politique", "monde", "breaking news"],
        "entertainment": ["memes", "humour", "films", "musique"],
        "education": ["education", "cours", "livres", "apprentissage"],
        "business": ["business", "entrepreneuriat", "marketing", "finances"],
        "lifestyle": ["voyages", "cuisine", "sante", "fitness"],
        "politics": ["politique", "elections", "gouvernement"],
        "gaming": ["jeux", "esports", "gaming"],
        "science": ["science", "espace", "recherche", "medecine"],
    },
    "ZH": {
        "tech": ["科技", "编程", "人工智能", "技术新闻", "互联网"],
        "crypto": ["加密货币", "比特币", "交易", "区块链", "DeFi"],
        "news": ["新闻", "时事", "政治", "世界"],
        "entertainment": ["娱乐", "搞笑", "电影", "音乐"],
        "education": ["教育", "学习", "课程", "读书"],
        "business": ["商业", "创业", "营销", "金融"],
        "lifestyle": ["旅行", "美食", "健康", "时尚"],
        "politics": ["政治", "选举", "国际"],
        "gaming": ["游戏", "电竞", "手游"],
        "science": ["科学", "太空", "医学", "研究"],
    },
    "HI": {
        "tech": ["तकनीक", "प्रोग्रामिंग", "टेक्नोलॉजी", "AI"],
        "crypto": ["क्रिप्टो", "बिटकॉइन", "ट्रेडिंग", "ब्लॉकचेन"],
        "news": ["समाचार", "ताजा खबर", "राजनीति", "दुनिया"],
        "entertainment": ["मनोरंजन", "कॉमेडी", "फिल्में", "संगीत"],
        "education": ["शिक्षा", "कोर्स", "किताबें", "सीखें"],
        "business": ["व्यापार", "मार्केटिंग", "फाइनेंस"],
        "lifestyle": ["यात्रा", "स्वास्थ्य", "फिटनेस", "खाना"],
        "politics": ["राजनीति", "चुनाव", "सरकार"],
        "gaming": ["गेमिंग", "खेल", "एस्पोर्ट्स"],
        "science": ["विज्ञान", "अंतरिक्ष", "चिकित्सा", "शोध"],
    },
    "TR": {
        "tech": ["teknoloji", "programlama", "yazilim", "yapay zeka"],
        "crypto": ["kripto", "bitcoin", "trading", "blockchain"],
        "news": ["haberler", "gundem", "siyaset", "dunya"],
        "entertainment": ["eglence", "komedi", "film", "muzik"],
        "education": ["egitim", "kurslar", "kitaplar", "ogrenme"],
        "business": ["is dunyasi", "girisimcilik", "pazarlama", "finans"],
        "lifestyle": ["seyahat", "saglik", "fitness", "yemek"],
        "politics": ["siyaset", "secimler", "hukumet"],
        "gaming": ["oyun", "espor", "gaming"],
        "science": ["bilim", "uzay", "tip", "arastirma"],
    },
    "ID": {
        "tech": ["teknologi", "programming", "software", "AI"],
        "crypto": ["kripto", "bitcoin", "trading", "blockchain"],
        "news": ["berita", "politik", "dunia", "terkini"],
        "entertainment": ["hiburan", "meme", "film", "musik"],
        "education": ["pendidikan", "kursus", "buku", "belajar"],
        "business": ["bisnis", "kewirausahaan", "pemasaran", "keuangan"],
        "lifestyle": ["perjalanan", "kesehatan", "kebugaran", "makanan"],
        "politics": ["politik", "pemilihan", "pemerintah"],
        "gaming": ["game", "esports", "gaming"],
        "science": ["ilmu", "antariksa", "kedokteran", "penelitian"],
    },
    "KO": {
        "tech": ["기술", "프로그래밍", "소프트웨어", "인공지능", "IT"],
        "crypto": ["암호화폐", "비트코인", "트레이딩", "블록체인"],
        "news": ["뉴스", "정치", "세계", "속보"],
        "entertainment": ["엔터테인먼트", "유머", "영화", "음악"],
        "education": ["교육", "강좌", "독서", "학습"],
        "business": ["비즈니스", "창업", "마케팅", "금융"],
        "lifestyle": ["여행", "건강", "피트니스", "음식"],
        "politics": ["정치", "선거", "정부"],
        "gaming": ["게임", "e스포츠", "게이밍"],
        "science": ["과학", "우주", "의학", "연구"],
    },
    "JA": {
        "tech": ["テクノロジー", "プログラミング", "ソフトウェア", "AI", "IT"],
        "crypto": ["仮想通貨", "ビットコイン", "トレーディング", "ブロックチェーン"],
        "news": ["ニュース", "政治", "世界", "速報"],
        "entertainment": ["エンタメ", "面白い", "映画", "音楽"],
        "education": ["教育", "学習", "コース", "読書"],
        "business": ["ビジネス", "起業", "マーケティング", "金融"],
        "lifestyle": ["旅行", "健康", "フィットネス", "グルメ"],
        "politics": ["政治", "選挙", "政府"],
        "gaming": ["ゲーム", "eスポーツ", "ゲーミング"],
        "science": ["科学", "宇宙", "医学", "研究"],
    },
}

# Маппинг TGStat категорий -> наши категории
TGSTAT_CATEGORY_MAP: Dict[str, str] = {
    "technologies": "tech",
    "it": "tech",
    "software": "tech",
    "startups": "tech",
    "crypto": "crypto",
    "cryptocurrency": "crypto",
    "blockchain": "crypto",
    "news": "news",
    "media": "news",
    "politics": "politics",
    "entertainment": "entertainment",
    "humor": "entertainment",
    "memes": "entertainment",
    "education": "education",
    "science": "science",
    "business": "business",
    "economics": "business",
    "marketing": "business",
    "finance": "business",
    "lifestyle": "lifestyle",
    "travel": "lifestyle",
    "food": "lifestyle",
    "health": "lifestyle",
    "sport": "lifestyle",
    "gaming": "gaming",
    "games": "gaming",
    "esports": "gaming",
}

# TGStat language codes
TGSTAT_LANG_MAP: Dict[str, str] = {
    "RU": "ru",
    "EN": "en",
    "ES": "es",
    "PT": "pt",
    "AR": "ar",
    "DE": "de",
    "FR": "fr",
    "ZH": "zh",
    "HI": "hi",
    "TR": "tr",
    "ID": "id",
    "KO": "ko",
    "JA": "ja",
}


# ---------------------------------------------------------------------------
# Детекция языка (расширенная версия)
# ---------------------------------------------------------------------------

import re

_LANG_PATTERNS: Dict[str, re.Pattern] = {
    "ru": re.compile(r"[А-Яа-яЁё]"),
    "ar": re.compile(r"[\u0600-\u06FF]"),
    "zh": re.compile(r"[\u4E00-\u9FFF]"),
    "ja": re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF]"),
    "ko": re.compile(r"[\uAC00-\uD7AF]"),
    "hi": re.compile(r"[\u0900-\u097F]"),
    "th": re.compile(r"[\u0E00-\u0E7F]"),
    "he": re.compile(r"[\u0590-\u05FF]"),
    "fa": re.compile(r"[\u0600-\u06FF]"),  # Overlap with Arabic, checked after
    "bn": re.compile(r"[\u0980-\u09FF]"),
}


def detect_language_extended(text: str) -> str:
    """Расширенная детекция языка по Unicode-диапазонам."""
    if not text:
        return "en"
    # Проверяем специфичные скрипты (порядок важен)
    for lang, pattern in _LANG_PATTERNS.items():
        matches = pattern.findall(text)
        if len(matches) >= 3:  # Минимум 3 символа для уверенности
            return lang
    return "en"


def infer_country_from_language(language: str, title: str = "", description: str = "") -> str:
    """Определяем страну по языку канала для координат на глобусе."""
    lang_lower = language.lower()
    return LANGUAGE_TO_COUNTRY.get(lang_lower, "US")


def get_coords_for_channel(
    language: str,
    country: Optional[str] = None,
    jitter: bool = True,
) -> tuple:
    """Получить координаты (lat, lng) для канала с опциональным jitter.

    Jitter добавляет случайное смещение +-2 градуса, чтобы каналы
    не накладывались друг на друга на глобусе.
    """
    if country and country.upper() in COUNTRY_COORDS:
        lat, lng = COUNTRY_COORDS[country.upper()]
    else:
        inferred = infer_country_from_language(language)
        lat, lng = COUNTRY_COORDS.get(inferred, (0.0, 0.0))

    if jitter:
        lat += random.uniform(-2.0, 2.0)
        lng += random.uniform(-2.0, 2.0)
        # Clamp latitude to valid range
        lat = max(-85.0, min(85.0, lat))

    return (round(lat, 4), round(lng, 4))


# ---------------------------------------------------------------------------
# Data class для промежуточного хранения спарсенных каналов
# ---------------------------------------------------------------------------

@dataclass
class ParsedChannel:
    """Промежуточное представление канала до записи в БД."""
    telegram_id: Optional[int] = None
    username: Optional[str] = None
    title: str = ""
    description: str = ""
    category: str = ""
    subcategory: str = ""
    language: str = "en"
    region: str = "global"
    country: str = ""
    member_count: int = 0
    has_comments: bool = False
    verified: bool = False
    source: str = "parsed"
    engagement_rate: Optional[float] = None
    post_frequency_daily: Optional[float] = None
    avg_comments_per_post: Optional[int] = None


# ---------------------------------------------------------------------------
# Existing usernames lookup (дедупликация / resume)
# ---------------------------------------------------------------------------

async def _load_existing_usernames(session: AsyncSession) -> Set[str]:
    """Загрузить все уже проиндексированные usernames из каталога."""
    result = await session.execute(
        select(ChannelMapEntry.username).where(
            ChannelMapEntry.tenant_id.is_(None),
            ChannelMapEntry.username.isnot(None),
        )
    )
    return {row[0].lower() for row in result.all() if row[0]}


async def _load_existing_telegram_ids(session: AsyncSession) -> Set[int]:
    """Загрузить все уже проиндексированные telegram_id из каталога."""
    result = await session.execute(
        select(ChannelMapEntry.telegram_id).where(
            ChannelMapEntry.tenant_id.is_(None),
            ChannelMapEntry.telegram_id.isnot(None),
        )
    )
    return {row[0] for row in result.all() if row[0]}


# ---------------------------------------------------------------------------
# Source 1: TGStat API
# ---------------------------------------------------------------------------

async def parse_tgstat(
    languages: List[str],
    categories: List[str],
    min_subscribers: int,
    limit: int,
) -> List[ParsedChannel]:
    """Парсинг каналов через TGStat API (tgstat.ru/api).

    Требует TGSTAT_API_TOKEN в env.
    API документация: https://api.tgstat.ru/docs/ru/start/intro.html
    """
    import aiohttp

    token = os.environ.get("TGSTAT_API_TOKEN")
    if not token:
        print("[ERROR] TGSTAT_API_TOKEN не установлен. Установите: export TGSTAT_API_TOKEN=your_token")
        print("        Получить токен: https://tgstat.ru/api")
        return []

    base_url = "https://api.tgstat.ru"
    channels: List[ParsedChannel] = []
    seen_ids: Set[str] = set()

    async with aiohttp.ClientSession() as http:
        for lang in languages:
            tg_lang = TGSTAT_LANG_MAP.get(lang.upper(), lang.lower())

            for cat in categories:
                if len(channels) >= limit:
                    break

                print(f"  [TGStat] Поиск: lang={lang}, category={cat}")

                # Основные ключевые слова для категории
                keywords = SEARCH_KEYWORDS.get(lang.upper(), {}).get(cat, [cat])

                for keyword in keywords[:3]:  # Максимум 3 ключевых слова на категорию
                    if len(channels) >= limit:
                        break

                    try:
                        # TGStat channels/search endpoint
                        params = {
                            "token": token,
                            "q": keyword,
                            "search_by": "title,about",
                            "country": tg_lang,
                            "participants_count": f"{min_subscribers};",
                            "limit": min(50, limit - len(channels)),
                        }

                        async with http.get(
                            f"{base_url}/channels/search",
                            params=params,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as resp:
                            if resp.status == 429:
                                print(f"    [TGStat] Rate limit hit, ждём 60с...")
                                await asyncio.sleep(60)
                                continue
                            if resp.status != 200:
                                print(f"    [TGStat] HTTP {resp.status} для '{keyword}'")
                                await asyncio.sleep(2)
                                continue

                            data = await resp.json()

                        if data.get("status") != "ok":
                            error = data.get("error", "unknown")
                            print(f"    [TGStat] API error: {error}")
                            await asyncio.sleep(2)
                            continue

                        items = data.get("response", {}).get("items", [])
                        if not items:
                            items = data.get("response", [])

                        for item in items:
                            if len(channels) >= limit:
                                break

                            username = (item.get("username") or "").strip().lstrip("@")
                            tg_id = item.get("id") or item.get("peer_id")
                            title = (item.get("title") or "").strip()

                            dedup_key = username.lower() if username else str(tg_id)
                            if dedup_key in seen_ids:
                                continue
                            seen_ids.add(dedup_key)

                            subs = int(item.get("participants_count", 0) or 0)
                            if subs < min_subscribers:
                                continue

                            description = (item.get("about") or item.get("description") or "").strip()
                            detected_lang = detect_language_extended(f"{title} {description}")
                            country = infer_country_from_language(detected_lang)

                            ch = ParsedChannel(
                                telegram_id=int(tg_id) if tg_id else None,
                                username=username or None,
                                title=title or f"channel_{tg_id}",
                                description=description[:2000] if description else "",
                                category=cat,
                                language=detected_lang,
                                region=_region_from_country(country),
                                country=country,
                                member_count=subs,
                                has_comments=bool(item.get("linked_chat_id")),
                                verified=bool(item.get("verified")),
                                source="tgstat",
                                engagement_rate=_safe_float(item.get("er")),
                                post_frequency_daily=_safe_float(item.get("avg_post_reach")),
                                avg_comments_per_post=_safe_int(item.get("avg_comments_count")),
                            )
                            channels.append(ch)

                        print(f"    [TGStat] +{len(items)} каналов для '{keyword}' (всего: {len(channels)})")

                    except asyncio.TimeoutError:
                        print(f"    [TGStat] Timeout для '{keyword}'")
                    except Exception as exc:
                        print(f"    [TGStat] Ошибка для '{keyword}': {exc}")

                    # Rate limiting между запросами
                    await asyncio.sleep(1.5)

            if len(channels) >= limit:
                break

    print(f"[TGStat] Итого: {len(channels)} каналов")
    return channels


# ---------------------------------------------------------------------------
# Source 2: Telemetr.io scraping
# ---------------------------------------------------------------------------

async def parse_telemetr(
    languages: List[str],
    categories: List[str],
    min_subscribers: int,
    limit: int,
) -> List[ParsedChannel]:
    """Парсинг каналов через Telemetr.io (scraping публичных каталогов).

    Не требует API-ключа, но подвержен rate limiting и блокировкам.
    """
    import aiohttp

    base_url = "https://telemetr.io"
    channels: List[ParsedChannel] = []
    seen_usernames: Set[str] = set()

    # Telemetr.io маппинг языков на регионы/пути
    lang_paths: Dict[str, str] = {
        "RU": "/channels/russia",
        "EN": "/channels/english",
        "ES": "/channels/spain",
        "PT": "/channels/brazil",
        "AR": "/channels/arab",
        "DE": "/channels/germany",
        "FR": "/channels/france",
        "ZH": "/channels/china",
        "HI": "/channels/india",
        "TR": "/channels/turkey",
        "ID": "/channels/indonesia",
        "KO": "/channels/south-korea",
        "JA": "/channels/japan",
    }

    # Telemetr.io категории
    cat_params: Dict[str, str] = {
        "tech": "technology",
        "crypto": "cryptocurrencies",
        "news": "news",
        "entertainment": "entertainment",
        "education": "education",
        "business": "business",
        "lifestyle": "lifestyle",
        "politics": "politics",
        "gaming": "games",
        "science": "science",
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }

    async with aiohttp.ClientSession(headers=headers) as http:
        for lang in languages:
            path = lang_paths.get(lang.upper())
            if not path:
                print(f"  [Telemetr] Язык {lang} не поддержан, пропуск")
                continue

            for cat in categories:
                if len(channels) >= limit:
                    break

                cat_param = cat_params.get(cat, cat)
                url = f"{base_url}{path}?category={cat_param}&sort=members&page=1"
                print(f"  [Telemetr] Запрос: {url}")

                for page in range(1, 21):  # Максимум 20 страниц
                    if len(channels) >= limit:
                        break

                    page_url = f"{base_url}{path}?category={cat_param}&sort=members&page={page}"

                    try:
                        async with http.get(
                            page_url,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as resp:
                            if resp.status == 429:
                                print(f"    [Telemetr] Rate limit, ждём 120с...")
                                await asyncio.sleep(120)
                                continue
                            if resp.status == 403:
                                print(f"    [Telemetr] Blocked (403), пропуск {lang}/{cat}")
                                break
                            if resp.status != 200:
                                print(f"    [Telemetr] HTTP {resp.status}")
                                break

                            html = await resp.text()

                        # Простой HTML-парсинг каналов из каталога Telemetr
                        parsed = _parse_telemetr_html(html, cat, lang.lower(), min_subscribers)

                        if not parsed:
                            break  # Нет больше каналов на странице

                        new_count = 0
                        for ch in parsed:
                            if len(channels) >= limit:
                                break
                            dedup_key = (ch.username or "").lower()
                            if dedup_key and dedup_key in seen_usernames:
                                continue
                            if dedup_key:
                                seen_usernames.add(dedup_key)
                            channels.append(ch)
                            new_count += 1

                        print(f"    [Telemetr] Страница {page}: +{new_count} каналов (всего: {len(channels)})")

                    except asyncio.TimeoutError:
                        print(f"    [Telemetr] Timeout на странице {page}")
                    except Exception as exc:
                        print(f"    [Telemetr] Ошибка: {exc}")
                        break

                    # Агрессивный rate limiting для scraping
                    await asyncio.sleep(random.uniform(3.0, 6.0))

    print(f"[Telemetr] Итого: {len(channels)} каналов")
    return channels


def _parse_telemetr_html(
    html: str,
    category: str,
    language: str,
    min_subscribers: int,
) -> List[ParsedChannel]:
    """Извлечение данных каналов из HTML-страницы Telemetr.io.

    Парсинг основан на типичной структуре каталога:
    - ссылки вида /channels/@username
    - блоки с подписчиками и описанием
    """
    channels: List[ParsedChannel] = []

    # Ищем ссылки на каналы
    username_pattern = re.compile(
        r'href="[^"]*/@([A-Za-z0-9_]{5,})"[^>]*>.*?'
        r'(?:title|channel-name)[^>]*>([^<]+)</.*?'
        r'(?:subscribers?|members?)[^>]*>([0-9,.\s]+[KkMm]?)',
        re.DOTALL | re.IGNORECASE,
    )

    # Fallback: простой поиск username-ов
    simple_pattern = re.compile(r'/@([A-Za-z0-9_]{5,32})')
    subs_pattern = re.compile(r'([\d,.\s]+)\s*(?:K|k|М|M|тыс|subscribers|подписчик)', re.IGNORECASE)

    usernames_found = simple_pattern.findall(html)
    subs_found = subs_pattern.findall(html)

    country = infer_country_from_language(language)

    for i, username in enumerate(usernames_found):
        subs = 0
        if i < len(subs_found):
            subs = _parse_subscriber_count(subs_found[i])
        if subs < min_subscribers and subs > 0:
            continue

        ch = ParsedChannel(
            username=username,
            title=username,
            category=category,
            language=language,
            region=_region_from_country(country),
            country=country,
            member_count=subs if subs >= min_subscribers else min_subscribers,
            source="telemetr",
        )
        channels.append(ch)

    return channels


# ---------------------------------------------------------------------------
# Source 3: Telethon direct search
# ---------------------------------------------------------------------------

async def parse_telethon(
    languages: List[str],
    categories: List[str],
    min_subscribers: int,
    limit: int,
) -> List[ParsedChannel]:
    """Прямой поиск каналов через Telethon API.

    Требует подключённую Telethon-сессию в SessionManager.
    Самый медленный метод, но не требует внешних API.
    """
    channels: List[ParsedChannel] = []
    seen_ids: Set[int] = set()

    # Попытка получить Telethon-клиент
    client = None
    try:
        from core.session_manager import SessionManager
        sm = SessionManager()
        phones = sm.get_connected_phones()
        for phone in phones:
            c = sm.get_client(phone)
            if c and c.is_connected():
                client = c
                break
    except Exception as exc:
        print(f"[Telethon] Не удалось получить клиент: {exc}")
        return []

    if client is None:
        print("[Telethon] Нет подключённых сессий. Используйте --source tgstat или telemetr.")
        return []

    try:
        from telethon import functions as tl_functions
        from telethon.tl.types import Channel as TLChannel
    except ImportError:
        print("[ERROR] telethon не установлен.")
        return []

    total_keywords = 0
    for lang in languages:
        for cat in categories:
            keywords = SEARCH_KEYWORDS.get(lang.upper(), {}).get(cat, [])
            total_keywords += len(keywords)

    kw_index = 0
    for lang in languages:
        for cat in categories:
            if len(channels) >= limit:
                break

            keywords = SEARCH_KEYWORDS.get(lang.upper(), {}).get(cat, [])
            if not keywords:
                continue

            for keyword in keywords:
                if len(channels) >= limit:
                    break

                kw_index += 1
                print(f"  [Telethon] [{kw_index}/{total_keywords}] Поиск: '{keyword}' ({lang}/{cat})")

                try:
                    result = await client(
                        tl_functions.contacts.SearchRequest(q=keyword, limit=100)
                    )
                except Exception as exc:
                    print(f"    [Telethon] Ошибка поиска '{keyword}': {exc}")
                    await asyncio.sleep(3)
                    continue

                batch_count = 0
                for chat in getattr(result, "chats", []):
                    if not isinstance(chat, TLChannel):
                        continue
                    if not getattr(chat, "broadcast", False):
                        continue

                    tg_id = int(chat.id)
                    if tg_id in seen_ids:
                        continue
                    seen_ids.add(tg_id)

                    subs = getattr(chat, "participants_count", 0) or 0
                    if subs < min_subscribers:
                        continue

                    title = (getattr(chat, "title", "") or "").strip()
                    username = getattr(chat, "username", None)

                    detected_lang = detect_language_extended(title)
                    country = infer_country_from_language(detected_lang)

                    ch = ParsedChannel(
                        telegram_id=tg_id,
                        username=username,
                        title=title or f"channel_{tg_id}",
                        category=cat,
                        language=detected_lang,
                        region=_region_from_country(country),
                        country=country,
                        member_count=subs,
                        has_comments=False,  # Нужен GetFullChannel для проверки
                        source="telethon",
                    )
                    channels.append(ch)
                    batch_count += 1

                if batch_count > 0:
                    print(f"    [Telethon] +{batch_count} каналов (всего: {len(channels)})")

                # Rate limiting для Telethon API
                await asyncio.sleep(random.uniform(2.0, 4.0))

    # Stage 2: обогащение данных (GetFullChannel) для топовых каналов
    print(f"\n[Telethon] Stage 2: обогащение топ-{min(len(channels), 100)} каналов...")
    enriched = 0
    for ch in sorted(channels, key=lambda c: c.member_count, reverse=True)[:100]:
        if ch.telegram_id is None:
            continue
        try:
            entity = await client.get_entity(ch.telegram_id)
            full = await client(tl_functions.channels.GetFullChannelRequest(channel=entity))
            full_chat = full.full_chat

            ch.description = (getattr(full_chat, "about", "") or "").strip()[:2000]
            ch.has_comments = bool(getattr(full_chat, "linked_chat_id", None))

            real_subs = getattr(full_chat, "participants_count", None)
            if real_subs:
                ch.member_count = int(real_subs)

            enriched += 1
            if enriched % 10 == 0:
                print(f"    [Telethon] Обогащено {enriched} каналов...")

            await asyncio.sleep(random.uniform(1.5, 3.0))
        except Exception as exc:
            log.debug(f"Telethon enrich failed for {ch.username}: {exc}")

    print(f"[Telethon] Итого: {len(channels)} каналов ({enriched} обогащено)")
    return channels


# ---------------------------------------------------------------------------
# DB insert: запись каналов в channel_map_entries
# ---------------------------------------------------------------------------

async def save_channels_to_db(
    channels: List[ParsedChannel],
    dry_run: bool = False,
) -> int:
    """Сохранение каналов в БД с дедупликацией.

    Returns: количество новых записей.
    """
    if dry_run:
        print(f"\n[DRY-RUN] Пропуск записи в БД. Каналы ({len(channels)}):")
        for i, ch in enumerate(channels[:50]):
            coords = get_coords_for_channel(ch.language, ch.country)
            print(
                f"  {i+1}. @{ch.username or '?'} | {ch.title[:40]} | "
                f"{ch.member_count:,} подписчиков | {ch.language} | {ch.category} | "
                f"({coords[0]}, {coords[1]}) | src={ch.source}"
            )
        if len(channels) > 50:
            print(f"  ... и ещё {len(channels) - 50} каналов")
        return 0

    await init_db()
    newly_saved = 0

    async with async_session() as session:
        existing_usernames = await _load_existing_usernames(session)
        existing_tg_ids = await _load_existing_telegram_ids(session)
        print(f"[DB] Уже в каталоге: {len(existing_usernames)} usernames, {len(existing_tg_ids)} telegram_id")

        batch: List[ChannelMapEntry] = []
        skipped = 0

        for ch in channels:
            # Дедупликация
            if ch.username and ch.username.lower() in existing_usernames:
                skipped += 1
                continue
            if ch.telegram_id and ch.telegram_id in existing_tg_ids:
                skipped += 1
                continue

            lat, lng = get_coords_for_channel(ch.language, ch.country)
            now = utcnow()

            entry = ChannelMapEntry(
                tenant_id=None,  # platform-level catalog
                telegram_id=ch.telegram_id,
                username=ch.username or (f"id{ch.telegram_id}" if ch.telegram_id else None),
                title=ch.title or "Unknown",
                description=ch.description[:2000] if ch.description else None,
                category=ch.category or None,
                language=ch.language,
                region=ch.region or "global",
                member_count=ch.member_count,
                has_comments=ch.has_comments,
                comments_enabled=ch.has_comments,
                verified=ch.verified,
                source=ch.source,
                engagement_rate=ch.engagement_rate,
                post_frequency_daily=ch.post_frequency_daily,
                avg_comments_per_post=ch.avg_comments_per_post,
                lat=lat,
                lng=lng,
                last_indexed_at=now,
                last_refreshed_at=now,
            )
            batch.append(entry)

            # Обновляем seen-множества
            if ch.username:
                existing_usernames.add(ch.username.lower())
            if ch.telegram_id:
                existing_tg_ids.add(ch.telegram_id)

            # Коммит батчами по 100
            if len(batch) >= 100:
                session.add_all(batch)
                await session.commit()
                newly_saved += len(batch)
                print(f"  [DB] Записано {newly_saved} каналов...")
                batch = []

        # Последний батч
        if batch:
            session.add_all(batch)
            await session.commit()
            newly_saved += len(batch)

        print(f"\n[DB] Готово: {newly_saved} новых, {skipped} пропущено (дубликаты)")

    return newly_saved


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _region_from_country(country: str) -> str:
    """Определяем регион по коду страны."""
    cis = {"RU", "UA", "BY", "KZ", "UZ", "KG", "TJ", "AZ", "GE", "AM", "MD"}
    eu = {
        "GB", "DE", "FR", "ES", "IT", "PT", "NL", "PL", "SE", "NO", "FI",
        "DK", "CH", "AT", "BE", "CZ", "RO", "GR", "HU", "BG", "RS", "HR", "IE",
    }
    mena = {"TR", "AE", "SA", "IR", "IQ", "IL", "EG", "QA", "KW", "JO", "LB", "MA", "DZ", "TN"}
    asia = {"CN", "JP", "KR", "IN", "ID", "TH", "VN", "PH", "MY", "SG", "PK", "BD", "TW", "HK"}
    americas = {"US", "BR", "MX", "AR", "CO", "CL", "PE", "VE", "CA"}
    africa = {"NG", "ZA", "KE", "ET", "GH"}

    uc = country.upper()
    if uc in cis:
        return "cis"
    if uc in eu:
        return "eu"
    if uc in mena:
        return "mena"
    if uc in asia:
        return "asia"
    if uc in americas:
        return "americas"
    if uc in africa:
        return "africa"
    return "global"


def _safe_float(val: Any) -> Optional[float]:
    """Безопасное преобразование в float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    """Безопасное преобразование в int."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_subscriber_count(raw: str) -> int:
    """Парсинг строки подписчиков: '12.5K' -> 12500, '1.2M' -> 1200000."""
    cleaned = raw.strip().replace(",", "").replace(" ", "").replace("\xa0", "")
    multiplier = 1

    if cleaned.lower().endswith(("k", "к", "тыс")):
        multiplier = 1000
        cleaned = re.sub(r"[kKкК]$|тыс$", "", cleaned)
    elif cleaned.lower().endswith(("m", "м", "млн")):
        multiplier = 1_000_000
        cleaned = re.sub(r"[mMмМ]$|млн$", "", cleaned)

    try:
        return int(float(cleaned) * multiplier)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Statistics / summary
# ---------------------------------------------------------------------------

def print_summary(channels: List[ParsedChannel]) -> None:
    """Вывод сводки по спарсенным каналам."""
    if not channels:
        print("\n[Summary] 0 каналов.")
        return

    print(f"\n{'='*60}")
    print(f"  СВОДКА: {len(channels)} каналов")
    print(f"{'='*60}")

    # По языкам
    by_lang: Dict[str, int] = {}
    for ch in channels:
        by_lang[ch.language] = by_lang.get(ch.language, 0) + 1
    print("\n  По языкам:")
    for lang, count in sorted(by_lang.items(), key=lambda x: -x[1]):
        print(f"    {lang.upper():5s} — {count:,}")

    # По категориям
    by_cat: Dict[str, int] = {}
    for ch in channels:
        by_cat[ch.category or "?"] = by_cat.get(ch.category or "?", 0) + 1
    print("\n  По категориям:")
    for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"    {cat:15s} — {count:,}")

    # По регионам
    by_region: Dict[str, int] = {}
    for ch in channels:
        by_region[ch.region] = by_region.get(ch.region, 0) + 1
    print("\n  По регионам:")
    for region, count in sorted(by_region.items(), key=lambda x: -x[1]):
        print(f"    {region:10s} — {count:,}")

    # Статистика подписчиков
    subs = [ch.member_count for ch in channels if ch.member_count > 0]
    if subs:
        print(f"\n  Подписчики:")
        print(f"    min:    {min(subs):>12,}")
        print(f"    max:    {max(subs):>12,}")
        print(f"    avg:    {sum(subs) // len(subs):>12,}")
        print(f"    median: {sorted(subs)[len(subs) // 2]:>12,}")

    # Топ-10 каналов
    top = sorted(channels, key=lambda c: c.member_count, reverse=True)[:10]
    print(f"\n  Топ-10 по подписчикам:")
    for i, ch in enumerate(top):
        print(
            f"    {i+1:2d}. @{(ch.username or '?')[:20]:20s} "
            f"| {ch.member_count:>10,} | {ch.language:3s} | {ch.category}"
        )

    print(f"\n{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def _main(args: argparse.Namespace) -> None:
    languages = [l.strip().upper() for l in args.language.split(",")] if args.language else SUPPORTED_LANGUAGES
    categories = [c.strip().lower() for c in args.category.split(",")] if args.category else SUPPORTED_CATEGORIES

    print(f"\n[Global Channel Parser]")
    print(f"  Источник:       {args.source}")
    print(f"  Языки:          {', '.join(languages)}")
    print(f"  Категории:      {', '.join(categories)}")
    print(f"  Мин. подписч.:  {args.min_subscribers:,}")
    print(f"  Лимит:          {args.limit:,}")
    print(f"  Dry-run:        {args.dry_run}")
    print()

    start_time = time.time()

    # Выбор источника
    if args.source == "tgstat":
        channels = await parse_tgstat(languages, categories, args.min_subscribers, args.limit)
    elif args.source == "telemetr":
        channels = await parse_telemetr(languages, categories, args.min_subscribers, args.limit)
    elif args.source == "telethon":
        channels = await parse_telethon(languages, categories, args.min_subscribers, args.limit)
    elif args.source == "all":
        # Комбинированный режим: TGStat -> Telemetr -> Telethon
        print("[Combo] Начинаем с TGStat...")
        channels = await parse_tgstat(languages, categories, args.min_subscribers, args.limit)

        remaining = args.limit - len(channels)
        if remaining > 0:
            print(f"\n[Combo] Добираем {remaining} из Telemetr...")
            extra = await parse_telemetr(languages, categories, args.min_subscribers, remaining)
            # Дедупликация
            seen = {(ch.username or "").lower() for ch in channels if ch.username}
            for ch in extra:
                if ch.username and ch.username.lower() not in seen:
                    channels.append(ch)
                    seen.add(ch.username.lower())

        remaining = args.limit - len(channels)
        if remaining > 0:
            print(f"\n[Combo] Добираем {remaining} из Telethon...")
            extra = await parse_telethon(languages, categories, args.min_subscribers, remaining)
            seen = {(ch.username or "").lower() for ch in channels if ch.username}
            for ch in extra:
                if ch.username and ch.username.lower() not in seen:
                    channels.append(ch)
                    seen.add(ch.username.lower())
    else:
        print(f"[ERROR] Неизвестный источник: {args.source}")
        sys.exit(1)

    elapsed = time.time() - start_time
    print(f"\n[Парсинг завершён за {elapsed:.1f}с]")

    # Сводка
    print_summary(channels)

    # Сохранение в БД
    if channels:
        saved = await save_channels_to_db(channels, dry_run=args.dry_run)
        if not args.dry_run:
            print(f"\n[Результат] {saved} новых каналов записано в channel_map_entries")

    # Экспорт в JSON (если --output)
    if args.output:
        export_data = []
        for ch in channels:
            lat, lng = get_coords_for_channel(ch.language, ch.country)
            export_data.append({
                "username": ch.username,
                "title": ch.title,
                "description": ch.description[:200] if ch.description else "",
                "category": ch.category,
                "language": ch.language,
                "region": ch.region,
                "country": ch.country,
                "member_count": ch.member_count,
                "has_comments": ch.has_comments,
                "source": ch.source,
                "lat": lat,
                "lng": lng,
            })
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        print(f"\n[Export] Сохранено в {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Глобальный парсер Telegram-каналов для Channel Map globe.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  # TGStat API (основной)
  python scripts/parse_global_channels.py --source tgstat --language RU,EN --category tech,crypto --limit 5000

  # Telemetr.io scraping
  python scripts/parse_global_channels.py --source telemetr --language RU,EN,ES --limit 3000

  # Telethon (требует сессию)
  python scripts/parse_global_channels.py --source telethon --language RU --category tech --limit 500

  # Все источники комбинированно
  python scripts/parse_global_channels.py --source all --limit 10000

  # Dry-run + экспорт в JSON
  python scripts/parse_global_channels.py --source tgstat --language RU --dry-run --output data/channels_export.json
        """,
    )
    parser.add_argument(
        "--source",
        choices=["tgstat", "telemetr", "telethon", "all"],
        default="tgstat",
        help="Источник данных (default: tgstat).",
    )
    parser.add_argument(
        "--language",
        metavar="LANGS",
        default=None,
        help="Языки через запятую: RU,EN,ES,... (default: все 13).",
    )
    parser.add_argument(
        "--category",
        metavar="CATS",
        default=None,
        help="Категории через запятую: tech,crypto,news,... (default: все 10).",
    )
    parser.add_argument(
        "--min-subscribers",
        type=int,
        default=5000,
        help="Минимальное число подписчиков (default: 5000).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Максимальное число каналов (default: 10000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать результаты, не записывать в БД.",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Экспорт результатов в JSON-файл.",
    )
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
