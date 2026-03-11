import { useEffect, useState } from "react";
import { billingApi, PlanInfo, SubscriptionInfo } from "../api";
import { useAuth } from "../auth";

const PLAN_ICONS: Record<string, string> = {
  trial: "🎯",
  starter: "🚀",
  growth: "📈",
  enterprise: "🏢",
};

const PLAN_COLORS: Record<string, string> = {
  trial: "#888",
  starter: "#0088cc",
  growth: "#00cc66",
  enterprise: "#cc8800",
};

function formatPrice(rub: number | null): string {
  if (!rub || rub === 0) return "Бесплатно";
  return `${rub.toLocaleString("ru-RU")} ₽/мес`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

const LIMIT_LABELS: Record<string, string> = {
  max_accounts: "Аккаунтов",
  max_channels: "Каналов",
  max_comments_per_day: "Комментариев / день",
  max_campaigns: "Кампаний",
};

export function BillingPage() {
  const auth = useAuth();
  const [plans, setPlans] = useState<PlanInfo[]>([]);
  const [sub, setSub] = useState<SubscriptionInfo | null>(null);
  const [currentPlan, setCurrentPlan] = useState<PlanInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!auth.accessToken) return;
    let cancelled = false;
    (async () => {
      try {
        const [plansRes, subRes] = await Promise.all([
          billingApi.plans(),
          billingApi.subscription(auth.accessToken!),
        ]);
        if (cancelled) return;
        setPlans(plansRes.items);
        setSub(subRes.subscription);
        setCurrentPlan(subRes.plan);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Ошибка загрузки");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [auth.accessToken]);

  if (loading) {
    return <div style={{ padding: 32, textAlign: "center", color: "#888" }}>Загрузка тарифов…</div>;
  }
  if (error) {
    return <div style={{ padding: 32, textAlign: "center", color: "#ef4444" }}>{error}</div>;
  }

  const activePlanSlug = currentPlan?.slug;
  const isTrial = sub?.status === "trialing";
  const trialEndsAt = sub?.trial_ends_at;

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1100, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>Биллинг</h1>
      <p style={{ color: "#888", marginBottom: 24, fontSize: 14 }}>
        Выберите тариф, который подходит вашему бизнесу. Оплата будет доступна после подключения платёжных систем.
      </p>

      {/* Current subscription banner */}
      {sub && (
        <div style={{
          background: "#1a1a2e",
          border: "1px solid #2a2a3e",
          borderRadius: 12,
          padding: "16px 24px",
          marginBottom: 28,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 12,
        }}>
          <div>
            <span style={{ fontSize: 13, color: "#888" }}>Текущий тариф: </span>
            <span style={{ fontWeight: 600, color: PLAN_COLORS[activePlanSlug || ""] || "#fff" }}>
              {currentPlan?.name || "Не выбран"}
            </span>
            {isTrial && trialEndsAt && (
              <span style={{ marginLeft: 12, fontSize: 12, color: "#eab308" }}>
                Пробный период до {formatDate(trialEndsAt)}
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <StatusBadge status={sub.status} />
          </div>
        </div>
      )}

      {/* Plans grid */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
        gap: 20,
      }}>
        {plans.map((plan) => {
          const isActive = plan.slug === activePlanSlug;
          const color = PLAN_COLORS[plan.slug] || "#0088cc";
          return (
            <div key={plan.id} style={{
              background: isActive ? "#1a1a2e" : "#141418",
              border: `1px solid ${isActive ? color : "#2a2a2e"}`,
              borderRadius: 14,
              padding: 24,
              display: "flex",
              flexDirection: "column",
              position: "relative",
              transition: "border-color 0.2s",
            }}>
              {isActive && (
                <div style={{
                  position: "absolute",
                  top: -1,
                  left: 20,
                  right: 20,
                  height: 3,
                  background: color,
                  borderRadius: "0 0 4px 4px",
                }} />
              )}
              <div style={{ fontSize: 28, marginBottom: 8 }}>{PLAN_ICONS[plan.slug] || "📦"}</div>
              <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 4, color }}>{plan.name}</h3>
              <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 16 }}>
                {formatPrice(plan.price_monthly_rub)}
              </div>

              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8, marginBottom: 20 }}>
                {(["max_accounts", "max_channels", "max_comments_per_day", "max_campaigns"] as const).map((key) => {
                  const val = plan[key as keyof PlanInfo];
                  if (val === null || val === undefined) return null;
                  return (
                    <div key={key} style={{ display: "flex", justifyContent: "space-between", fontSize: 13, color: "#aaa" }}>
                      <span>{LIMIT_LABELS[key]}</span>
                      <span style={{ fontWeight: 600, color: "#ddd" }}>{val === -1 ? "∞" : String(val)}</span>
                    </div>
                  );
                })}
              </div>

              <button
                disabled={isActive}
                style={{
                  width: "100%",
                  padding: "10px 0",
                  borderRadius: 8,
                  border: "none",
                  background: isActive ? "#2a2a2e" : color,
                  color: isActive ? "#888" : "#fff",
                  fontWeight: 600,
                  fontSize: 14,
                  cursor: isActive ? "default" : "pointer",
                  opacity: isActive ? 0.7 : 1,
                }}
              >
                {isActive ? "Текущий тариф" : "Выбрать"}
              </button>
            </div>
          );
        })}
      </div>

      {/* Payment integration notice */}
      <div style={{
        marginTop: 32,
        padding: "16px 24px",
        background: "#1a1a1e",
        border: "1px solid #2a2a2e",
        borderRadius: 10,
        color: "#888",
        fontSize: 13,
        lineHeight: 1.6,
      }}>
        <strong style={{ color: "#aaa" }}>Подключение оплаты</strong>
        <br />
        Для активации онлайн-оплаты будут интегрированы Stripe и ЮKassa. Сейчас смена тарифа доступна по запросу через поддержку.
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; color: string; bg: string }> = {
    active: { label: "Активна", color: "#22c55e", bg: "rgba(34,197,94,0.15)" },
    trialing: { label: "Пробный период", color: "#eab308", bg: "rgba(234,179,8,0.15)" },
    cancelled: { label: "Отменена", color: "#ef4444", bg: "rgba(239,68,68,0.15)" },
    past_due: { label: "Просрочена", color: "#ef4444", bg: "rgba(239,68,68,0.15)" },
  };
  const info = map[status] || { label: status, color: "#888", bg: "rgba(136,136,136,0.15)" };
  return (
    <span style={{
      padding: "4px 12px",
      borderRadius: 999,
      fontSize: 12,
      fontWeight: 600,
      color: info.color,
      background: info.bg,
    }}>
      {info.label}
    </span>
  );
}
