import { useEffect, useState } from "react";
import {
  billingApi,
  BillingSubscriptionResponse,
  PaymentRecord,
  PlanInfo,
} from "../api";
import { useAuth } from "../auth";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatPrice(kopeks: number | null, currency = "RUB"): string {
  if (!kopeks || kopeks === 0) return "По запросу";
  if (currency === "RUB") return `${Math.round(kopeks / 100).toLocaleString("ru-RU")} ₽/мес`;
  return `$${Math.round(kopeks / 100).toLocaleString("en-US")}/мес`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function daysUntil(iso: string | null): number {
  if (!iso) return 0;
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000));
}

function usagePercent(used: number, limit: number): number {
  if (limit <= 0 || limit >= 999999) return 0;
  return Math.min(100, Math.round((used / limit) * 100));
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const PLAN_ACCENTS: Record<string, string> = {
  starter: "#0088cc",
  growth: "#00cc66",
  pro: "#9b59b6",
  agency: "#e67e22",
  enterprise: "#c0392b",
  trial: "#7f8c8d",
};

const STATUS_MAP: Record<string, { label: string; color: string; bg: string }> = {
  active: { label: "Активна", color: "#22c55e", bg: "rgba(34,197,94,0.15)" },
  trialing: { label: "Пробный период", color: "#eab308", bg: "rgba(234,179,8,0.15)" },
  cancelled: { label: "Отменена", color: "#ef4444", bg: "rgba(239,68,68,0.15)" },
  past_due: { label: "Просрочена", color: "#f97316", bg: "rgba(249,115,22,0.15)" },
  expired: { label: "Истекла", color: "#6b7280", bg: "rgba(107,114,128,0.15)" },
};

function StatusBadge({ status }: { status: string }) {
  const info = STATUS_MAP[status] || { label: status, color: "#888", bg: "rgba(136,136,136,0.15)" };
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

function UsageBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = usagePercent(used, limit);
  const unlimited = limit >= 999999;
  const color = pct >= 90 ? "#ef4444" : pct >= 75 ? "#f97316" : "#00ff88";
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#aaa", marginBottom: 4 }}>
        <span>{label}</span>
        <span style={{ color: "#ddd" }}>
          {used} / {unlimited ? "∞" : limit}
          {!unlimited && <span style={{ marginLeft: 6, color: pct >= 80 ? color : "#666" }}>{pct}%</span>}
        </span>
      </div>
      {!unlimited && (
        <div style={{ height: 4, borderRadius: 4, background: "#2a2a2e" }}>
          <div style={{
            height: 4,
            borderRadius: 4,
            width: `${pct}%`,
            background: color,
            transition: "width 0.4s",
          }} />
        </div>
      )}
    </div>
  );
}

interface PlanCardProps {
  plan: PlanInfo;
  isActive: boolean;
  onSelect: (plan: PlanInfo) => void;
  loading: boolean;
}

function PlanCard({ plan, isActive, onSelect, loading }: PlanCardProps) {
  const color = PLAN_ACCENTS[plan.slug] || "#0088cc";
  const priceDisplay = formatPrice(plan.price_rub ?? plan.price_monthly_rub);
  const isEnterprise = plan.slug === "enterprise";

  return (
    <div style={{
      background: isActive ? "#1a1a2e" : "#111114",
      border: `1px solid ${isActive ? color : "#2a2a2e"}`,
      borderRadius: 14,
      padding: 24,
      display: "flex",
      flexDirection: "column",
      position: "relative",
      transition: "border-color 0.2s, transform 0.1s",
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

      <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 4, color }}>
        {plan.display_name || plan.name}
      </h3>
      <div style={{ fontSize: 22, fontWeight: 700, marginBottom: 6, color: isEnterprise ? "#888" : "#fff" }}>
        {isEnterprise ? "По запросу" : priceDisplay}
      </div>
      {plan.ai_tier && plan.ai_tier !== "worker" && (
        <div style={{ fontSize: 11, color: "#888", marginBottom: 12 }}>
          AI-уровень: <span style={{ color, fontWeight: 600 }}>{plan.ai_tier}</span>
        </div>
      )}

      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6, marginBottom: 20, fontSize: 13 }}>
        {[
          { key: "comments_per_day", label: "Комментариев/день" },
          { key: "max_accounts", label: "Аккаунтов" },
          { key: "max_channels", label: "Каналов" },
          { key: "max_farms", label: "Ферм" },
          { key: "max_campaigns", label: "Кампаний" },
        ].map(({ key, label }) => {
          const val = plan[key as keyof PlanInfo] as number | null;
          if (val === null || val === undefined) return null;
          const display = val >= 999999 ? "∞" : String(val);
          return (
            <div key={key} style={{ display: "flex", justifyContent: "space-between", color: "#aaa" }}>
              <span>{label}</span>
              <span style={{ fontWeight: 600, color: "#ddd" }}>{display}</span>
            </div>
          );
        })}
      </div>

      {isEnterprise ? (
        <a
          href="mailto:sales@neurocommenting.com"
          style={{
            display: "block",
            width: "100%",
            padding: "10px 0",
            borderRadius: 8,
            border: `1px solid ${color}`,
            background: "transparent",
            color,
            fontWeight: 600,
            fontSize: 14,
            cursor: "pointer",
            textAlign: "center",
            textDecoration: "none",
          }}
        >
          Связаться с продажами
        </a>
      ) : (
        <button
          disabled={isActive || loading}
          onClick={() => onSelect(plan)}
          style={{
            width: "100%",
            padding: "10px 0",
            borderRadius: 8,
            border: "none",
            background: isActive ? "#2a2a2e" : color,
            color: isActive ? "#888" : "#fff",
            fontWeight: 600,
            fontSize: 14,
            cursor: isActive || loading ? "default" : "pointer",
            opacity: loading ? 0.6 : 1,
          }}
        >
          {isActive ? "Текущий тариф" : "Выбрать"}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function BillingPage() {
  const auth = useAuth();
  const [billing, setBilling] = useState<BillingSubscriptionResponse | null>(null);
  const [plans, setPlans] = useState<PlanInfo[]>([]);
  const [payments, setPayments] = useState<PaymentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");
  const [cancelModal, setCancelModal] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  const token = auth.accessToken;

  const load = async () => {
    if (!token) return;
    try {
      const [plansRes, subRes, paymentsRes] = await Promise.all([
        billingApi.plans(),
        billingApi.subscription(token),
        billingApi.payments(token, 10),
      ]);
      setPlans(plansRes.items);
      setBilling(subRes);
      setPayments(paymentsRes.items);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [token]);

  const handleActivateTrial = async () => {
    if (!token) return;
    setActionLoading(true);
    try {
      await billingApi.activateTrial(token);
      setSuccessMsg("Пробный период активирован!");
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка активации");
    } finally {
      setActionLoading(false);
    }
  };

  const handleSelectPlan = async (plan: PlanInfo) => {
    if (!token) return;
    setActionLoading(true);
    try {
      const res = await billingApi.subscribe(token, plan.slug);
      if (res.payment_url) {
        window.location.href = res.payment_url;
      } else {
        setSuccessMsg(`Тариф "${plan.display_name || plan.name}" активирован!`);
        await load();
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка смены тарифа");
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!token) return;
    setActionLoading(true);
    try {
      await billingApi.cancel(token, cancelReason || undefined);
      setCancelModal(false);
      setSuccessMsg("Подписка отменена. Доступ сохраняется до конца периода.");
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка отмены");
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return <div style={{ padding: 32, textAlign: "center", color: "#888" }}>Загрузка биллинга…</div>;
  }

  const sub = billing?.subscription;
  const currentPlan = billing?.plan;
  const usage = billing?.usage;
  const limits = billing?.limits;
  const activePlanSlug = currentPlan?.slug;
  const isTrial = sub?.status === "trialing";
  const trialDaysLeft = isTrial && sub?.trial_ends_at ? daysUntil(sub.trial_ends_at) : 0;
  const atLimitWarning = usage && limits && (
    usagePercent(usage.max_accounts, limits.max_accounts) >= 80 ||
    usagePercent(usage.comments_per_day, limits.comments_per_day) >= 80
  );

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1140, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 8, color: "#fff" }}>Биллинг</h1>
      <p style={{ color: "#888", marginBottom: 24, fontSize: 14 }}>
        Управление тарифом и платежами. Интеграция Stripe и ЮKassa — в следующем релизе.
      </p>

      {error && (
        <div style={{
          background: "rgba(239,68,68,0.12)",
          border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: 8,
          padding: "10px 16px",
          marginBottom: 16,
          color: "#ef4444",
          fontSize: 14,
          display: "flex",
          justifyContent: "space-between",
        }}>
          {error}
          <button onClick={() => setError("")} style={{ background: "none", border: "none", color: "#ef4444", cursor: "pointer" }}>x</button>
        </div>
      )}
      {successMsg && (
        <div style={{
          background: "rgba(0,255,136,0.1)",
          border: "1px solid rgba(0,255,136,0.3)",
          borderRadius: 8,
          padding: "10px 16px",
          marginBottom: 16,
          color: "#00ff88",
          fontSize: 14,
          display: "flex",
          justifyContent: "space-between",
        }}>
          {successMsg}
          <button onClick={() => setSuccessMsg("")} style={{ background: "none", border: "none", color: "#00ff88", cursor: "pointer" }}>x</button>
        </div>
      )}

      {/* Limit warning banner */}
      {atLimitWarning && (
        <div style={{
          background: "rgba(249,115,22,0.12)",
          border: "1px solid rgba(249,115,22,0.35)",
          borderRadius: 10,
          padding: "12px 20px",
          marginBottom: 20,
          fontSize: 14,
          color: "#f97316",
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}>
          <span style={{ fontSize: 20 }}>!</span>
          <span>
            Вы приближаетесь к лимитам текущего тарифа. Рассмотрите обновление плана для продолжения работы без ограничений.
          </span>
        </div>
      )}

      {/* Trial countdown */}
      {isTrial && trialDaysLeft > 0 && (
        <div style={{
          background: "rgba(234,179,8,0.1)",
          border: "1px solid rgba(234,179,8,0.3)",
          borderRadius: 10,
          padding: "12px 20px",
          marginBottom: 20,
          fontSize: 14,
          color: "#eab308",
        }}>
          Пробный период истекает через <strong>{trialDaysLeft} дн.</strong> ({formatDate(sub?.trial_ends_at ?? null)}).
          Выберите тариф для продолжения работы.
        </div>
      )}
      {isTrial && trialDaysLeft === 0 && (
        <div style={{
          background: "rgba(239,68,68,0.1)",
          border: "1px solid rgba(239,68,68,0.3)",
          borderRadius: 10,
          padding: "12px 20px",
          marginBottom: 20,
          fontSize: 14,
          color: "#ef4444",
        }}>
          Пробный период истёк. Выберите тариф для восстановления доступа.
        </div>
      )}

      {/* Current subscription card */}
      {sub ? (
        <div style={{
          background: "#111114",
          border: "1px solid #2a2a2e",
          borderRadius: 12,
          padding: "20px 24px",
          marginBottom: 28,
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, marginBottom: 20 }}>
            <div>
              <span style={{ fontSize: 13, color: "#888" }}>Текущий тариф: </span>
              <span style={{ fontWeight: 700, fontSize: 16, color: PLAN_ACCENTS[activePlanSlug || ""] || "#fff" }}>
                {currentPlan?.display_name || currentPlan?.name || "Не выбран"}
              </span>
              <span style={{ marginLeft: 12, fontSize: 13, color: "#888" }}>
                до {formatDate(sub.current_period_end)}
              </span>
            </div>
            <StatusBadge status={sub.status} />
          </div>

          {/* Usage bars */}
          {usage && limits && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "8px 24px" }}>
              <UsageBar label="Комментариев сегодня" used={usage.comments_per_day} limit={limits.comments_per_day || 100} />
              <UsageBar label="Аккаунтов" used={usage.max_accounts} limit={limits.max_accounts || 5} />
              <UsageBar label="Каналов" used={usage.max_channels} limit={limits.max_channels || 20} />
              <UsageBar label="Ферм" used={usage.max_farms} limit={limits.max_farms || 5} />
            </div>
          )}
        </div>
      ) : (
        <div style={{
          background: "#111114",
          border: "1px solid #2a2a2e",
          borderRadius: 12,
          padding: "20px 24px",
          marginBottom: 28,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 12,
        }}>
          <span style={{ color: "#888", fontSize: 14 }}>Нет активной подписки</span>
          <button
            onClick={handleActivateTrial}
            disabled={actionLoading}
            style={{
              padding: "10px 24px",
              borderRadius: 8,
              border: "none",
              background: "#00ff88",
              color: "#000",
              fontWeight: 700,
              fontSize: 14,
              cursor: "pointer",
              opacity: actionLoading ? 0.6 : 1,
            }}
          >
            Активировать пробный период
          </button>
        </div>
      )}

      {/* Plans grid */}
      <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 16, color: "#ccc" }}>Тарифные планы</h2>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
        gap: 16,
        marginBottom: 36,
      }}>
        {plans.map((plan) => (
          <PlanCard
            key={plan.id}
            plan={plan}
            isActive={plan.slug === activePlanSlug}
            onSelect={handleSelectPlan}
            loading={actionLoading}
          />
        ))}
      </div>

      {/* Payment history */}
      {payments.length > 0 && (
        <>
          <h2 style={{ fontSize: 16, fontWeight: 700, marginBottom: 16, color: "#ccc" }}>История платежей</h2>
          <div style={{
            background: "#111114",
            border: "1px solid #2a2a2e",
            borderRadius: 12,
            overflow: "hidden",
            marginBottom: 28,
          }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #2a2a2e" }}>
                  {["Дата", "Поставщик", "Сумма", "Валюта", "Статус"].map((h) => (
                    <th key={h} style={{ padding: "10px 16px", textAlign: "left", color: "#666", fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {payments.map((p) => (
                  <tr key={p.id} style={{ borderBottom: "1px solid #1e1e22" }}>
                    <td style={{ padding: "10px 16px", color: "#aaa" }}>{formatDate(p.created_at)}</td>
                    <td style={{ padding: "10px 16px", color: "#ccc" }}>{p.payment_provider}</td>
                    <td style={{ padding: "10px 16px", color: "#ccc" }}>{(p.amount / 100).toFixed(2)}</td>
                    <td style={{ padding: "10px 16px", color: "#888" }}>{p.currency}</td>
                    <td style={{ padding: "10px 16px" }}>
                      <span style={{
                        color: p.status === "succeeded" ? "#22c55e" : p.status === "failed" ? "#ef4444" : "#888",
                        fontWeight: 600,
                      }}>
                        {p.status === "succeeded" ? "Успешно" : p.status === "failed" ? "Ошибка" : p.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Cancel subscription */}
      {sub && sub.status === "active" && (
        <div style={{
          background: "#111114",
          border: "1px solid #2a2a2e",
          borderRadius: 12,
          padding: "16px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 12,
        }}>
          <div>
            <div style={{ fontWeight: 600, color: "#ccc", fontSize: 14 }}>Отменить подписку</div>
            <div style={{ color: "#666", fontSize: 12, marginTop: 2 }}>
              Доступ сохраняется до {formatDate(sub.current_period_end)}
            </div>
          </div>
          <button
            onClick={() => setCancelModal(true)}
            style={{
              padding: "8px 20px",
              borderRadius: 8,
              border: "1px solid #ef4444",
              background: "transparent",
              color: "#ef4444",
              fontWeight: 600,
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            Отменить подписку
          </button>
        </div>
      )}

      {/* Cancel modal */}
      {cancelModal && (
        <div style={{
          position: "fixed",
          inset: 0,
          background: "rgba(0,0,0,0.75)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 1000,
        }}>
          <div style={{
            background: "#18181b",
            border: "1px solid #3a3a3e",
            borderRadius: 14,
            padding: 28,
            maxWidth: 420,
            width: "100%",
          }}>
            <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 12, color: "#fff" }}>Отменить подписку?</h3>
            <p style={{ color: "#888", fontSize: 14, marginBottom: 16 }}>
              Доступ сохранится до {formatDate(sub?.current_period_end ?? null)}. Вы можете возобновить подписку в любое время.
            </p>
            <textarea
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              placeholder="Причина отмены (необязательно)…"
              rows={3}
              style={{
                width: "100%",
                background: "#111",
                border: "1px solid #333",
                borderRadius: 8,
                padding: "8px 12px",
                color: "#ccc",
                fontSize: 13,
                resize: "vertical",
                marginBottom: 16,
                boxSizing: "border-box",
              }}
            />
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button
                onClick={() => setCancelModal(false)}
                style={{
                  padding: "8px 20px",
                  borderRadius: 8,
                  border: "1px solid #444",
                  background: "transparent",
                  color: "#888",
                  cursor: "pointer",
                }}
              >
                Назад
              </button>
              <button
                onClick={handleCancel}
                disabled={actionLoading}
                style={{
                  padding: "8px 20px",
                  borderRadius: 8,
                  border: "none",
                  background: "#ef4444",
                  color: "#fff",
                  fontWeight: 700,
                  cursor: "pointer",
                  opacity: actionLoading ? 0.6 : 1,
                }}
              >
                Отменить подписку
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Payment integration notice */}
      <div style={{
        marginTop: 28,
        padding: "14px 20px",
        background: "#111114",
        border: "1px solid #2a2a2e",
        borderRadius: 10,
        color: "#666",
        fontSize: 12,
        lineHeight: 1.7,
      }}>
        <strong style={{ color: "#888" }}>Подключение онлайн-оплаты</strong>
        <br />
        Stripe и ЮKassa готовы к подключению. Укажите
        {" "}<code style={{ color: "#999" }}>STRIPE_SECRET_KEY</code>,
        {" "}<code style={{ color: "#999" }}>STRIPE_WEBHOOK_SECRET</code>,
        {" "}<code style={{ color: "#999" }}>YOOKASSA_SHOP_ID</code>,
        {" "}<code style={{ color: "#999" }}>YOOKASSA_SECRET_KEY</code> в
        {" "}<code style={{ color: "#999" }}>.env</code> для активации.
      </div>
    </div>
  );
}
