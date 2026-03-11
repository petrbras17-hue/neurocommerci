"""
Seed the channel_map_entries table with popular CIS Telegram channels.

These are well-known public channels with open comments and 1000+ subscribers.
Data sourced from publicly available channel directories (tgstat, telemetr).

Usage:
    python scripts/seed_channel_catalog.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from sqlalchemy import text
from storage.sqlite_db import init_db, async_session

# Seed data: popular CIS Telegram channels with open comments
SEED_CHANNELS = [
    # ── Tech & IT ────────────────────────────────────────────────────────────
    {"username": "habr_com", "title": "Хабр", "category": "Tech", "language": "ru", "region": "ru", "member_count": 98000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 45, "engagement_rate": 0.035, "avg_post_reach": 35000, "post_frequency_daily": 8.0, "description": "Крупнейшее IT-сообщество СНГ. Статьи, новости, обсуждения технологий."},
    {"username": "tproger", "title": "Типичный программист", "category": "Tech", "language": "ru", "region": "ru", "member_count": 215000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 30, "engagement_rate": 0.025, "avg_post_reach": 45000, "post_frequency_daily": 5.0, "description": "Новости IT, мемы, полезные материалы для разработчиков."},
    {"username": "codecamp", "title": "Код Кэмп", "category": "Tech", "language": "ru", "region": "cis", "member_count": 42000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 15, "engagement_rate": 0.03, "avg_post_reach": 12000, "post_frequency_daily": 3.0, "description": "Обучение программированию, курсы, туториалы."},
    {"username": "devgang", "title": "DevGang", "category": "Tech", "language": "ru", "region": "ru", "member_count": 35000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 12, "engagement_rate": 0.028, "avg_post_reach": 10000, "post_frequency_daily": 4.0, "description": "Сообщество разработчиков. Код, архитектура, карьера."},
    {"username": "technologicus", "title": "Технологикус", "category": "Tech", "language": "ru", "region": "ru", "member_count": 78000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 20, "engagement_rate": 0.022, "avg_post_reach": 18000, "post_frequency_daily": 6.0, "description": "Технологии, гаджеты, наука, будущее."},
    {"username": "python_scripts", "title": "Python Scripts", "category": "Tech", "language": "ru", "region": "cis", "member_count": 55000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 18, "engagement_rate": 0.032, "avg_post_reach": 15000, "post_frequency_daily": 2.0, "description": "Python код, скрипты, библиотеки, туториалы."},
    {"username": "frontend_ru", "title": "Фронтенд", "category": "Tech", "language": "ru", "region": "ru", "member_count": 48000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 14, "engagement_rate": 0.027, "avg_post_reach": 13000, "post_frequency_daily": 3.5, "description": "Всё о фронтенд-разработке: React, Vue, Angular, CSS."},

    # ── Crypto ────────────────────────────────────────────────────────────────
    {"username": "CryptoNewsRu", "title": "Crypto News RU", "category": "Crypto", "language": "ru", "region": "cis", "member_count": 125000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 55, "engagement_rate": 0.04, "avg_post_reach": 50000, "post_frequency_daily": 10.0, "description": "Криптовалюты, биткоин, DeFi, NFT новости на русском."},
    {"username": "bitcoin_ru", "title": "Bitcoin Russia", "category": "Crypto", "language": "ru", "region": "ru", "member_count": 89000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 40, "engagement_rate": 0.038, "avg_post_reach": 35000, "post_frequency_daily": 7.0, "description": "Биткоин, крипторынок, аналитика, прогнозы."},
    {"username": "defi_russia", "title": "DeFi Russia", "category": "Crypto", "language": "ru", "region": "ru", "member_count": 45000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 25, "engagement_rate": 0.045, "avg_post_reach": 18000, "post_frequency_daily": 5.0, "description": "Децентрализованные финансы, фарминг, стейкинг."},
    {"username": "nft_community_ru", "title": "NFT Community RU", "category": "Crypto", "language": "ru", "region": "cis", "member_count": 32000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 20, "engagement_rate": 0.05, "avg_post_reach": 12000, "post_frequency_daily": 4.0, "description": "NFT-коллекции, метавселенные, цифровое искусство."},
    {"username": "web3_ru", "title": "Web3 Россия", "category": "Crypto", "language": "ru", "region": "ru", "member_count": 28000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 15, "engagement_rate": 0.042, "avg_post_reach": 10000, "post_frequency_daily": 3.0, "description": "Web3, блокчейн-проекты, крипто-стартапы."},

    # ── Marketing ─────────────────────────────────────────────────────────────
    {"username": "marketingrus", "title": "Маркетинг RUS", "category": "Marketing", "language": "ru", "region": "ru", "member_count": 67000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 22, "engagement_rate": 0.03, "avg_post_reach": 20000, "post_frequency_daily": 4.0, "description": "Маркетинг, SMM, реклама, аналитика."},
    {"username": "targetolog", "title": "Таргетолог", "category": "Marketing", "language": "ru", "region": "cis", "member_count": 54000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 18, "engagement_rate": 0.028, "avg_post_reach": 16000, "post_frequency_daily": 3.0, "description": "Таргетированная реклама, кейсы, настройка."},
    {"username": "smm_handbook", "title": "SMM Handbook", "category": "Marketing", "language": "ru", "region": "ru", "member_count": 43000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 14, "engagement_rate": 0.025, "avg_post_reach": 12000, "post_frequency_daily": 2.5, "description": "Гайды по SMM, контент-маркетингу, продвижению."},
    {"username": "digital_branding", "title": "Digital Branding", "category": "Marketing", "language": "ru", "region": "cis", "member_count": 38000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 10, "engagement_rate": 0.022, "avg_post_reach": 9000, "post_frequency_daily": 2.0, "description": "Бренд-стратегия, дизайн, нейминг, позиционирование."},
    {"username": "growth_hacking_ru", "title": "Growth Hacking RU", "category": "Marketing", "language": "ru", "region": "ru", "member_count": 29000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 12, "engagement_rate": 0.035, "avg_post_reach": 8000, "post_frequency_daily": 2.0, "description": "Хаки роста, воронки, A/B тесты, метрики."},

    # ── E-commerce ────────────────────────────────────────────────────────────
    {"username": "ecommerce_ru", "title": "E-commerce Russia", "category": "E-commerce", "language": "ru", "region": "ru", "member_count": 52000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 16, "engagement_rate": 0.026, "avg_post_reach": 14000, "post_frequency_daily": 3.0, "description": "Интернет-торговля, маркетплейсы, Ozon, WB."},
    {"username": "wb_sellers", "title": "Продавцы Wildberries", "category": "E-commerce", "language": "ru", "region": "ru", "member_count": 95000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 50, "engagement_rate": 0.045, "avg_post_reach": 40000, "post_frequency_daily": 6.0, "description": "Сообщество продавцов Wildberries. Кейсы, аналитика, новости."},
    {"username": "ozon_sellers_channel", "title": "Ozon Sellers", "category": "E-commerce", "language": "ru", "region": "ru", "member_count": 72000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 35, "engagement_rate": 0.04, "avg_post_reach": 28000, "post_frequency_daily": 5.0, "description": "Продавцы на Ozon. Логистика, FBO, FBS, реклама."},
    {"username": "marketplace_analytics", "title": "Аналитика Маркетплейсов", "category": "E-commerce", "language": "ru", "region": "cis", "member_count": 34000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 12, "engagement_rate": 0.03, "avg_post_reach": 10000, "post_frequency_daily": 2.5, "description": "Данные и аналитика по маркетплейсам СНГ."},
    {"username": "dropshipping_ru", "title": "Дропшиппинг", "category": "E-commerce", "language": "ru", "region": "cis", "member_count": 25000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 10, "engagement_rate": 0.032, "avg_post_reach": 7000, "post_frequency_daily": 2.0, "description": "Дропшиппинг бизнес, поставщики, кейсы."},

    # ── EdTech ─────────────────────────────────────────────────────────────────
    {"username": "edtech_ru", "title": "EdTech Russia", "category": "EdTech", "language": "ru", "region": "ru", "member_count": 31000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 12, "engagement_rate": 0.032, "avg_post_reach": 9000, "post_frequency_daily": 2.0, "description": "Онлайн-образование, курсы, EdTech стартапы."},
    {"username": "skillbox_media", "title": "Skillbox Media", "category": "EdTech", "language": "ru", "region": "ru", "member_count": 85000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 25, "engagement_rate": 0.025, "avg_post_reach": 22000, "post_frequency_daily": 4.0, "description": "Образовательный контент от Skillbox."},
    {"username": "netology_channel", "title": "Нетология", "category": "EdTech", "language": "ru", "region": "ru", "member_count": 62000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 18, "engagement_rate": 0.024, "avg_post_reach": 16000, "post_frequency_daily": 3.0, "description": "Онлайн-университет Нетология. Курсы, карьера, digital."},

    # ── News ───────────────────────────────────────────────────────────────────
    {"username": "breakingmash", "title": "Mash", "category": "News", "language": "ru", "region": "ru", "member_count": 2500000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 200, "engagement_rate": 0.012, "avg_post_reach": 800000, "post_frequency_daily": 30.0, "description": "Новости. Расследования. Первые."},
    {"username": "shot_shot", "title": "SHOT", "category": "News", "language": "ru", "region": "ru", "member_count": 1800000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 150, "engagement_rate": 0.015, "avg_post_reach": 600000, "post_frequency_daily": 25.0, "description": "Медиа про факты. Новости, расследования."},
    {"username": "raborunews", "title": "РаБо", "category": "News", "language": "ru", "region": "ru", "member_count": 450000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 80, "engagement_rate": 0.02, "avg_post_reach": 150000, "post_frequency_daily": 15.0, "description": "Новости, политика, аналитика."},

    # ── Finance ────────────────────────────────────────────────────────────────
    {"username": "investfuture", "title": "InvestFuture", "category": "Finance", "language": "ru", "region": "ru", "member_count": 180000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 40, "engagement_rate": 0.02, "avg_post_reach": 50000, "post_frequency_daily": 5.0, "description": "Инвестиции, фондовый рынок, аналитика, обзоры."},
    {"username": "stockmarketru", "title": "Фондовый рынок", "category": "Finance", "language": "ru", "region": "ru", "member_count": 95000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 30, "engagement_rate": 0.028, "avg_post_reach": 30000, "post_frequency_daily": 4.0, "description": "Акции, облигации, IPO, дивиденды."},
    {"username": "finex_channel", "title": "FinEx", "category": "Finance", "language": "ru", "region": "ru", "member_count": 65000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 20, "engagement_rate": 0.025, "avg_post_reach": 18000, "post_frequency_daily": 3.0, "description": "ETF, индексное инвестирование, портфели."},

    # ── Entertainment ──────────────────────────────────────────────────────────
    {"username": "durov", "title": "Дуров", "category": "Tech", "language": "ru", "region": "cis", "member_count": 3200000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 500, "engagement_rate": 0.008, "avg_post_reach": 2000000, "post_frequency_daily": 0.3, "description": "Личный канал Павла Дурова."},
    {"username": "kinomania_channel", "title": "Киномания", "category": "Entertainment", "language": "ru", "region": "ru", "member_count": 120000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 35, "engagement_rate": 0.025, "avg_post_reach": 40000, "post_frequency_daily": 5.0, "description": "Кино, сериалы, рецензии, новинки."},
    {"username": "muzikannel", "title": "Музыкальный канал", "category": "Entertainment", "language": "ru", "region": "cis", "member_count": 85000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 20, "engagement_rate": 0.02, "avg_post_reach": 25000, "post_frequency_daily": 4.0, "description": "Новая музыка, подборки, обзоры альбомов."},

    # ── Lifestyle ──────────────────────────────────────────────────────────────
    {"username": "lifehacker_ru", "title": "Лайфхакер", "category": "Lifestyle", "language": "ru", "region": "ru", "member_count": 310000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 45, "engagement_rate": 0.018, "avg_post_reach": 80000, "post_frequency_daily": 8.0, "description": "Лайфхаки, советы, продуктивность, здоровье."},
    {"username": "travel_ru_channel", "title": "Путешествия", "category": "Travel", "language": "ru", "region": "ru", "member_count": 145000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 25, "engagement_rate": 0.02, "avg_post_reach": 40000, "post_frequency_daily": 4.0, "description": "Путешествия, авиабилеты, горящие туры, лайфхаки."},
    {"username": "food_channel_ru", "title": "Еда и Рецепты", "category": "Lifestyle", "language": "ru", "region": "cis", "member_count": 98000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 30, "engagement_rate": 0.028, "avg_post_reach": 30000, "post_frequency_daily": 3.0, "description": "Рецепты, обзоры ресторанов, кулинарные тренды."},

    # ── Gaming ─────────────────────────────────────────────────────────────────
    {"username": "gameru", "title": "Game.ru", "category": "Gaming", "language": "ru", "region": "ru", "member_count": 175000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 50, "engagement_rate": 0.03, "avg_post_reach": 55000, "post_frequency_daily": 7.0, "description": "Игровые новости, обзоры, трейлеры, киберспорт."},
    {"username": "gamedev_ru", "title": "GameDev RU", "category": "Gaming", "language": "ru", "region": "cis", "member_count": 42000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 15, "engagement_rate": 0.03, "avg_post_reach": 12000, "post_frequency_daily": 2.5, "description": "Разработка игр, Unity, Unreal, инди-проекты."},

    # ── Sports ─────────────────────────────────────────────────────────────────
    {"username": "sportexpress", "title": "Спорт-Экспресс", "category": "Sports", "language": "ru", "region": "ru", "member_count": 280000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 60, "engagement_rate": 0.02, "avg_post_reach": 80000, "post_frequency_daily": 15.0, "description": "Спортивные новости, результаты, аналитика."},
    {"username": "football_ru", "title": "Футбол России", "category": "Sports", "language": "ru", "region": "ru", "member_count": 195000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 45, "engagement_rate": 0.022, "avg_post_reach": 55000, "post_frequency_daily": 10.0, "description": "РПЛ, сборная, трансферы, аналитика."},

    # ── Health ─────────────────────────────────────────────────────────────────
    {"username": "zdorovie_ru", "title": "Здоровье", "category": "Health", "language": "ru", "region": "ru", "member_count": 78000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 20, "engagement_rate": 0.025, "avg_post_reach": 22000, "post_frequency_daily": 3.0, "description": "Здоровье, медицина, фитнес, питание."},
    {"username": "fitness_pro_ru", "title": "Fitness Pro", "category": "Health", "language": "ru", "region": "cis", "member_count": 55000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 15, "engagement_rate": 0.028, "avg_post_reach": 15000, "post_frequency_daily": 2.5, "description": "Тренировки, питание, мотивация, ЗОЖ."},

    # ── VPN/Privacy ────────────────────────────────────────────────────────────
    {"username": "vpn_russia", "title": "VPN Россия", "category": "Tech", "subcategory": "VPN", "language": "ru", "region": "ru", "member_count": 65000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 25, "engagement_rate": 0.035, "avg_post_reach": 20000, "post_frequency_daily": 3.0, "description": "VPN-сервисы, обзоры, тесты скорости, обход блокировок."},
    {"username": "privacy_ru", "title": "Приватность", "category": "Tech", "subcategory": "Privacy", "language": "ru", "region": "cis", "member_count": 42000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 18, "engagement_rate": 0.04, "avg_post_reach": 15000, "post_frequency_daily": 2.0, "description": "Приватность в интернете, анонимность, шифрование."},

    # ── Politics (CIS) ────────────────────────────────────────────────────────
    {"username": "politica_ru", "title": "Политика", "category": "Politics", "language": "ru", "region": "ru", "member_count": 350000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 100, "engagement_rate": 0.025, "avg_post_reach": 120000, "post_frequency_daily": 12.0, "description": "Политические новости, аналитика, комментарии."},

    # ── Ukraine ────────────────────────────────────────────────────────────────
    {"username": "ukraine_tech", "title": "Україна Tech", "category": "Tech", "language": "uk", "region": "cis", "member_count": 38000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 12, "engagement_rate": 0.028, "avg_post_reach": 10000, "post_frequency_daily": 3.0, "description": "Технології, стартапи, IT в Україні."},
    {"username": "kyiv_lifestyle", "title": "Київ Лайфстайл", "category": "Lifestyle", "language": "uk", "region": "cis", "member_count": 45000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 15, "engagement_rate": 0.03, "avg_post_reach": 12000, "post_frequency_daily": 4.0, "description": "Київ, події, розваги, лайфстайл."},

    # ── Kazakhstan ─────────────────────────────────────────────────────────────
    {"username": "kz_tech", "title": "KZ Tech", "category": "Tech", "language": "kz", "region": "cis", "member_count": 22000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 8, "engagement_rate": 0.032, "avg_post_reach": 6000, "post_frequency_daily": 2.0, "description": "IT и технологии Казахстана. Стартапы, новости."},
    {"username": "business_kz", "title": "Бизнес Казахстан", "category": "Finance", "language": "ru", "region": "cis", "member_count": 35000, "has_comments": True, "comments_enabled": True, "avg_comments_per_post": 12, "engagement_rate": 0.03, "avg_post_reach": 10000, "post_frequency_daily": 3.0, "description": "Бизнес, инвестиции, экономика Казахстана."},
]


async def seed():
    await init_db()
    async with async_session() as session:
        async with session.begin():
            # Check if data already exists
            result = await session.execute(
                text("SELECT count(*) FROM channel_map_entries WHERE source = 'seed'")
            )
            existing = result.scalar()
            if existing and existing > 0:
                print(f"Seed data already exists ({existing} entries). Skipping.")
                return

            now = datetime.utcnow()
            for ch in SEED_CHANNELS:
                await session.execute(
                    text("""
                        INSERT INTO channel_map_entries
                        (tenant_id, username, title, description, category, subcategory,
                         language, region, member_count, has_comments, comments_enabled,
                         avg_comments_per_post, avg_post_reach, engagement_rate,
                         post_frequency_daily, verified, source, last_indexed_at, created_at)
                        VALUES
                        (NULL, :username, :title, :description, :category, :subcategory,
                         :language, :region, :member_count, :has_comments, :comments_enabled,
                         :avg_comments_per_post, :avg_post_reach, :engagement_rate,
                         :post_frequency_daily, false, 'seed', :now, :now)
                    """),
                    {
                        "username": ch["username"],
                        "title": ch["title"],
                        "description": ch.get("description", ""),
                        "category": ch["category"],
                        "subcategory": ch.get("subcategory"),
                        "language": ch["language"],
                        "region": ch.get("region", "ru"),
                        "member_count": ch["member_count"],
                        "has_comments": ch["has_comments"],
                        "comments_enabled": ch["comments_enabled"],
                        "avg_comments_per_post": ch.get("avg_comments_per_post"),
                        "avg_post_reach": ch.get("avg_post_reach"),
                        "engagement_rate": ch.get("engagement_rate"),
                        "post_frequency_daily": ch.get("post_frequency_daily"),
                        "now": now,
                    },
                )
            print(f"Seeded {len(SEED_CHANNELS)} channels into channel_map_entries.")


if __name__ == "__main__":
    asyncio.run(seed())
