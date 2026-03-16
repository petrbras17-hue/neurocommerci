"""
Programmatic SEO page engine for NEURO COMMENTING.

Generates hundreds of long-tail landing pages from structured data templates:
- "Telegram marketing for {industry}" (7 industries x 9 languages)
- "Telegram growth in {country}" (20 countries x 9 languages)
- "{tool} alternative" (5 competitors)
- "How to {action} on Telegram" (10 guides)

Each page gets a unique title, meta description, H1, 500-800 words of content,
Schema.org FAQ + Article markup, hreflang for all 9 languages, internal links,
and CTA. All pages are auto-included in the sitemap.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "seo_pages.json"

SUPPORTED_LANGUAGES = ["ru", "en", "es", "pt", "tr", "de", "fr", "zh", "ar"]
DEFAULT_LANGUAGE = "en"

# Fallback content generation templates (for languages without full data)
_INDUSTRY_CONTENT_TEMPLATE_EN = (
    "Telegram has become one of the most powerful marketing channels for {industry_name} brands. "
    "With over 800 million monthly active users, the platform offers unmatched organic reach "
    "through channel commenting, audience parsing, and content distribution. "
    "NEURO COMMENTING helps {industry_name} businesses automate their Telegram growth with "
    "AI-powered commenting in 10 styles, a global channel map of 100K+ channels, "
    "7-phase account warmup, and content factory that repurposes one piece of content "
    "for 6 platforms simultaneously. The platform supports 9 languages and serves brands "
    "across RU/CIS, LATAM, MENA, Europe, and Asia."
)

_COUNTRY_CONTENT_TEMPLATE_EN = (
    "Telegram in {country_name} has {telegram_mau} monthly active users, making it one of "
    "the key markets for Telegram-based growth strategies. Local businesses and international "
    "brands targeting {country_name} can leverage AI-powered commenting to build organic "
    "presence in local channels. NEURO COMMENTING's Channel Map indexes thousands of channels "
    "in the {region} region, with category filtering, engagement metrics, and subscriber data. "
    "The AI generates comments in the local language, matching the tone and style of each channel. "
    "Combined with 7-phase account warmup and anti-detection technology, brands can scale their "
    "Telegram presence in {country_name} safely and sustainably."
)

_INDUSTRY_CONTENT_TEMPLATE_RU = (
    "Telegram стал одним из мощнейших маркетинговых каналов для брендов в сфере {industry_name}. "
    "С более чем 800 миллионами активных пользователей ежемесячно, платформа предлагает "
    "непревзойдённый органический охват через комментирование в каналах, парсинг аудитории "
    "и дистрибуцию контента. NEURO COMMENTING помогает бизнесам в сфере {industry_name} "
    "автоматизировать рост в Telegram с AI-комментингом в 10 стилях, глобальной картой "
    "100K+ каналов, 7-фазным прогревом аккаунтов и контент-фабрикой, которая превращает "
    "один пост в 6 форматов для разных платформ. Платформа поддерживает 9 языков и обслуживает "
    "бренды по всему миру: РФ/СНГ, LATAM, MENA, Европа и Азия."
)

_COUNTRY_CONTENT_TEMPLATE_RU = (
    "Telegram в стране {country_name} насчитывает {telegram_mau} активных пользователей ежемесячно, "
    "что делает его одним из ключевых рынков для Telegram-стратегий роста. Локальные компании "
    "и международные бренды, нацеленные на {country_name}, могут использовать AI-комментинг "
    "для построения органического присутствия в местных каналах. Карта каналов NEURO COMMENTING "
    "индексирует тысячи каналов в регионе {region} с фильтрацией по категориям, метриками "
    "вовлечённости и данными по подписчикам. AI генерирует комментарии на местном языке, "
    "соответствуя тону и стилю каждого канала. В сочетании с 7-фазным прогревом и антидетект-"
    "технологией бренды могут безопасно и устойчиво масштабировать присутствие в Telegram "
    "в {country_name}."
)

# Default FAQ for pages without custom FAQ data
_DEFAULT_FAQ_EN = [
    {"q": "What is NEURO COMMENTING?", "a": "NEURO COMMENTING is a Telegram Growth OS — an all-in-one SaaS platform for AI-powered commenting, channel parsing, account warmup, content repurposing, and growth analytics."},
    {"q": "How much does it cost?", "a": "NEURO COMMENTING offers a free 14-day trial. Paid plans start from $49/month with features scaling up to the Business plan at $499/month."},
    {"q": "Is it safe to use?", "a": "Yes. The platform includes a 7-phase account warmup engine, anti-detection technology with 3 modes, and human-like activity patterns that minimize ban risk."},
]

_DEFAULT_FAQ_RU = [
    {"q": "Что такое NEURO COMMENTING?", "a": "NEURO COMMENTING — это Telegram Growth OS, SaaS-платформа для AI-комментинга, парсинга каналов, прогрева аккаунтов, репёрпозинга контента и аналитики роста."},
    {"q": "Сколько это стоит?", "a": "NEURO COMMENTING предлагает бесплатный 14-дневный пробный период. Платные тарифы начинаются от $49/мес и масштабируются до Business-плана за $499/мес."},
    {"q": "Это безопасно?", "a": "Да. Платформа включает 7-фазный прогрев, антидетект-технологию с 3 режимами и человекоподобные паттерны активности, минимизирующие риск бана."},
]

# Internal links inserted into every SEO page
_INTERNAL_LINKS = [
    {"url": "/", "label_en": "Home", "label_ru": "Главная"},
    {"url": "/pricing", "label_en": "Pricing", "label_ru": "Тарифы"},
    {"url": "/blog", "label_en": "Blog", "label_ru": "Блог"},
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SEOPage:
    """Represents a single programmatic SEO page."""
    slug: str
    template_type: str  # "industry", "country", "competitor", "how_to"
    canonical_path: str  # e.g. "/telegram-marketing-for-ecommerce"
    title: str
    description: str
    h1: str
    intro: str
    sections: List[Dict[str, str]] = field(default_factory=list)
    faq: List[Dict[str, str]] = field(default_factory=list)
    comparison: List[Dict[str, str]] = field(default_factory=list)
    verdict: str = ""
    cta_text: str = ""
    stats: Dict[str, str] = field(default_factory=dict)
    related_pages: List[Dict[str, str]] = field(default_factory=list)
    lang: str = "en"
    hreflang_urls: Dict[str, str] = field(default_factory=dict)
    breadcrumbs: List[Dict[str, str]] = field(default_factory=list)
    schema_json: str = ""
    # Extra SEO metadata
    word_count: int = 0
    internal_links: List[Dict[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_seo_data() -> Dict[str, Any]:
    """Load and cache SEO page data from JSON."""
    if not _DATA_FILE.is_file():
        logger.warning("SEO data file not found: %s", _DATA_FILE)
        return {}
    try:
        with open(_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load SEO data: %s", exc)
        return {}


def clear_seo_cache() -> None:
    """Clear cached SEO data (for hot reload in development)."""
    _load_seo_data.cache_clear()


# ---------------------------------------------------------------------------
# Page generators
# ---------------------------------------------------------------------------

def _get_localized(data: Dict[str, Any], lang: str, key: str, fallback: str = "") -> str:
    """Get a localized string from nested data, falling back to EN, then fallback."""
    lang_data = data.get(lang, {})
    if isinstance(lang_data, dict):
        val = lang_data.get(key, "")
        if val:
            return val
    # Fallback to English
    en_data = data.get("en", {})
    if isinstance(en_data, dict):
        val = en_data.get(key, "")
        if val:
            return val
    return fallback


def _build_hreflang_urls(base_path: str) -> Dict[str, str]:
    """Build hreflang URL map for all 9 supported languages."""
    urls: Dict[str, str] = {}
    for lang_code in SUPPORTED_LANGUAGES:
        urls[lang_code] = f"{base_path}?lang={lang_code}"
    urls["x-default"] = base_path
    return urls


def _build_breadcrumbs(template_type: str, page_name: str, page_path: str, lang: str) -> List[Dict[str, str]]:
    """Build breadcrumb chain for a page."""
    home_label = "Главная" if lang == "ru" else "Home"
    crumbs = [{"name": home_label, "url": "/"}]

    type_labels = {
        "industry": {"en": "Industries", "ru": "Индустрии"},
        "country": {"en": "Countries", "ru": "Страны"},
        "competitor": {"en": "Comparisons", "ru": "Сравнения"},
        "how_to": {"en": "Guides", "ru": "Гайды"},
    }
    type_label = type_labels.get(template_type, {}).get(lang, type_labels.get(template_type, {}).get("en", "Pages"))
    crumbs.append({"name": type_label, "url": ""})
    crumbs.append({"name": page_name, "url": page_path})
    return crumbs


def _build_faq_schema(faq: List[Dict[str, str]]) -> str:
    """Build JSON-LD FAQPage schema."""
    if not faq:
        return ""
    main_entity = []
    for item in faq:
        main_entity.append({
            "@type": "Question",
            "name": item.get("q", ""),
            "acceptedAnswer": {
                "@type": "Answer",
                "text": item.get("a", ""),
            },
        })
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": main_entity,
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)


def _build_article_schema(page: SEOPage, base_url: str) -> str:
    """Build JSON-LD Article schema."""
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": page.h1,
        "description": page.description,
        "author": {
            "@type": "Organization",
            "name": "NEURO COMMENTING",
        },
        "publisher": {
            "@type": "Organization",
            "name": "NEURO COMMENTING",
            "url": base_url,
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": f"{base_url}{page.canonical_path}",
        },
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)


def _build_breadcrumb_schema(breadcrumbs: List[Dict[str, str]], base_url: str) -> str:
    """Build JSON-LD BreadcrumbList schema."""
    items = []
    for i, crumb in enumerate(breadcrumbs, start=1):
        item: Dict[str, Any] = {
            "@type": "ListItem",
            "position": i,
            "name": crumb["name"],
        }
        if crumb.get("url"):
            item["item"] = f"{base_url}{crumb['url']}"
        items.append(item)
    schema = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": items,
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)


def _count_words(text: str) -> int:
    """Rough word count for CJK and Latin text."""
    return len(text.split())


def _get_default_faq(lang: str) -> List[Dict[str, str]]:
    if lang == "ru":
        return list(_DEFAULT_FAQ_RU)
    return list(_DEFAULT_FAQ_EN)


def _get_internal_links(lang: str) -> List[Dict[str, str]]:
    result = []
    for link in _INTERNAL_LINKS:
        label = link.get(f"label_{lang}", link.get("label_en", "Link"))
        result.append({"url": link["url"], "label": label})
    return result


# ---------------------------------------------------------------------------
# Industry pages
# ---------------------------------------------------------------------------

def _generate_industry_page(industry_key: str, industry_data: Dict[str, Any], lang: str) -> Optional[SEOPage]:
    """Generate a single industry SEO page."""
    slug = industry_data.get("slug", industry_key)
    canonical_path = f"/telegram-marketing-for-{slug}"

    title = _get_localized(industry_data, lang, "headline", f"Telegram Marketing for {slug}")
    description = _get_localized(industry_data, lang, "description", f"Grow your {slug} brand with Telegram marketing.")
    h1 = _get_localized(industry_data, lang, "h1", title)
    intro = _get_localized(industry_data, lang, "intro", "")
    name = _get_localized(industry_data, lang, "name", slug.replace("-", " ").title())

    # Generate content sections
    sections: List[Dict[str, str]] = []
    lang_data = industry_data.get(lang, {})
    if isinstance(lang_data, dict) and lang_data.get("sections"):
        sections = lang_data["sections"]
    elif industry_data.get("en", {}).get("sections"):
        sections = industry_data["en"]["sections"]

    # If no sections, generate from template
    if not sections:
        template = _INDUSTRY_CONTENT_TEMPLATE_RU if lang == "ru" else _INDUSTRY_CONTENT_TEMPLATE_EN
        generated_text = template.format(industry_name=name)
        section_title = "Как работает Telegram-маркетинг" if lang == "ru" else "How Telegram Marketing Works"
        sections = [{"h2": section_title, "content": generated_text}]

    if not intro:
        intro = sections[0].get("content", "")[:200] + "..." if sections else ""

    # FAQ
    faq: List[Dict[str, str]] = []
    if isinstance(lang_data, dict) and lang_data.get("faq"):
        faq = lang_data["faq"]
    elif industry_data.get("en", {}).get("faq"):
        faq = industry_data["en"]["faq"]
    if not faq:
        faq = _get_default_faq(lang)

    cta_text = _get_localized(industry_data, lang, "cta_text", "")
    if not cta_text:
        cta_text = "Начать бесплатный пробный период" if lang == "ru" else "Start your free 14-day trial"

    stats = industry_data.get("stats", {})

    # Word count
    all_text = intro + " " + " ".join(s.get("content", "") for s in sections)
    word_count = _count_words(all_text)

    breadcrumbs = _build_breadcrumbs("industry", name, canonical_path, lang)
    hreflang_urls = _build_hreflang_urls(canonical_path)

    return SEOPage(
        slug=slug,
        template_type="industry",
        canonical_path=canonical_path,
        title=title,
        description=description,
        h1=h1,
        intro=intro,
        sections=sections,
        faq=faq,
        cta_text=cta_text,
        stats=stats,
        lang=lang,
        hreflang_urls=hreflang_urls,
        breadcrumbs=breadcrumbs,
        word_count=word_count,
        internal_links=_get_internal_links(lang),
    )


# ---------------------------------------------------------------------------
# Country pages
# ---------------------------------------------------------------------------

def _generate_country_page(country_key: str, country_data: Dict[str, Any], lang: str) -> Optional[SEOPage]:
    """Generate a single country SEO page."""
    slug = country_data.get("slug", country_key)
    canonical_path = f"/telegram-growth-in-{slug}"

    title = _get_localized(country_data, lang, "headline", f"Telegram Growth in {slug}")
    description = _get_localized(country_data, lang, "description", f"Grow your brand in {slug}'s Telegram market.")
    h1 = _get_localized(country_data, lang, "h1", title)
    name = _get_localized(country_data, lang, "name", slug.replace("-", " ").title())
    telegram_mau = country_data.get("telegram_mau", "N/A")
    region = country_data.get("region", "")

    # Generate content
    template = _COUNTRY_CONTENT_TEMPLATE_RU if lang == "ru" else _COUNTRY_CONTENT_TEMPLATE_EN
    intro = template.format(
        country_name=name,
        telegram_mau=telegram_mau,
        region=region,
    )

    # Build sections
    if lang == "ru":
        sections = [
            {"h2": f"Рынок Telegram в стране {name}", "content": f"{name} — один из ключевых рынков Telegram с {telegram_mau} ежемесячных пользователей. Регион {region} показывает стабильный рост использования Telegram для бизнеса, маркетинга и коммуникаций. Местные бренды всё активнее используют Telegram-каналы для продвижения."},
            {"h2": f"AI-комментинг для бизнеса в {name}", "content": "NEURO COMMENTING предлагает AI-комментинг, адаптированный под местный рынок. Платформа генерирует комментарии на языке целевой аудитории, подстраиваясь под тон и стиль местных каналов. 10 стилей комментариев, A/B-тестирование и антидетект-технология обеспечивают безопасный и эффективный рост."},
            {"h2": "Функции платформы", "content": "Глобальная карта 100K+ каналов с фильтрацией по региону, категории и вовлечённости. 7-фазный автономный прогрев аккаунтов. Ферма до 50 потоков на каждую. Контент-фабрика: один пост превращается в 6 форматов для разных платформ. Агентский пакет с white label для маркетинговых агентств."},
        ]
    else:
        sections = [
            {"h2": f"Telegram Market in {name}", "content": f"{name} is a key Telegram market with {telegram_mau} monthly users. The {region} region shows consistent growth in Telegram usage for business, marketing, and communications. Local brands increasingly use Telegram channels for promotion and audience engagement."},
            {"h2": f"AI Commenting for Business in {name}", "content": "NEURO COMMENTING offers AI commenting adapted for the local market. The platform generates comments in the target audience's language, matching the tone and style of local channels. 10 commenting styles, A/B testing, and anti-detection technology ensure safe and effective growth."},
            {"h2": "Platform Features", "content": "Global Channel Map of 100K+ channels with region, category, and engagement filtering. 7-phase autonomous account warmup. Farm orchestrator with up to 50 threads per farm. Content factory: one post becomes 6 formats for different platforms. Agency package with white label for marketing agencies."},
        ]

    faq = _get_default_faq(lang)
    cta_text = "Начать бесплатный пробный период" if lang == "ru" else "Start your free 14-day trial"

    stats = {
        "telegram_mau": telegram_mau,
        "region": region,
        "country_code": country_data.get("code", ""),
    }

    all_text = intro + " " + " ".join(s.get("content", "") for s in sections)
    word_count = _count_words(all_text)

    breadcrumbs = _build_breadcrumbs("country", name, canonical_path, lang)
    hreflang_urls = _build_hreflang_urls(canonical_path)

    return SEOPage(
        slug=slug,
        template_type="country",
        canonical_path=canonical_path,
        title=title,
        description=description,
        h1=h1,
        intro=intro,
        sections=sections,
        faq=faq,
        cta_text=cta_text,
        stats=stats,
        lang=lang,
        hreflang_urls=hreflang_urls,
        breadcrumbs=breadcrumbs,
        word_count=word_count,
        internal_links=_get_internal_links(lang),
    )


# ---------------------------------------------------------------------------
# Competitor pages
# ---------------------------------------------------------------------------

def _generate_competitor_page(comp_key: str, comp_data: Dict[str, Any], lang: str) -> Optional[SEOPage]:
    """Generate a single competitor comparison SEO page."""
    slug = comp_data.get("slug", comp_key)
    canonical_path = f"/{slug}-alternative"

    title = _get_localized(comp_data, lang, "headline", f"{slug} Alternative: NEURO COMMENTING")
    description = _get_localized(comp_data, lang, "description", f"Compare {slug} with NEURO COMMENTING.")
    h1 = _get_localized(comp_data, lang, "h1", title)
    intro = _get_localized(comp_data, lang, "intro", "")
    name = comp_data.get("name", slug)

    # Comparison table
    comparison: List[Dict[str, str]] = []
    lang_data = comp_data.get(lang, {})
    if isinstance(lang_data, dict) and lang_data.get("comparison"):
        comparison = lang_data["comparison"]
    elif comp_data.get("en", {}).get("comparison"):
        comparison = comp_data["en"]["comparison"]

    verdict = _get_localized(comp_data, lang, "verdict", "")

    # Build sections from comparison
    sections: List[Dict[str, str]] = []
    if intro:
        sections.append({"h2": f"{name} vs NEURO COMMENTING", "content": intro})
    if verdict:
        if lang == "ru":
            sections.append({"h2": "Итог", "content": verdict})
        else:
            sections.append({"h2": "Verdict", "content": verdict})

    # Add a section about migration
    if lang == "ru":
        sections.append({
            "h2": f"Как перейти с {name} на NEURO COMMENTING",
            "content": f"Переход с {name} занимает менее часа. Зарегистрируйтесь для бесплатного 14-дневного пробного периода, импортируйте ваши аккаунты и настройки, и начните использовать расширенные функции: 10 стилей комментинга, глобальную карту каналов, 7-фазный прогрев и контент-фабрику."
        })
    else:
        sections.append({
            "h2": f"How to Switch from {name} to NEURO COMMENTING",
            "content": f"Switching from {name} takes less than an hour. Sign up for a free 14-day trial, import your accounts and settings, and start using advanced features: 10 commenting styles, global Channel Map, 7-phase warmup, and Content Factory."
        })

    faq = _get_default_faq(lang)
    cta_text = "Начать бесплатный пробный период" if lang == "ru" else "Start your free 14-day trial"

    all_text = intro + " " + verdict + " " + " ".join(s.get("content", "") for s in sections)
    word_count = _count_words(all_text)

    breadcrumbs = _build_breadcrumbs("competitor", f"{name} Alternative", canonical_path, lang)
    hreflang_urls = _build_hreflang_urls(canonical_path)

    return SEOPage(
        slug=slug,
        template_type="competitor",
        canonical_path=canonical_path,
        title=title,
        description=description,
        h1=h1,
        intro=intro,
        sections=sections,
        faq=faq,
        comparison=comparison,
        verdict=verdict,
        cta_text=cta_text,
        stats={"price": comp_data.get("price", ""), "modules": str(comp_data.get("modules", 0))},
        lang=lang,
        hreflang_urls=hreflang_urls,
        breadcrumbs=breadcrumbs,
        word_count=word_count,
        internal_links=_get_internal_links(lang),
    )


# ---------------------------------------------------------------------------
# How-to pages
# ---------------------------------------------------------------------------

def _generate_howto_page(action_key: str, action_data: Dict[str, Any], lang: str) -> Optional[SEOPage]:
    """Generate a single how-to SEO page."""
    slug = action_data.get("slug", action_key)
    canonical_path = f"/how-to-{slug}"

    title = _get_localized(action_data, lang, "headline", f"How to {slug}")
    description = _get_localized(action_data, lang, "description", f"Complete guide to {slug}.")
    h1 = _get_localized(action_data, lang, "h1", title)
    intro = _get_localized(action_data, lang, "intro", "")

    # Sections
    sections: List[Dict[str, str]] = []
    lang_data = action_data.get(lang, {})
    if isinstance(lang_data, dict) and lang_data.get("sections"):
        sections = lang_data["sections"]
    elif action_data.get("en", {}).get("sections"):
        sections = action_data["en"]["sections"]

    # If no sections, generate minimal content
    if not sections:
        if lang == "ru":
            sections = [
                {"h2": "Пошаговый гайд", "content": f"{intro} В этом разделе мы подробно разберём каждый шаг."},
                {"h2": "Инструменты и настройка", "content": "NEURO COMMENTING предоставляет все необходимые инструменты для решения этой задачи. Платформа включает AI-комментинг в 10 стилях, глобальную карту 100K+ каналов, 7-фазный прогрев аккаунтов и контент-фабрику на 6 платформ."},
                {"h2": "Советы и лучшие практики", "content": "Начинайте с малого и масштабируйте постепенно. Используйте A/B-тестирование стилей комментирования. Всегда прогревайте аккаунты перед активным использованием. Следите за аналитикой и корректируйте стратегию."},
            ]
        else:
            sections = [
                {"h2": "Step-by-Step Guide", "content": f"{intro} In this section we break down each step in detail."},
                {"h2": "Tools and Setup", "content": "NEURO COMMENTING provides all the tools needed for this task. The platform includes AI commenting in 10 styles, a global Channel Map of 100K+ channels, 7-phase account warmup, and a Content Factory for 6 platforms."},
                {"h2": "Tips and Best Practices", "content": "Start small and scale gradually. Use A/B testing for commenting styles. Always warm up accounts before active use. Monitor analytics and adjust your strategy accordingly."},
            ]

    # FAQ
    faq: List[Dict[str, str]] = []
    if isinstance(lang_data, dict) and lang_data.get("faq"):
        faq = lang_data["faq"]
    elif action_data.get("en", {}).get("faq"):
        faq = action_data["en"]["faq"]
    if not faq:
        faq = _get_default_faq(lang)

    cta_text = "Начать бесплатный пробный период" if lang == "ru" else "Start your free 14-day trial"

    all_text = intro + " " + " ".join(s.get("content", "") for s in sections)
    word_count = _count_words(all_text)

    name = h1 or slug.replace("-", " ").title()
    breadcrumbs = _build_breadcrumbs("how_to", name, canonical_path, lang)
    hreflang_urls = _build_hreflang_urls(canonical_path)

    return SEOPage(
        slug=slug,
        template_type="how_to",
        canonical_path=canonical_path,
        title=title,
        description=description,
        h1=h1,
        intro=intro,
        sections=sections,
        faq=faq,
        cta_text=cta_text,
        lang=lang,
        hreflang_urls=hreflang_urls,
        breadcrumbs=breadcrumbs,
        word_count=word_count,
        internal_links=_get_internal_links(lang),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_all_seo_pages(lang: str = DEFAULT_LANGUAGE) -> List[SEOPage]:
    """
    Generate all programmatic SEO pages for a given language.
    Returns a list of SEOPage objects.
    """
    data = _load_seo_data()
    if not data:
        return []

    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE

    pages: List[SEOPage] = []

    # Industry pages
    for key, industry in data.get("industries", {}).items():
        page = _generate_industry_page(key, industry, lang)
        if page:
            pages.append(page)

    # Country pages
    for key, country in data.get("countries", {}).items():
        page = _generate_country_page(key, country, lang)
        if page:
            pages.append(page)

    # Competitor pages
    for key, comp in data.get("competitors", {}).items():
        page = _generate_competitor_page(key, comp, lang)
        if page:
            pages.append(page)

    # How-to pages
    for key, action in data.get("how_to", {}).items():
        page = _generate_howto_page(key, action, lang)
        if page:
            pages.append(page)

    return pages


def get_seo_page_by_path(path: str, lang: str = DEFAULT_LANGUAGE) -> Optional[SEOPage]:
    """
    Look up a specific SEO page by its canonical path.
    Example: get_seo_page_by_path("/telegram-marketing-for-ecommerce", "ru")
    """
    # Normalize
    path = path.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path

    for page in get_all_seo_pages(lang):
        if page.canonical_path == path:
            return page
    return None


def get_seo_sitemap_entries() -> List[Dict[str, str]]:
    """
    Return sitemap entries for all SEO pages (default language only).
    Each entry: {"path": "/telegram-marketing-for-ecommerce", "priority": "0.6", "changefreq": "monthly"}
    """
    pages = get_all_seo_pages(DEFAULT_LANGUAGE)
    entries: List[Dict[str, str]] = []

    priority_map = {
        "industry": "0.7",
        "country": "0.6",
        "competitor": "0.7",
        "how_to": "0.7",
    }

    for page in pages:
        entries.append({
            "path": page.canonical_path,
            "priority": priority_map.get(page.template_type, "0.6"),
            "changefreq": "monthly",
        })

    return entries


def get_seo_page_schemas(page: SEOPage, base_url: str) -> List[str]:
    """
    Return all JSON-LD schemas for a page as a list of JSON strings.
    """
    schemas: List[str] = []

    # Article schema
    article = _build_article_schema(page, base_url)
    if article:
        schemas.append(article)

    # FAQ schema
    faq = _build_faq_schema(page.faq)
    if faq:
        schemas.append(faq)

    # Breadcrumb schema
    breadcrumbs = _build_breadcrumb_schema(page.breadcrumbs, base_url)
    if breadcrumbs:
        schemas.append(breadcrumbs)

    return schemas


def get_related_seo_pages(page: SEOPage, lang: str, limit: int = 4) -> List[Dict[str, str]]:
    """
    Return related pages for cross-linking.
    Same type first, then mix from other types.
    """
    all_pages = get_all_seo_pages(lang)
    same_type = [p for p in all_pages if p.template_type == page.template_type and p.slug != page.slug]
    other_type = [p for p in all_pages if p.template_type != page.template_type]

    related: List[Dict[str, str]] = []
    for p in same_type[:2]:
        related.append({"title": p.h1, "url": p.canonical_path})
    for p in other_type[:limit - len(related)]:
        related.append({"title": p.h1, "url": p.canonical_path})
    return related[:limit]
