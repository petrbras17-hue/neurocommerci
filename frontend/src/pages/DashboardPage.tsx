import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

type AccountsResponse = {
  items: Array<{ status: string; health_status: string; recommended_next_action: string }>;
  total: number;
};

type ProxiesResponse = {
  items: Array<{ health_status: string }>;
  total: number;
  summary?: Record<string, unknown>;
};

type ContextResponse = {
  brief: {
    completeness_score: number;
    assistant_ready: boolean;
    missing_fields: string[];
    assets_count: number;
    draft_count: number;
    status: string;
  };
};

type CreativeResponse = {
  total: number;
  items: Array<{ status: string }>;
};

export function DashboardPage() {
  const { accessToken, profile } = useAuth();
  const [accounts, setAccounts] = useState<AccountsResponse | null>(null);
  const [proxies, setProxies] = useState<ProxiesResponse | null>(null);
  const [context, setContext] = useState<ContextResponse | null>(null);
  const [creative, setCreative] = useState<CreativeResponse | null>(null);

  useEffect(() => {
    if (!accessToken) {
      return;
    }
    void Promise.all([
      apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken }),
      apiFetch<ProxiesResponse>("/v1/web/proxies/available", { accessToken }),
      apiFetch<ContextResponse>("/v1/context", { accessToken }),
      apiFetch<CreativeResponse>("/v1/creative/drafts", { accessToken }),
    ])
      .then(([accountsPayload, proxiesPayload, contextPayload, creativePayload]) => {
        setAccounts(accountsPayload);
        setProxies(proxiesPayload);
        setContext(contextPayload);
        setCreative(creativePayload);
      })
      .catch(() => {
        setAccounts(null);
        setProxies(null);
        setContext(null);
        setCreative(null);
      });
  }, [accessToken]);

  const metrics = useMemo(() => {
    const accountItems = accounts?.items || [];
    const proxyItems = proxies?.items || [];
    const aliveAccounts = accountItems.filter((item) => item.status === "active" && item.health_status === "alive").length;
    const attentionAccounts = accountItems.filter((item) => item.health_status !== "alive").length;
    const healthyProxies = proxyItems.filter((item) => item.health_status === "alive").length;
    const approvedDrafts = (creative?.items || []).filter((item) => item.status === "approved").length;
    return {
      accountsTotal: accountItems.length,
      aliveAccounts,
      attentionAccounts,
      healthyProxies,
      proxiesTotal: proxyItems.length,
      nextStep: String(profile?.onboarding?.next_step || "upload_account"),
      briefScore: Math.round(Number(context?.brief?.completeness_score || 0) * 100),
      assistantReady: Boolean(context?.brief?.assistant_ready),
      draftCount: creative?.total || 0,
      approvedDrafts,
      approvedAssets: Number(context?.brief?.assets_count || 0),
    };
  }, [accounts, proxies, context, creative, profile]);

  return (
    <div className="page-grid">
      <section className="hero-panel">
        <div className="eyebrow">Operator-first workspace</div>
        <h1>Управляйте growth-процессом без хаоса и без боевых действий по аккаунтам</h1>
        <p>
          Этот кабинет помогает оператору видеть состояние аккаунтов, следующий безопасный шаг, готовность бизнес-контекста и
          AI-черновиков. Пока мы тестируем только безопасный shell без реального execution path.
        </p>
      </section>

      <section className="card-grid">
        <article className="stat-card">
          <span>Аккаунтов всего</span>
          <strong>{metrics.accountsTotal}</strong>
        </article>
        <article className="stat-card">
          <span>Готовы к аудиту</span>
          <strong>{metrics.aliveAccounts}</strong>
        </article>
        <article className="stat-card">
          <span>Требуют внимания</span>
          <strong>{metrics.attentionAccounts}</strong>
        </article>
        <article className="stat-card">
          <span>Следующий шаг</span>
          <strong>{metrics.nextStep}</strong>
        </article>
      </section>

      <section className="card-grid">
        <article className="stat-card">
          <span>Живых прокси</span>
          <strong>
            {metrics.healthyProxies}/{metrics.proxiesTotal}
          </strong>
        </article>
        <article className="stat-card">
          <span>Готовность брифа</span>
          <strong>{metrics.briefScore}%</strong>
        </article>
        <article className="stat-card">
          <span>Черновиков</span>
          <strong>{metrics.draftCount}</strong>
        </article>
        <article className="stat-card">
          <span>Подтверждённых assets</span>
          <strong>{metrics.approvedAssets}</strong>
        </article>
      </section>

      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Onboarding</div>
              <h2>Что происходит сейчас</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Текущий onboarding state: {String(profile?.onboarding?.next_step || "upload_account")}</li>
            <li>Бизнес-контекст {metrics.assistantReady ? "достаточно собран" : "ещё неполный"}.</li>
            <li>Безопасный путь сейчас: загрузка pair, привязка прокси, аудит, заметки, brief и drafts.</li>
          </ul>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Brief readiness</div>
              <h2>Что ещё нужно ассистенту</h2>
            </div>
          </div>
          <ul className="bullet-list">
            {(context?.brief?.missing_fields || []).length ? (
              (context?.brief?.missing_fields || []).map((field) => <li key={field}>{field}</li>)
            ) : (
              <li>Бриф выглядит полным, можно подтверждать контекст и переходить к креативу.</li>
            )}
          </ul>
        </article>
      </section>

      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Что делает система</div>
              <h2>Автоматически</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Хранит tenant, workspace и историю действий.</li>
            <li>Показывает состояние аккаунтов, прокси и brief-ready статусы.</li>
            <li>Готовит AI-черновики и recommendations, но не делает Telegram-side шаги молча.</li>
          </ul>
        </article>

        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Что делает оператор</div>
              <h2>Вручную и осознанно</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Загружает pair `.session + .json` и проверяет видимость прокси.</li>
            <li>Смотрит audit, оставляет заметки и ведёт историю аккаунта.</li>
            <li>Подтверждает бизнес-контекст и approved creative assets.</li>
          </ul>
        </article>
      </section>
    </div>
  );
}
