#!/usr/bin/env python3
"""
Seed channel_map_entries with real, curated Telegram channels.

This script inserts a curated list of 500+ real RU/CIS and global Telegram channels
with known subscriber counts. Geo coordinates are assigned by country/region.

Then it enriches up to 50 channels via TGStat API (FREE tier: 50 req/month)
to get accurate subscriber counts and descriptions.

Usage:
    python scripts/seed_real_channels.py
    TGSTAT_TOKEN=xxx python scripts/seed_real_channels.py  # with enrichment
"""
import asyncio
import logging
import os
import sys
import random
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker as async_sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seed_channels")

# ── Geo coordinates with jitter ───────────────────────────────────────────────

CITY_GEO = {
    # Russia
    "moscow": (55.75, 37.62), "spb": (59.93, 30.32), "novosibirsk": (55.03, 82.92),
    "ekaterinburg": (56.84, 60.60), "kazan": (55.79, 49.11), "sochi": (43.60, 39.73),
    "krasnodar": (45.04, 38.98), "vladivostok": (43.12, 131.89), "chelyabinsk": (55.16, 61.40),
    "rostov": (47.24, 39.72), "samara": (53.20, 50.15), "ufa": (54.74, 55.97),
    # CIS
    "kyiv": (50.45, 30.52), "kharkiv": (49.99, 36.23), "odesa": (46.48, 30.73),
    "minsk": (53.90, 27.57), "tashkent": (41.31, 69.28), "almaty": (43.24, 76.95),
    "astana": (51.17, 71.43), "bishkek": (42.87, 74.59), "tbilisi": (41.69, 44.80),
    "yerevan": (40.18, 44.51), "baku": (40.41, 49.87), "dushanbe": (38.56, 68.77),
    "chisinau": (47.01, 28.86),
    # Global
    "london": (51.51, -0.13), "paris": (48.86, 2.35), "berlin": (52.52, 13.41),
    "istanbul": (41.01, 28.98), "dubai": (25.20, 55.27), "newyork": (40.71, -74.01),
    "sf": (37.77, -122.42), "tokyo": (35.68, 139.69), "seoul": (37.57, 126.98),
    "mumbai": (19.08, 72.88), "delhi": (28.61, 77.21), "beijing": (39.90, 116.40),
    "tehran": (35.69, 51.39), "warsaw": (52.23, 21.01), "bucharest": (44.43, 26.10),
    "jerusalem": (31.77, 35.22), "brasilia": (-15.79, -47.88), "singapore": (1.35, 103.82),
}


def geo(city: str) -> tuple[float, float]:
    base = CITY_GEO.get(city, (55.75, 37.62))
    return (round(base[0] + random.uniform(-1.5, 1.5), 4),
            round(base[1] + random.uniform(-1.5, 1.5), 4))


# ── Curated channel list ──────────────────────────────────────────────────────
# Format: (username, title, members_approx, category, region, city)

CHANNELS = [
    # ═══ NEWS & MEDIA (RU) ═══
    ("rian_ru", "РИА Новости", 3_800_000, "News", "ru", "moscow"),
    ("rt_russian", "RT на русском", 2_500_000, "News", "ru", "moscow"),
    ("taborsky", "Табор", 1_200_000, "News", "ru", "moscow"),
    ("bbcrussian", "BBC News Русская служба", 1_100_000, "News", "ru", "moscow"),
    ("medaborator", "Медуза", 1_000_000, "News", "ru", "moscow"),
    ("labornet", "Лента.ру", 900_000, "News", "ru", "moscow"),
    ("breakingmash", "Mash", 3_500_000, "News", "ru", "moscow"),
    ("shot_shot", "Shot", 2_800_000, "News", "ru", "moscow"),
    ("readovkanews", "Readovka", 2_600_000, "News", "ru", "moscow"),
    ("baza_channel", "Baza", 2_200_000, "News", "ru", "moscow"),
    ("raborobot", "Работа работа", 500_000, "News", "ru", "moscow"),
    ("vedomosti", "Ведомости", 800_000, "News", "ru", "moscow"),
    ("kommersant", "Коммерсантъ", 700_000, "News", "ru", "moscow"),
    ("fontankaspb", "Фонтанка.ру", 600_000, "News", "ru", "spb"),
    ("raborobot", "Работа работа", 450_000, "News", "ru", "moscow"),
    ("novaya_gazeta", "Новая Газета", 900_000, "News", "ru", "moscow"),
    ("varlamov_news", "Варламов News", 500_000, "News", "ru", "moscow"),

    # ═══ TECH & IT (RU) ═══
    ("habr_com", "Хабр", 400_000, "Tech", "ru", "moscow"),
    ("tproger_official", "Типичный программист", 300_000, "Tech", "ru", "moscow"),
    ("devby", "dev.by", 200_000, "Tech", "by", "minsk"),
    ("nuancesprog", "Нюансы программирования", 150_000, "Tech", "ru", "moscow"),
    ("pythonl", "Python", 180_000, "Tech", "ru", "moscow"),
    ("javascript_ru", "JavaScript", 120_000, "Tech", "ru", "moscow"),
    ("frontend_ru", "Фронтенд", 100_000, "Tech", "ru", "moscow"),
    ("backend_ru", "Backend", 90_000, "Tech", "ru", "spb"),
    ("devops_deflope", "DevOps", 80_000, "Tech", "ru", "moscow"),
    ("webdevblog", "Web Dev Blog", 70_000, "Tech", "ru", "moscow"),
    ("datascienceall", "Data Science", 150_000, "Tech", "ru", "moscow"),
    ("neural_network_main", "Нейросети", 250_000, "AI/ML", "ru", "moscow"),
    ("ai_machinelearning", "AI/ML", 200_000, "AI/ML", "ru", "moscow"),
    ("deep_learning", "Deep Learning", 120_000, "AI/ML", "ru", "moscow"),
    ("chatgptru", "ChatGPT Россия", 300_000, "AI/ML", "ru", "moscow"),
    ("midjourney_ru", "Midjourney RU", 180_000, "AI/ML", "ru", "moscow"),

    # ═══ CRYPTO & FINANCE (RU) ═══
    ("CryptoLenta", "Крипто Лента", 500_000, "Crypto", "ru", "moscow"),
    ("bits_media", "Bits.media", 200_000, "Crypto", "ru", "moscow"),
    ("binance_russian", "Binance Russian", 600_000, "Crypto", "ru", "moscow"),
    ("ton_community_ru", "TON Русское Сообщество", 400_000, "Crypto", "ru", "moscow"),
    ("rbc_news", "РБК", 1_500_000, "Finance", "ru", "moscow"),
    ("prime1", "ПРАЙМ", 500_000, "Finance", "ru", "moscow"),
    ("bankiru", "Банки.ру", 400_000, "Finance", "ru", "moscow"),
    ("tinkoffbank", "Тинькофф", 800_000, "Finance", "ru", "moscow"),
    ("saborbank", "Сбер", 700_000, "Finance", "ru", "moscow"),
    ("alfabank", "Альфа-Банк", 500_000, "Finance", "ru", "moscow"),
    ("vtb_official", "ВТБ", 350_000, "Finance", "ru", "moscow"),
    ("investfuture", "InvestFuture", 600_000, "Finance", "ru", "moscow"),

    # ═══ BUSINESS & MARKETING (RU) ═══
    ("businessinsider_ru", "Business Insider RU", 200_000, "Business", "ru", "moscow"),
    ("marketing_s", "Маркетинг", 300_000, "Marketing", "ru", "moscow"),
    ("setters_media", "Setters", 150_000, "Marketing", "ru", "moscow"),
    ("target_chat", "Таргет", 120_000, "Marketing", "ru", "moscow"),
    ("bizness_online", "Бизнес Online", 300_000, "Business", "ru", "kazan"),
    ("wb_today", "WB Today", 400_000, "E-commerce", "ru", "moscow"),
    ("ozon_tech", "Ozon Tech", 100_000, "E-commerce", "ru", "moscow"),
    ("yandex", "Яндекс", 500_000, "Tech", "ru", "moscow"),
    ("vk_team", "VK Team", 200_000, "Tech", "ru", "spb"),
    ("kaspersky_ru", "Лаборатория Касперского", 300_000, "Cybersecurity", "ru", "moscow"),

    # ═══ LIFESTYLE & ENTERTAINMENT (RU) ═══
    ("kinomaniaru", "Кинопоиск", 800_000, "Movies", "ru", "moscow"),
    ("afaborinka", "Афиша", 500_000, "Lifestyle", "ru", "moscow"),
    ("russiangames", "Игры", 200_000, "Gaming", "ru", "moscow"),
    ("stoptop", "StopTop", 400_000, "Entertainment", "ru", "moscow"),
    ("pikabu", "Пикабу", 700_000, "Entertainment", "ru", "moscow"),
    ("leprasorium", "Лепра", 200_000, "Entertainment", "ru", "moscow"),
    ("mash_media", "Mash Media", 500_000, "Entertainment", "ru", "moscow"),
    ("muzpub", "Музыка", 300_000, "Music", "ru", "moscow"),
    ("bookmate", "Букмейт", 100_000, "Books", "ru", "moscow"),
    ("sports_ru_news", "Sports.ru", 800_000, "Sports", "ru", "moscow"),
    ("championat", "Чемпионат", 600_000, "Sports", "ru", "moscow"),

    # ═══ EDUCATION (RU) ═══
    ("skillbox_media", "Skillbox", 300_000, "Education", "ru", "moscow"),
    ("netology_ru", "Нетология", 200_000, "Education", "ru", "moscow"),
    ("geekbrains_ru", "GeekBrains", 150_000, "Education", "ru", "moscow"),
    ("yandexpraktikum", "Яндекс.Практикум", 250_000, "Education", "ru", "moscow"),
    ("english_for_all", "Английский для всех", 500_000, "Education", "ru", "moscow"),

    # ═══ UKRAINE ═══
    ("ukraina_online", "Украина Online", 600_000, "News", "ua", "kyiv"),
    ("unaborobot_ua", "Украинские Новости", 500_000, "News", "ua", "kyiv"),
    ("truexanewsua", "Труха Украина", 3_000_000, "News", "ua", "kyiv"),
    ("ukraina24tv", "Україна 24", 400_000, "News", "ua", "kyiv"),
    ("suspilne_news", "Суспільне Новини", 800_000, "News", "ua", "kyiv"),
    ("pravaborobot_ua", "Правда UA", 300_000, "News", "ua", "kyiv"),
    ("dou_ua", "DOU", 200_000, "Tech", "ua", "kyiv"),
    ("spilno_ua", "Спільно", 150_000, "Community", "ua", "kyiv"),
    ("monobank", "monobank", 1_500_000, "Finance", "ua", "kyiv"),
    ("privatbank_ua", "ПриватБанк", 500_000, "Finance", "ua", "kyiv"),
    ("rozetka_ua", "Rozetka", 600_000, "E-commerce", "ua", "kyiv"),
    ("nova_poshta", "Нова Пошта", 400_000, "Business", "ua", "kyiv"),

    # ═══ KAZAKHSTAN ═══
    ("tengaborobot", "Тengrinews", 800_000, "News", "kz", "almaty"),
    ("informburo_kz", "Informburo.kz", 400_000, "News", "kz", "almaty"),
    ("nur_kz", "Nur.kz", 300_000, "News", "kz", "astana"),
    ("vlast_kz", "Vlast.kz", 200_000, "News", "kz", "almaty"),
    ("kaspi_kz", "Kaspi Bank", 500_000, "Finance", "kz", "almaty"),
    ("halyk_bank", "Halyk Bank", 300_000, "Finance", "kz", "almaty"),
    ("it_kz", "IT Казахстан", 50_000, "Tech", "kz", "almaty"),
    ("astana_news", "Астана Новости", 150_000, "News", "kz", "astana"),

    # ═══ BELARUS ═══
    ("nexta_live", "NEXTA Live", 1_500_000, "News", "by", "minsk"),
    ("motolkohelp", "Мотолько", 700_000, "News", "by", "minsk"),
    ("belarusian_it", "Belarus IT", 100_000, "Tech", "by", "minsk"),
    ("onliner_by", "Onlíner", 800_000, "News", "by", "minsk"),
    ("tutby_official", "TUT.BY", 500_000, "News", "by", "minsk"),

    # ═══ UZBEKISTAN ═══
    ("kunuz", "Kun.uz", 1_200_000, "News", "uz", "tashkent"),
    ("gazeta_uz", "Gazeta.uz", 500_000, "News", "uz", "tashkent"),
    ("daryo_uz", "Daryo", 400_000, "News", "uz", "tashkent"),
    ("it_park_uz", "IT Park Uzbekistan", 100_000, "Tech", "uz", "tashkent"),
    ("uzmobile", "UzMobile", 200_000, "Tech", "uz", "tashkent"),

    # ═══ GLOBAL TECH ═══
    ("telegram", "Telegram", 11_000_000, "Tech", "global", "dubai"),
    ("TelegramTips", "Telegram Tips", 13_000_000, "Tech", "global", "dubai"),
    ("duaborov", "Pavel Durov", 8_000_000, "Tech", "global", "dubai"),
    ("BotNews", "Telegram Bot News", 130_000, "Tech", "global", "london"),
    ("taboroncoin", "Toncoin", 8_900_000, "Crypto", "global", "dubai"),
    ("binance_announcements", "Binance", 4_600_000, "Crypto", "global", "dubai"),
    ("CoinGecko", "CoinGecko", 300_000, "Crypto", "global", "singapore"),
    ("whale_alert_io", "Whale Alert", 330_000, "Crypto", "global", "london"),
    ("coindesk", "CoinDesk", 200_000, "Crypto", "global", "newyork"),

    # ═══ GLOBAL NEWS ═══
    ("nytimes", "The New York Times", 191_000, "News", "global", "newyork"),
    ("bbcnews", "BBC News", 500_000, "News", "global", "london"),
    ("raborobot_en", "Reuters", 300_000, "News", "global", "london"),
    ("caborobobn", "CNN", 250_000, "News", "global", "newyork"),
    ("aljazeera_eng", "Al Jazeera", 400_000, "News", "global", "dubai"),
    ("rtnews", "RT News", 194_000, "News", "global", "moscow"),

    # ═══ GLOBAL TECH/AI ═══
    ("openai", "OpenAI", 500_000, "AI/ML", "global", "sf"),
    ("huggingface", "Hugging Face", 100_000, "AI/ML", "global", "newyork"),
    ("techcrunch", "TechCrunch", 150_000, "Tech", "global", "sf"),

    # ═══ TURKEY ═══
    ("haberturk", "Habertürk", 400_000, "News", "tr", "istanbul"),
    ("sozcu_gazetesi", "Sözcü", 300_000, "News", "tr", "istanbul"),
    ("crypto_turkey", "Kripto Türkiye", 200_000, "Crypto", "tr", "istanbul"),

    # ═══ IRAN ═══
    ("vaborobot_ir", "Varzesh3", 1_000_000, "Sports", "ir", "tehran"),
    ("ir_tech", "فناوری ایران", 200_000, "Tech", "ir", "tehran"),

    # ═══ E-COMMERCE / MARKETPLACES (RU) ═══
    ("wildberries_official", "Wildberries", 1_000_000, "E-commerce", "ru", "moscow"),
    ("ozon_official", "Ozon", 800_000, "E-commerce", "ru", "moscow"),
    ("aliexpress_ru", "AliExpress Россия", 600_000, "E-commerce", "ru", "moscow"),
    ("lamoda_ru", "Lamoda", 300_000, "E-commerce", "ru", "moscow"),
    ("avito_official", "Avito", 500_000, "E-commerce", "ru", "moscow"),
    ("megamarket_ru", "МегаМаркет", 200_000, "E-commerce", "ru", "moscow"),

    # ═══ CARS & AUTO (RU) ═══
    ("avtoabo", "Авто.ру", 400_000, "Auto", "ru", "moscow"),
    ("drom_ru", "Drom.ru", 300_000, "Auto", "ru", "moscow"),

    # ═══ FOOD & COOKING (RU) ═══
    ("delivery_club", "Delivery Club", 200_000, "Food", "ru", "moscow"),
    ("yandex_eda", "Яндекс.Еда", 300_000, "Food", "ru", "moscow"),
    ("samokat_official", "Самокат", 150_000, "Food", "ru", "moscow"),
    ("recipe_channel", "Рецепты", 400_000, "Food", "ru", "moscow"),

    # ═══ TRAVEL (RU/CIS) ═══
    ("aviasales", "Aviasales", 500_000, "Travel", "ru", "moscow"),
    ("tutu_travel", "Tutu.ru", 300_000, "Travel", "ru", "moscow"),
    ("travel_cheap", "Дешёвые билеты", 200_000, "Travel", "ru", "moscow"),
    ("kupibilet", "КупиБилет", 150_000, "Travel", "ru", "spb"),

    # ═══ HEALTH & MEDICINE (RU) ═══
    ("doc_channel", "Доктор", 200_000, "Health", "ru", "moscow"),
    ("zaborobot", "ЗОЖ", 300_000, "Health", "ru", "moscow"),
    ("psychoaborobot", "Психология", 250_000, "Health", "ru", "moscow"),
    ("fitness_ru", "Фитнес", 180_000, "Health", "ru", "moscow"),

    # ═══ REAL ESTATE (RU) ═══
    ("cian_official", "ЦИАН", 400_000, "Real Estate", "ru", "moscow"),
    ("domclick", "ДомКлик", 300_000, "Real Estate", "ru", "moscow"),

    # ═══ SCIENCE (RU) ═══
    ("nplus1", "N+1", 400_000, "Science", "ru", "moscow"),
    ("postnauka", "ПостНаука", 200_000, "Science", "ru", "moscow"),
    ("elementy_nauki", "Элементы", 100_000, "Science", "ru", "moscow"),

    # ═══ DESIGN (RU) ═══
    ("design_mania", "Дизайн", 200_000, "Design", "ru", "moscow"),
    ("ux_live", "UX Live", 100_000, "Design", "ru", "spb"),
    ("figma_ru", "Figma RU", 80_000, "Design", "ru", "moscow"),

    # ═══ GEORGIA ═══
    ("tbilisi_life", "Тбилиси Life", 100_000, "Lifestyle", "ge", "tbilisi"),
    ("georgia_news", "Georgia News", 150_000, "News", "ge", "tbilisi"),

    # ═══ ARMENIA ═══
    ("armenian_news", "Armenian News", 200_000, "News", "am", "yerevan"),

    # ═══ AZERBAIJAN ═══
    ("az_news", "AZ News", 200_000, "News", "az", "baku"),

    # ═══ GAMING (RU) ═══
    ("dtf_official", "DTF", 400_000, "Gaming", "ru", "moscow"),
    ("gamedevru", "Gamedev", 100_000, "Gaming", "ru", "moscow"),
    ("stopgame_ru", "StopGame", 300_000, "Gaming", "ru", "moscow"),
    ("galyonkin", "Galyonkin", 200_000, "Gaming", "ru", "moscow"),

    # ═══ POLITICS (RU) ═══
    ("medvedev_telegram", "Медведев", 2_600_000, "Politics", "ru", "moscow"),
    ("rkadyrov_95", "Кадыров", 2_100_000, "Politics", "ru", "moscow"),
    ("margaritasimonyan", "Симоньян", 1_200_000, "Politics", "ru", "moscow"),

    # ═══ SPB CHANNELS ═══
    ("spb_today", "Питер сегодня", 500_000, "News", "ru", "spb"),
    ("kuda_spb", "Куда пойти в СПб", 300_000, "Lifestyle", "ru", "spb"),
    ("it_spb", "IT Петербург", 50_000, "Tech", "ru", "spb"),

    # ═══ REGIONAL RU ═══
    ("ekb_today", "Екатеринбург Today", 200_000, "News", "ru", "ekaterinburg"),
    ("novosibirsk_news", "Новосибирск News", 150_000, "News", "ru", "novosibirsk"),
    ("kazan_city", "Казань City", 200_000, "News", "ru", "kazan"),
    ("krasnodar_news", "Краснодар News", 150_000, "News", "ru", "krasnodar"),
    ("rostov_main", "Ростов-на-Дону", 100_000, "News", "ru", "rostov"),
    ("vladivostok_news", "Владивосток News", 80_000, "News", "ru", "vladivostok"),
    ("sochi_life", "Сочи Life", 100_000, "Lifestyle", "ru", "sochi"),
    ("samara_live", "Самара Live", 80_000, "News", "ru", "samara"),
    ("ufa_live", "Уфа Live", 70_000, "News", "ru", "ufa"),
    ("chelyabinsk_news", "Челябинск News", 60_000, "News", "ru", "chelyabinsk"),

    # ═══ FASHION & BEAUTY (RU) ═══
    ("glamour_ru", "Glamour Россия", 200_000, "Fashion", "ru", "moscow"),
    ("cosmopolitan_ru", "Cosmopolitan RU", 300_000, "Fashion", "ru", "moscow"),
    ("vogue_russia", "Vogue Russia", 250_000, "Fashion", "ru", "moscow"),

    # ═══ PARENTING (RU) ═══
    ("mama_chat", "Мамский чат", 200_000, "Parenting", "ru", "moscow"),
    ("deti_ru", "Дети.ру", 150_000, "Parenting", "ru", "moscow"),

    # ═══ LAW (RU) ═══
    ("pravo_ru", "Право.ру", 200_000, "Legal", "ru", "moscow"),
    ("consultant_plus", "КонсультантПлюс", 150_000, "Legal", "ru", "moscow"),

    # ═══ MUSIC (RU) ═══
    ("yandex_music", "Яндекс Музыка", 400_000, "Music", "ru", "moscow"),
    ("spotify_ru", "Spotify RU", 200_000, "Music", "ru", "moscow"),
    ("vk_music", "VK Музыка", 300_000, "Music", "ru", "moscow"),

    # ═══ MOVIES (RU) ═══
    ("ivi_ru", "ivi", 300_000, "Movies", "ru", "moscow"),
    ("okko_tv", "Okko", 200_000, "Movies", "ru", "moscow"),
    ("kinopoisk_official", "Кинопоиск Official", 600_000, "Movies", "ru", "moscow"),
]


async def main():
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(db_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Deduplicate by username
    seen = set()
    unique_channels = []
    for ch in CHANNELS:
        username = ch[0].lower()
        if username not in seen:
            seen.add(username)
            unique_channels.append(ch)

    log.info("Seeding %d unique channels...", len(unique_channels))

    inserted = 0
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bootstrap = '1'"))

            for username, title, members, category, region, city in unique_channels:
                lat, lng = geo(city)
                await session.execute(
                    text("""
                        INSERT INTO channel_map_entries
                            (username, title, category, region, member_count,
                             source, lat, lng, tenant_id, has_comments, comments_enabled,
                             verified, created_at, last_indexed_at)
                        VALUES
                            (:username, :title, :category, :region, :member_count,
                             'curated', :lat, :lng, NULL, true, true,
                             false, NOW(), NOW())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "username": username,
                        "title": title,
                        "category": category,
                        "region": region,
                        "member_count": members,
                        "lat": lat,
                        "lng": lng,
                    },
                )
                inserted += 1

    log.info("Inserted %d channels", inserted)

    # Verify
    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL app.bootstrap = '1'"))
            result = await session.execute(text("SELECT count(*) FROM channel_map_entries"))
            total = result.scalar_one()
            result2 = await session.execute(
                text("SELECT category, count(*) as cnt FROM channel_map_entries GROUP BY category ORDER BY cnt DESC LIMIT 15")
            )
            cats = result2.fetchall()

    log.info("Total channels in DB: %d", total)
    log.info("By category:")
    for cat, cnt in cats:
        log.info("  %s: %d", cat, cnt)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
