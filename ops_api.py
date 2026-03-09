from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

import jwt
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.lead_funnel import LeadSnapshot, deliver_lead_funnel
from storage.models import Account, Lead, Tenant, Workspace
from storage.sqlite_db import apply_session_rls_context, async_session, dispose_engine, init_db
from utils.helpers import utcnow

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


class LeadCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    company: str = Field(min_length=1, max_length=255)
    telegram_username: Optional[str] = Field(default=None, max_length=255)
    use_case: str = Field(min_length=1, max_length=64)
    utm_source: Optional[str] = Field(default=None, max_length=255)

    @field_validator("name", "email", "company", "use_case", mode="before")
    @classmethod
    def strip_required_strings(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("invalid_email")
        return value.lower()

    @field_validator("telegram_username")
    @classmethod
    def normalize_username(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.startswith("@"):
            normalized = normalized[1:]
        return normalized


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MARKETING_PAGES = {
    "home": {
        "path": "/",
        "slug": "home",
        "title": "Telegram Growth OS для брендов — автоматизируй рост в Telegram",
        "description": "NEURO COMMENTING помогает growth-командам находить релевантные Telegram-каналы, запускать кампании и собирать лиды в одном premium workflow.",
        "headline": "Telegram Growth OS для брендов — автоматизируй рост в Telegram",
        "subheadline": "Discovery, кампании, AI-черновики и аналитика в одном рабочем контуре для growth-команды.",
        "bullets": [
            "Находите каналы и сообщества, где уже есть спрос на ваш продукт.",
            "Переходите от discovery к кампании без ручной путаницы между таблицами и чатами.",
            "Получайте AI-черновики, approvals и growth-аналитику в одном месте.",
        ],
        "segment_title": "Для growth-команд, которые хотят видеть Telegram как полноценный revenue-канал",
        "segment_points": [
            "Быстрое исследование ниш и конкурентных площадок.",
            "Кампании и направления без потери контроля и контекста.",
            "От lead capture до операционного follow-up внутри одного процесса.",
        ],
    },
    "ecom": {
        "path": "/ecom",
        "slug": "ecom",
        "title": "Telegram Growth OS для интернет-магазинов",
        "description": "Для ecom-команд: поиск нишевых Telegram-каналов, кампании, подборки и AI-assisted growth workflow.",
        "headline": "Рост интернет-магазина в Telegram без хаоса",
        "subheadline": "Для брендов, которые хотят собирать спрос, находить площадки и быстро запускать Telegram-кампании.",
        "bullets": [
            "Находите каналы и чаты, где уже обсуждают товары, скидки и тематические подборки.",
            "Собирайте кампании под сезонные акции, новые линейки и товарные дропы.",
            "Держите площадки, черновики и follow-up в одном кабинете.",
        ],
        "segment_title": "Что особенно важно для ecom",
        "segment_points": [
            "Товарный спрос и каталоги по нишам.",
            "Сезонные предложения, промо и подборки.",
            "Ускоренный путь от discovery до первых лидов.",
        ],
    },
    "edtech": {
        "path": "/edtech",
        "slug": "edtech",
        "title": "Telegram Growth OS для онлайн-школ",
        "description": "Для edtech-команд: Telegram discovery, AI-черновики, кампании и аналитика для воронок набора.",
        "headline": "Telegram-рост для онлайн-школ и образовательных продуктов",
        "subheadline": "От исследования ниши до запуска кампаний под наборы и вебинары — без потери контекста между маркетингом и операционкой.",
        "bullets": [
            "Находите площадки, где обсуждают карьеру, навыки, переобучение и апгрейд профессии.",
            "Планируйте кампании под вебинары, наборы и evergreen-продукты.",
            "Получайте AI-черновики и активационную аналитику в одной системе.",
        ],
        "segment_title": "Что особенно важно для edtech",
        "segment_points": [
            "Площадки с аудиторией, готовой к обучению.",
            "Наборы, вебинары и evergreen-воронки в одном pipeline.",
            "Понятный follow-up по лидам и интересу к продукту.",
        ],
    },
    "saas": {
        "path": "/saas",
        "slug": "saas",
        "title": "Telegram Growth OS для SaaS-продуктов",
        "description": "Для SaaS-команд: канальный ресерч, AI-черновики, кампании и операционная аналитика роста в Telegram.",
        "headline": "Telegram Growth OS для SaaS-команд",
        "subheadline": "Сводите discovery, кампании, AI-черновики и аналитику в один управляемый pipeline роста.",
        "bullets": [
            "Ищите комьюнити и каналы, где обсуждают ваш use case, боли и конкурентов.",
            "Разводите спрос по сегментам: SMB, agency, startup, product teams.",
            "Управляйте ростом как продуктовой функцией, а не набором хаотичных действий.",
        ],
        "segment_title": "Что особенно важно для SaaS",
        "segment_points": [
            "B2B discovery по нишам и use cases.",
            "Повторяемые кампании и pipeline approvals.",
            "Прозрачная аналитика по лидам, площадкам и активности.",
        ],
    },
}

TRUST_STRIP = [
    "Discovery",
    "Campaign orchestration",
    "AI drafts",
    "Lead capture",
    "Analytics",
]

PROOF_METRICS = [
    {"value": "24/7", "label": "контроль роста и discovery"},
    {"value": "<48ч", "label": "реакция на новые заявки"},
    {"value": "1 OS", "label": "для parser, drafts и campaigns"},
]

FEATURES = [
    {
        "title": "Discovery Engine",
        "body": "Находите релевантные Telegram-каналы, обсуждения и комьюнити по нишам и сценариям роста.",
    },
    {
        "title": "Campaign Control",
        "body": "Управляйте направлениями, approvals, safety rings и кампанийным pipeline в одном рабочем слое.",
    },
    {
        "title": "AI Draft Studio",
        "body": "Готовьте черновики комментариев, ответов и кампанийного контента без потери tone of voice.",
    },
    {
        "title": "Lead Funnel",
        "body": "Собирайте лиды с лендинга, дублируйте их в Sheets и получайте Telegram-уведомления без ручной рутины.",
    },
    {
        "title": "Analytics Layer",
        "body": "Видите кампании, находки, активность и usage в одном growth dashboard, а не по кускам.",
    },
]

PRICING = [
    {
        "name": "Pro",
        "price": "$499/mo",
        "note": "Для первых growth-команд",
        "featured": False,
        "bullets": [
            "Discovery и parser workflow",
            "AI-черновики и lead capture",
            "Стартовый campaign control",
        ],
    },
    {
        "name": "Scale",
        "price": "$1 499/mo",
        "note": "Для команд, которым нужен repeatable growth",
        "featured": True,
        "bullets": [
            "Несколько workspaces и больше каналов",
            "Управляемые approvals и campaign pipeline",
            "Расширенные usage и activation метрики",
        ],
    },
    {
        "name": "Business",
        "price": "$4 999/mo",
        "note": "Для управляемых команд и агентств",
        "featured": False,
        "bullets": [
            "Высокие лимиты и multi-tenant ops",
            "Глубокий parser + drafts workflow",
            "Подготовка к agency и enterprise motion",
        ],
    },
]

FAQ = [
    ("Что такое Telegram Growth OS?", "Это единая операционная система для discovery, кампаний, AI-черновиков, аналитики и growth-команд в Telegram."),
    ("Для кого продукт?", "Для брендов и growth-команд RU/CIS mid-market, которые используют Telegram как acquisition и community-канал."),
    ("Что я получу после заявки?", "Мы свяжемся с вами, уточним ваш use case и покажем, как построить Telegram growth workflow под ваш продукт."),
    ("Нужна ли отдельная команда разработки?", "Нет, продукт рассчитан на маркетинг и growth-операторов. Технический слой уже встроен в платформу."),
    ("Можно ли начать с пилота?", "Да. Стартовый путь рассчитан на пилоты, onboarding и дальнейший переход на полноценный subscription workflow."),
]


@dataclass
class TenantContext:
    user_id: int
    tenant_id: int
    workspace_id: int | None
    role: str
    token_type: str


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "").strip()
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_bearer_token")
    return header.split(" ", 1)[1].strip()


def _decode_jwt(token: str) -> TenantContext:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_ACCESS_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token") from exc

    token_type = str(payload.get("type") or "")
    if token_type != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_type")

    try:
        user_id = int(payload["sub"])
        tenant_id = int(payload["tenant_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_token_payload") from exc

    workspace_raw = payload.get("workspace_id")
    workspace_id = int(workspace_raw) if workspace_raw is not None else None
    return TenantContext(
        user_id=user_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role=str(payload.get("role") or "member"),
        token_type=token_type,
    )


async def require_internal_token(request: Request) -> None:
    token = _bearer_token(request)
    if token != settings.OPS_API_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_internal_token")


async def get_tenant_context(request: Request) -> TenantContext:
    token = _bearer_token(request)
    tenant_context = _decode_jwt(token)

    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(
                session,
                tenant_id=tenant_context.tenant_id,
                user_id=tenant_context.user_id,
            )
            tenant = await session.get(Tenant, tenant_context.tenant_id)
            if tenant is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="tenant_not_found")
            if tenant.status == "suspended":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tenant_suspended")

    request.state.tenant_context = tenant_context
    return tenant_context


async def tenant_session(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> AsyncIterator[AsyncSession]:
    async with async_session() as session:
        async with session.begin():
            await apply_session_rls_context(
                session,
                tenant_id=tenant_context.tenant_id,
                user_id=tenant_context.user_id,
            )
            yield session


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    try:
        yield
    finally:
        await dispose_engine()


app = FastAPI(title="NEURO COMMENTING Ops API", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _page_context(request: Request, page: dict[str, object]) -> dict[str, object]:
    base_url = str(request.base_url).rstrip("/")
    og_image = f"{base_url}{request.url_for('static', path='og-default.svg')}"
    return {
        "request": request,
        "page": page,
        "trust_strip": TRUST_STRIP,
        "proof_metrics": PROOF_METRICS,
        "features": FEATURES,
        "pricing": PRICING,
        "faq": FAQ,
        "success_message": "Вы в списке — мы напишем вам в ближайшие 48 часов",
        "og_image": og_image,
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "marketing/landing.html", _page_context(request, MARKETING_PAGES["home"]))


@app.get("/ecom", response_class=HTMLResponse)
async def landing_ecom(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "marketing/landing.html", _page_context(request, MARKETING_PAGES["ecom"]))


@app.get("/edtech", response_class=HTMLResponse)
async def landing_edtech(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "marketing/landing.html", _page_context(request, MARKETING_PAGES["edtech"]))


@app.get("/saas", response_class=HTMLResponse)
async def landing_saas(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "marketing/landing.html", _page_context(request, MARKETING_PAGES["saas"]))


@app.post("/api/leads")
async def create_lead(payload: LeadCreatePayload, request: Request) -> JSONResponse:
    utm_source = payload.utm_source or request.query_params.get("utm_source")
    lead_snapshot: Optional[LeadSnapshot] = None
    async with async_session() as session:
        async with session.begin():
            lead = Lead(
                name=payload.name,
                email=payload.email,
                company=payload.company,
                telegram_username=payload.telegram_username,
                use_case=payload.use_case,
                utm_source=utm_source,
            )
            session.add(lead)
            await session.flush()
            lead_snapshot = LeadSnapshot(
                lead_id=int(lead.id or 0),
                name=lead.name,
                email=lead.email,
                company=lead.company,
                telegram_username=lead.telegram_username,
                use_case=lead.use_case,
                utm_source=lead.utm_source,
                created_at=lead.created_at or utcnow(),
            )

    if lead_snapshot is not None:
        await deliver_lead_funnel(lead_snapshot)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "ok": True,
            "message": "Вы в списке — мы напишем вам в ближайшие 48 часов",
            "lead_id": lead_snapshot.lead_id if lead_snapshot else None,
        },
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt() -> str:
    return "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n"


@app.get("/sitemap.xml")
async def sitemap_xml(request: Request) -> Response:
    base_url = str(request.base_url).rstrip("/")
    urls = "\n".join(
        f"  <url><loc>{base_url}{page['path']}</loc></url>"
        for page in MARKETING_PAGES.values()
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/v1/accounts")
async def list_accounts(_: None = Depends(require_internal_token)) -> dict[str, object]:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Account).order_by(Account.id)
            )
        ).scalars().all()

    items = [
        {
            "id": row.id,
            "phone": row.phone,
            "status": row.status,
            "health_status": row.health_status,
            "lifecycle_stage": row.lifecycle_stage,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@app.get("/v1/internal/leads")
async def list_recent_leads(
    limit: int = 25,
    _: None = Depends(require_internal_token),
) -> dict[str, object]:
    safe_limit = max(1, min(int(limit), 100))
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Lead).order_by(Lead.created_at.desc(), Lead.id.desc()).limit(safe_limit)
            )
        ).scalars().all()

    items = [
        {
            "id": row.id,
            "name": row.name,
            "email": row.email,
            "company": row.company,
            "telegram_username": row.telegram_username,
            "use_case": row.use_case,
            "utm_source": row.utm_source,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@app.get("/v1/workspaces")
async def list_workspaces(
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, object]:
    rows = (
        await session.execute(select(Workspace).order_by(Workspace.id))
    ).scalars().all()

    items = [
        {
            "id": row.id,
            "name": row.name,
            "settings": row.settings or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
