import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "../api";
import { useAuth } from "../auth";

type AccountsResponse = {
  items: Array<{ status: string; health_status: string }>;
  total: number;
};

export function DashboardPage() {
  const { accessToken, profile } = useAuth();
  const [accounts, setAccounts] = useState<AccountsResponse | null>(null);

  useEffect(() => {
    if (!accessToken) {
      return;
    }
    void apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken }).then(setAccounts).catch(() => setAccounts(null));
  }, [accessToken]);

  const metrics = useMemo(() => {
    const items = accounts?.items || [];
    const alive = items.filter((item) => item.status === "active" && item.health_status === "alive").length;
    const warnings = items.filter((item) => item.health_status !== "alive").length;
    return {
      accountsTotal: items.length,
      accountsAlive: alive,
      accountsWarnings: warnings,
      nextStep: String(profile?.onboarding?.next_step || "upload_account")
    };
  }, [accounts, profile]);

  return (
    <div className="page-grid">
      <section className="hero-panel">
        <div className="eyebrow">Workspace overview</div>
        <h1>Добро пожаловать в web workspace</h1>
        <p>
          Sprint 3 переводит NEURO COMMENTING в Telegram-first SaaS shell: auth, tenant, onboarding и account audit в
          одном кабинете.
        </p>
      </section>

      <section className="card-grid">
        <article className="stat-card">
          <span>Аккаунтов всего</span>
          <strong>{metrics.accountsTotal}</strong>
        </article>
        <article className="stat-card">
          <span>Готовы к работе</span>
          <strong>{metrics.accountsAlive}</strong>
        </article>
        <article className="stat-card">
          <span>Требуют внимания</span>
          <strong>{metrics.accountsWarnings}</strong>
        </article>
        <article className="stat-card">
          <span>Следующий шаг</span>
          <strong>{metrics.nextStep}</strong>
        </article>
      </section>
    </div>
  );
}
