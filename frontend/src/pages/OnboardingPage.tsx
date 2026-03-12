import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  Globe,
  Brain,
  Rocket,
  CheckCircle,
  ChevronRight,
  RefreshCw,
  ArrowLeft,
  Zap,
  Target,
  TrendingUp,
  Building2,
} from "lucide-react";
import { productBriefApi, type ProductBrief } from "../api";
import { useAuth } from "../auth";

type Step = 1 | 2 | 3 | 4;

const PLANS = [
  {
    slug: "starter",
    name: "Starter",
    price: "9 900 ₽/мес",
    accounts: 5,
    daily: 50,
    features: ["До 5 аккаунтов", "50 комментариев/день", "AI-ассистент", "Базовая аналитика"],
    icon: Zap,
    color: "var(--info)",
  },
  {
    slug: "growth",
    name: "Growth",
    price: "29 900 ₽/мес",
    accounts: 20,
    daily: 200,
    features: [
      "До 20 аккаунтов",
      "200 комментариев/день",
      "Авто-кампании",
      "Расширенная аналитика",
      "Приоритетная поддержка",
    ],
    icon: TrendingUp,
    color: "var(--accent)",
    featured: true,
  },
  {
    slug: "pro",
    name: "Pro",
    price: "69 900 ₽/мес",
    accounts: 50,
    daily: 500,
    features: [
      "До 50 аккаунтов",
      "500 комментариев/день",
      "Все AI-функции",
      "Выделенная поддержка",
      "API-доступ",
    ],
    icon: Target,
    color: "var(--warning)",
  },
  {
    slug: "agency",
    name: "Agency",
    price: "149 900 ₽/мес",
    accounts: 200,
    daily: 2000,
    features: [
      "До 200 аккаунтов",
      "2000 комментариев/день",
      "Multi-tenant",
      "White-label опции",
      "SLA поддержка",
    ],
    icon: Building2,
    color: "#ff6b9d",
  },
];

const stepVariants = {
  initial: { opacity: 0, x: 40 },
  animate: { opacity: 1, x: 0 },
  exit: { opacity: 0, x: -40 },
};

function StepIndicator({ current, total }: { current: Step; total: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 32 }}>
      {Array.from({ length: total }, (_, i) => i + 1).map((s) => (
        <div key={s} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 12,
              fontWeight: 600,
              background:
                s < current
                  ? "var(--accent)"
                  : s === current
                    ? "rgba(0,255,136,0.2)"
                    : "var(--surface-2)",
              color:
                s < current
                  ? "var(--bg)"
                  : s === current
                    ? "var(--accent)"
                    : "var(--muted)",
              border:
                s === current
                  ? "2px solid var(--accent)"
                  : s < current
                    ? "2px solid var(--accent)"
                    : "2px solid var(--border)",
              transition: "all 0.3s ease",
            }}
          >
            {s < current ? <CheckCircle size={14} /> : s}
          </div>
          {s < total && (
            <div
              style={{
                width: 40,
                height: 2,
                background: s < current ? "var(--accent)" : "var(--border)",
                transition: "background 0.3s ease",
              }}
            />
          )}
        </div>
      ))}
      <span style={{ color: "var(--muted)", fontSize: 12, marginLeft: 8 }}>
        Шаг {current} из {total}
      </span>
    </div>
  );
}

export function OnboardingPage() {
  const { accessToken } = useAuth();
  const navigate = useNavigate();

  const [step, setStep] = useState<Step>(1);
  const [url, setUrl] = useState("");
  const [brief, setBrief] = useState<ProductBrief | null>(null);
  const [editedBrief, setEditedBrief] = useState<Partial<ProductBrief>>({});
  const [selectedPlan, setSelectedPlan] = useState("growth");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [launched, setLaunched] = useState(false);

  const effectiveBrief = brief ? { ...brief, ...editedBrief } : null;

  const handleAnalyze = async () => {
    if (!accessToken || !url.trim()) {
      setError("Введите ссылку на ваш продукт.");
      return;
    }
    const urlTrimmed = url.trim();
    if (!urlTrimmed.startsWith("http://") && !urlTrimmed.startsWith("https://")) {
      setError("Ссылка должна начинаться с https:// или http://");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await productBriefApi.analyze(accessToken, urlTrimmed);
      setBrief(result);
      setEditedBrief({});
      setStep(2);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка анализа. Попробуйте ещё раз.");
    } finally {
      setBusy(false);
    }
  };

  const handleLaunch = async () => {
    if (!accessToken || !brief) return;
    setBusy(true);
    setError("");
    try {
      await productBriefApi.createCampaign(accessToken, brief.id, {
        name: effectiveBrief?.product_name
          ? `Кампания: ${effectiveBrief.product_name}`
          : undefined,
      });
      setLaunched(true);
      setStep(4);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка запуска кампании.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dash">
      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: -16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        style={{ marginBottom: 24 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <button
            className="ghost-button"
            type="button"
            onClick={() => navigate("/campaigns")}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <ArrowLeft size={14} />
            Назад
          </button>
          <div>
            <p className="dash-panel-title">Онбординг</p>
            <h2 style={{ fontSize: "1.3rem", marginTop: 4 }}>Запуск кампании за 4 шага</h2>
          </div>
        </div>
      </motion.div>

      <motion.div
        className="dash-panel"
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.05 }}
        style={{ maxWidth: 720, margin: "0 auto" }}
      >
        <StepIndicator current={step} total={4} />

        {error ? (
          <motion.div
            className="status-banner"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            style={{ marginBottom: 24, borderColor: "var(--danger)", color: "var(--danger)" }}
          >
            {error}
          </motion.div>
        ) : null}

        <AnimatePresence mode="wait">
          {/* ── Step 1: URL ── */}
          {step === 1 && (
            <motion.div
              key="step1"
              variants={stepVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
                <div className="dash-action-icon" style={{ background: "rgba(0,255,136,0.15)" }}>
                  <Globe size={20} style={{ color: "var(--accent)" }} />
                </div>
                <div>
                  <h3 style={{ fontSize: "1.1rem", marginBottom: 4 }}>Вставьте ссылку на ваш продукт</h3>
                  <p className="dash-empty" style={{ margin: 0 }}>
                    AI проанализирует продукт и подготовит стратегию комментирования
                  </p>
                </div>
              </div>

              <div className="stack-form">
                <label className="field">
                  <span
                    style={{
                      color: "var(--accent)",
                      fontSize: 11,
                      textTransform: "uppercase",
                      letterSpacing: "0.1em",
                      fontWeight: 500,
                    }}
                  >
                    URL продукта или лендинга
                  </span>
                  <input
                    type="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://yourproduct.com"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void handleAnalyze();
                    }}
                    autoFocus
                  />
                </label>

                <button
                  className="primary-button"
                  type="button"
                  disabled={busy || !url.trim()}
                  onClick={() => void handleAnalyze()}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    justifyContent: "center",
                    padding: "14px 24px",
                    fontSize: "0.95rem",
                  }}
                >
                  {busy ? (
                    <>
                      <RefreshCw size={16} className="spin" />
                      Анализируем продукт...
                    </>
                  ) : (
                    <>
                      <Brain size={16} />
                      Анализировать продукт
                      <ChevronRight size={16} />
                    </>
                  )}
                </button>
              </div>
            </motion.div>
          )}

          {/* ── Step 2: Brief review ── */}
          {step === 2 && effectiveBrief && (
            <motion.div
              key="step2"
              variants={stepVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
                <div className="dash-action-icon" style={{ background: "rgba(68,136,255,0.15)" }}>
                  <Brain size={20} style={{ color: "var(--info)" }} />
                </div>
                <div>
                  <h3 style={{ fontSize: "1.1rem", marginBottom: 4 }}>Результат анализа</h3>
                  <p className="dash-empty" style={{ margin: 0 }}>
                    Проверьте и при необходимости отредактируйте данные
                  </p>
                </div>
              </div>

              <div className="stack-form">
                <label className="field">
                  <span
                    style={{
                      color: "var(--accent)",
                      fontSize: 11,
                      textTransform: "uppercase",
                      letterSpacing: "0.1em",
                      fontWeight: 500,
                    }}
                  >
                    Название продукта
                  </span>
                  <input
                    value={editedBrief.product_name ?? effectiveBrief.product_name ?? ""}
                    onChange={(e) =>
                      setEditedBrief((prev) => ({ ...prev, product_name: e.target.value }))
                    }
                  />
                </label>

                <label className="field">
                  <span
                    style={{
                      color: "var(--accent)",
                      fontSize: 11,
                      textTransform: "uppercase",
                      letterSpacing: "0.1em",
                      fontWeight: 500,
                    }}
                  >
                    Целевая аудитория
                  </span>
                  <textarea
                    className="assistant-textarea"
                    rows={3}
                    value={editedBrief.target_audience ?? effectiveBrief.target_audience ?? ""}
                    onChange={(e) =>
                      setEditedBrief((prev) => ({ ...prev, target_audience: e.target.value }))
                    }
                  />
                </label>

                <label className="field">
                  <span
                    style={{
                      color: "var(--accent)",
                      fontSize: 11,
                      textTransform: "uppercase",
                      letterSpacing: "0.1em",
                      fontWeight: 500,
                    }}
                  >
                    УТП (уникальное торговое предложение)
                  </span>
                  <textarea
                    className="assistant-textarea"
                    rows={2}
                    value={editedBrief.usp ?? effectiveBrief.usp ?? ""}
                    onChange={(e) =>
                      setEditedBrief((prev) => ({ ...prev, usp: e.target.value }))
                    }
                  />
                </label>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <label className="field">
                    <span
                      style={{
                        color: "var(--accent)",
                        fontSize: 11,
                        textTransform: "uppercase",
                        letterSpacing: "0.1em",
                        fontWeight: 500,
                      }}
                    >
                      Тональность бренда
                    </span>
                    <select
                      value={editedBrief.brand_tone ?? effectiveBrief.brand_tone ?? "native"}
                      onChange={(e) =>
                        setEditedBrief((prev) => ({ ...prev, brand_tone: e.target.value }))
                      }
                    >
                      <option value="casual">Casual — разговорный</option>
                      <option value="formal">Formal — официальный</option>
                      <option value="expert">Expert — экспертный</option>
                      <option value="friendly">Friendly — дружелюбный</option>
                      <option value="bold">Bold — дерзкий</option>
                      <option value="native">Native — нативный</option>
                    </select>
                  </label>

                  <label className="field">
                    <span
                      style={{
                        color: "var(--accent)",
                        fontSize: 11,
                        textTransform: "uppercase",
                        letterSpacing: "0.1em",
                        fontWeight: 500,
                      }}
                    >
                      Комментариев в день
                    </span>
                    <input
                      type="number"
                      min={5}
                      max={500}
                      value={editedBrief.daily_volume ?? effectiveBrief.daily_volume ?? 30}
                      onChange={(e) =>
                        setEditedBrief((prev) => ({
                          ...prev,
                          daily_volume: Number(e.target.value),
                        }))
                      }
                    />
                  </label>
                </div>

                {(effectiveBrief.keywords?.length ?? 0) > 0 && (
                  <div>
                    <span
                      style={{
                        color: "var(--accent)",
                        fontSize: 11,
                        textTransform: "uppercase",
                        letterSpacing: "0.1em",
                        fontWeight: 500,
                        display: "block",
                        marginBottom: 8,
                      }}
                    >
                      Ключевые слова для поиска каналов
                    </span>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {(effectiveBrief.keywords ?? []).map((kw) => (
                        <span
                          key={kw}
                          className="pill"
                          style={{
                            background: "rgba(0,255,136,0.1)",
                            color: "var(--accent)",
                            border: "1px solid rgba(0,255,136,0.3)",
                            fontSize: 12,
                          }}
                        >
                          {kw}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                <div className="actions-row" style={{ marginTop: 8 }}>
                  <button
                    className="primary-button"
                    type="button"
                    onClick={() => setStep(3)}
                    style={{ display: "flex", alignItems: "center", gap: 8 }}
                  >
                    Выбрать план
                    <ChevronRight size={16} />
                  </button>
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() => setStep(1)}
                    style={{ display: "flex", alignItems: "center", gap: 6 }}
                  >
                    <ArrowLeft size={14} />
                    Назад
                  </button>
                </div>
              </div>
            </motion.div>
          )}

          {/* ── Step 3: Plan selection ── */}
          {step === 3 && (
            <motion.div
              key="step3"
              variants={stepVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
                <div className="dash-action-icon" style={{ background: "rgba(255,170,0,0.15)" }}>
                  <Rocket size={20} style={{ color: "var(--warning)" }} />
                </div>
                <div>
                  <h3 style={{ fontSize: "1.1rem", marginBottom: 4 }}>Выберите план</h3>
                  <p className="dash-empty" style={{ margin: 0 }}>
                    Начните с любого — тарифы можно изменить позже
                  </p>
                </div>
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(2, 1fr)",
                  gap: 16,
                  marginBottom: 24,
                }}
              >
                {PLANS.map((plan) => {
                  const Icon = plan.icon;
                  const isSelected = selectedPlan === plan.slug;
                  return (
                    <motion.div
                      key={plan.slug}
                      whileHover={{ scale: 1.01 }}
                      onClick={() => setSelectedPlan(plan.slug)}
                      style={{
                        padding: 20,
                        borderRadius: 12,
                        border: `2px solid ${isSelected ? plan.color : "var(--border)"}`,
                        background: isSelected
                          ? `rgba(${plan.color === "var(--accent)" ? "0,255,136" : plan.color === "var(--info)" ? "68,136,255" : plan.color === "var(--warning)" ? "255,170,0" : "255,107,157"},0.06)`
                          : "var(--surface-2)",
                        cursor: "pointer",
                        transition: "all 0.2s ease",
                        position: "relative",
                      }}
                    >
                      {plan.featured && (
                        <div
                          style={{
                            position: "absolute",
                            top: -10,
                            right: 12,
                            background: "var(--accent)",
                            color: "var(--bg)",
                            fontSize: 10,
                            fontWeight: 700,
                            padding: "3px 10px",
                            borderRadius: 999,
                            textTransform: "uppercase",
                            letterSpacing: "0.05em",
                          }}
                        >
                          Популярный
                        </div>
                      )}
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          marginBottom: 12,
                        }}
                      >
                        <Icon size={20} style={{ color: plan.color }} />
                        <strong style={{ fontSize: "1rem", color: "var(--text)" }}>
                          {plan.name}
                        </strong>
                        {isSelected && (
                          <CheckCircle
                            size={16}
                            style={{ color: plan.color, marginLeft: "auto" }}
                          />
                        )}
                      </div>
                      <div
                        style={{
                          fontSize: "1.2rem",
                          fontWeight: 700,
                          color: plan.color,
                          marginBottom: 12,
                          fontFamily: "var(--font-mono)",
                        }}
                      >
                        {plan.price}
                      </div>
                      <ul
                        style={{
                          listStyle: "none",
                          padding: 0,
                          margin: 0,
                          display: "flex",
                          flexDirection: "column",
                          gap: 6,
                        }}
                      >
                        {plan.features.map((f) => (
                          <li
                            key={f}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 6,
                              fontSize: 12,
                              color: "var(--text-secondary)",
                            }}
                          >
                            <CheckCircle size={12} style={{ color: plan.color, flexShrink: 0 }} />
                            {f}
                          </li>
                        ))}
                      </ul>
                    </motion.div>
                  );
                })}
              </div>

              <div className="actions-row">
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => setStep(4 as Step)}
                  style={{ display: "flex", alignItems: "center", gap: 8 }}
                >
                  Продолжить
                  <ChevronRight size={16} />
                </button>
                <button
                  className="ghost-button"
                  type="button"
                  onClick={() => setStep(2)}
                  style={{ display: "flex", alignItems: "center", gap: 6 }}
                >
                  <ArrowLeft size={14} />
                  Назад
                </button>
              </div>
            </motion.div>
          )}

          {/* ── Step 4: Launch ── */}
          {step === 4 && (
            <motion.div
              key="step4"
              variants={stepVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            >
              {launched ? (
                <div style={{ textAlign: "center", padding: "32px 0" }}>
                  <motion.div
                    initial={{ scale: 0 }}
                    animate={{ scale: 1 }}
                    transition={{ type: "spring", stiffness: 200, damping: 15 }}
                    style={{
                      width: 80,
                      height: 80,
                      borderRadius: "50%",
                      background: "rgba(0,255,136,0.15)",
                      border: "2px solid var(--accent)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      margin: "0 auto 24px",
                    }}
                  >
                    <CheckCircle size={40} style={{ color: "var(--accent)" }} />
                  </motion.div>
                  <h3 style={{ fontSize: "1.3rem", marginBottom: 8 }}>
                    Кампания создана!
                  </h3>
                  <p style={{ color: "var(--text-secondary)", marginBottom: 24 }}>
                    Кампания создана в статусе «Черновик». Перейдите в раздел кампаний, чтобы запустить её.
                  </p>
                  <div className="actions-row" style={{ justifyContent: "center" }}>
                    <button
                      className="primary-button"
                      type="button"
                      onClick={() => navigate("/campaigns")}
                      style={{ display: "flex", alignItems: "center", gap: 8 }}
                    >
                      <Rocket size={16} />
                      К кампаниям
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
                    <div
                      className="dash-action-icon"
                      style={{ background: "rgba(0,255,136,0.15)" }}
                    >
                      <Rocket size={20} style={{ color: "var(--accent)" }} />
                    </div>
                    <div>
                      <h3 style={{ fontSize: "1.1rem", marginBottom: 4 }}>Запуск!</h3>
                      <p className="dash-empty" style={{ margin: 0 }}>
                        Проверьте итоговые данные и запустите кампанию
                      </p>
                    </div>
                  </div>

                  {effectiveBrief && (
                    <div className="terminal-window" style={{ marginBottom: 24 }}>
                      <div className="terminal-line">
                        <span className="timestamp">brief</span>
                        <span className="message white">
                          {effectiveBrief.product_name || "Продукт"}
                        </span>
                      </div>
                      <div className="terminal-line">
                        <span className="timestamp">тон</span>
                        <span className="message">{effectiveBrief.brand_tone || "native"}</span>
                      </div>
                      <div className="terminal-line">
                        <span className="timestamp">план</span>
                        <span className="message" style={{ color: "var(--accent)" }}>
                          {PLANS.find((p) => p.slug === selectedPlan)?.name ?? selectedPlan}
                        </span>
                      </div>
                      <div className="terminal-line">
                        <span className="timestamp">лимит</span>
                        <span className="message">
                          {effectiveBrief.daily_volume ?? 30} комментариев/день
                        </span>
                      </div>
                    </div>
                  )}

                  <div className="actions-row">
                    <button
                      className="primary-button"
                      type="button"
                      disabled={busy}
                      onClick={() => void handleLaunch()}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "14px 28px",
                        fontSize: "0.95rem",
                      }}
                    >
                      {busy ? (
                        <>
                          <RefreshCw size={16} className="spin" />
                          Создаём кампанию...
                        </>
                      ) : (
                        <>
                          <Rocket size={16} />
                          Создать кампанию
                        </>
                      )}
                    </button>
                    <button
                      className="ghost-button"
                      type="button"
                      onClick={() => setStep(3)}
                      style={{ display: "flex", alignItems: "center", gap: 6 }}
                    >
                      <ArrowLeft size={14} />
                      Назад
                    </button>
                  </div>
                </>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
