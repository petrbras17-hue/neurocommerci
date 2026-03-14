import { useState, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";
import { useNavigate } from "react-router-dom";

type WizardStep = "upload" | "proxy" | "verify" | "harden" | "warmup" | "ready";

const STEPS: { key: WizardStep; label: string; num: number }[] = [
  { key: "upload", label: "Загрузка", num: 1 },
  { key: "proxy", label: "Прокси", num: 2 },
  { key: "verify", label: "Проверка", num: 3 },
  { key: "harden", label: "Защита", num: 4 },
  { key: "warmup", label: "Прогрев", num: 5 },
  { key: "ready", label: "Готово", num: 6 },
];

export function AccountOnboardingWizard() {
  const { accessToken } = useAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState<WizardStep>("upload");
  const [accountId, setAccountId] = useState<number | null>(null);
  const [accountPhone, setAccountPhone] = useState<string>("");
  const [uploadType, setUploadType] = useState<"session" | "tdata">("session");
  const [proxyInput, setProxyInput] = useState("");
  const [proxyId, setProxyId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [verifyResult, setVerifyResult] = useState<Record<string, unknown> | null>(null);
  const [hardenResult, setHardenResult] = useState<Record<string, unknown> | null>(null);

  const addLog = useCallback((msg: string) => {
    setLog((prev) => [...prev, `[${new Date().toLocaleTimeString("ru-RU")}] ${msg}`]);
  }, []);

  // Step 1: Upload
  const handleUpload = async (files: FileList) => {
    setLoading(true);
    addLog("Загружаем файлы...");
    try {
      const formData = new FormData();
      if (uploadType === "session") {
        const sessionFile = Array.from(files).find((f) => f.name.endsWith(".session"));
        const jsonFile = Array.from(files).find((f) => f.name.endsWith(".json"));
        if (!sessionFile || !jsonFile) {
          addLog("❌ Нужны .session и .json файлы");
          return;
        }
        formData.append("session", sessionFile);
        formData.append("metadata", jsonFile);
        const result = await apiFetch<{ id: number; phone: string }>("/v1/admin/onboarding/upload-session", {
          method: "POST",
          body: formData,
          accessToken,
        });
        setAccountId(result.id);
        setAccountPhone(result.phone);
        addLog(`✓ Аккаунт загружен: ${result.phone} (ID: ${result.id})`);
      } else {
        const zipFile = files[0];
        if (!zipFile) {
          addLog("❌ Выберите ZIP с tdata");
          return;
        }
        formData.append("tdata", zipFile);
        const result = await apiFetch<{ id: number; phone: string }>("/v1/admin/onboarding/upload-tdata", {
          method: "POST",
          body: formData,
          accessToken,
        });
        setAccountId(result.id);
        setAccountPhone(result.phone);
        addLog(`✓ tdata конвертирован: ${result.phone} (ID: ${result.id})`);
      }
      setStep("proxy");
    } catch (e) {
      addLog(`❌ Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  // Step 2: Proxy
  const handleProxy = async () => {
    if (!accountId || !proxyInput.trim()) return;
    setLoading(true);
    addLog("Импортируем прокси...");
    try {
      const result = await apiFetch<{ imported: number; proxies: { id: number }[] }>("/v1/admin/proxies/import", {
        method: "POST",
        json: { lines: [proxyInput.trim()], proxy_type: "socks5" },
        accessToken,
      });
      if (result.proxies.length > 0) {
        const pId = result.proxies[0].id;
        setProxyId(pId);
        addLog(`✓ Прокси импортирован (ID: ${pId})`);

        // Test proxy
        addLog("Тестируем прокси...");
        const testResult = await apiFetch<{ status: string; is_dual: boolean }>(`/v1/admin/proxies/${pId}/test`, {
          method: "POST",
          accessToken,
        });
        addLog(`✓ Прокси: ${testResult.status} | DUAL: ${testResult.is_dual}`);

        // Bind
        addLog("Привязываем прокси к аккаунту...");
        await apiFetch(`/v1/admin/proxies/${pId}/bind/${accountId}`, { method: "POST", accessToken });
        addLog("✓ Прокси привязан к аккаунту");
        setStep("verify");
      }
    } catch (e) {
      addLog(`❌ Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  // Step 3: Verify
  const handleVerify = async () => {
    if (!accountId) return;
    setLoading(true);
    addLog("Подключаемся к Telegram...");
    try {
      const result = await apiFetch<Record<string, unknown>>(`/v1/admin/onboarding/accounts/${accountId}/verify`, {
        method: "POST",
        accessToken,
      });
      setVerifyResult(result);
      if (result.authorized) {
        addLog(`✓ Авторизован: ${String(result.name)} (@${String(result.username || "—")})`);
        setStep("harden");
      } else {
        addLog("❌ Аккаунт не авторизован — сессия может быть мёртвой");
      }
    } catch (e) {
      addLog(`❌ Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  // Step 4: Harden
  const handleHarden = async () => {
    if (!accountId) return;
    setLoading(true);
    addLog("🔒 Запускаем усиление безопасности (это займёт ~30-60 сек)...");
    try {
      const result = await apiFetch<Record<string, unknown>>(`/v1/admin/onboarding/accounts/${accountId}/harden`, {
        method: "POST",
        accessToken,
      });
      setHardenResult(result);
      addLog(`✓ Сессий убито: ${String(result.sessions_terminated)}`);
      addLog(`✓ 2FA: ${result.two_fa_set ? "установлен" : "уже был"}`);
      addLog(`✓ Приватность: ${result.privacy_configured ? "настроена" : "ошибка"}`);
      setStep("warmup");
    } catch (e) {
      addLog(`❌ Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  };

  const currentStepIdx = STEPS.findIndex((s) => s.key === step);

  return (
    <div style={{ padding: 24, maxWidth: 800, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, color: "#ff4444", marginBottom: 24 }}>Onboarding — Новый аккаунт</h1>

      {/* Step indicator */}
      <div style={{ display: "flex", gap: 4, marginBottom: 32 }}>
        {STEPS.map((s, i) => (
          <div
            key={s.key}
            style={{
              flex: 1,
              height: 4,
              borderRadius: 2,
              background: i <= currentStepIdx ? (i === currentStepIdx ? "#ff4444" : "#00ff88") : "rgba(255,255,255,0.1)",
              transition: "background 0.3s ease",
            }}
          />
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 24, fontSize: 12, color: "#888" }}>
        {STEPS.map((s) => (
          <span key={s.key} style={{ color: s.key === step ? "#ff4444" : undefined }}>
            {s.num}. {s.label}
          </span>
        ))}
      </div>

      {/* Step content */}
      {step === "upload" && (
        <div className="card">
          <h2>Шаг 1: Загрузка аккаунта</h2>
          <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
            <button
              className={uploadType === "session" ? "btn-accent" : "btn-secondary"}
              onClick={() => setUploadType("session")}
            >
              .session + .json
            </button>
            <button
              className={uploadType === "tdata" ? "btn-accent" : "btn-secondary"}
              onClick={() => setUploadType("tdata")}
            >
              tdata ZIP
            </button>
          </div>
          <input
            type="file"
            multiple={uploadType === "session"}
            accept={uploadType === "session" ? ".session,.json" : ".zip"}
            onChange={(e) => e.target.files && handleUpload(e.target.files)}
            disabled={loading}
            style={{ marginBottom: 12 }}
          />
          <p style={{ fontSize: 12, color: "#888" }}>
            {uploadType === "session"
              ? "Выберите .session и metadata.json файлы"
              : "Выберите ZIP архив с папкой tdata"}
          </p>
        </div>
      )}

      {step === "proxy" && (
        <div className="card">
          <h2>Шаг 2: Прокси</h2>
          <p style={{ color: "#888", fontSize: 13 }}>Формат: host:port:user:password (SOCKS5)</p>
          <input
            type="text"
            value={proxyInput}
            onChange={(e) => setProxyInput(e.target.value)}
            placeholder="91.147.123.84:49156:user:pass"
            style={{ width: "100%", padding: "8px 12px", marginBottom: 12 }}
          />
          <button className="btn-accent" onClick={handleProxy} disabled={loading || !proxyInput.trim()}>
            {loading ? "Тестируем..." : "Импорт + Тест + Привязка"}
          </button>
        </div>
      )}

      {step === "verify" && (
        <div className="card">
          <h2>Шаг 3: Проверка авторизации</h2>
          <p style={{ color: "#888" }}>Аккаунт: {accountPhone}</p>
          <button className="btn-accent" onClick={handleVerify} disabled={loading}>
            {loading ? "Подключаемся..." : "Проверить авторизацию"}
          </button>
          {verifyResult && (
            <div style={{ marginTop: 12, padding: 12, background: "rgba(0,255,136,0.05)", borderRadius: 8, fontSize: 13 }}>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(verifyResult, null, 2)}</pre>
            </div>
          )}
        </div>
      )}

      {step === "harden" && (
        <div className="card">
          <h2>Шаг 4: Усиление безопасности</h2>
          <p style={{ color: "#ffcc00", fontSize: 13 }}>
            ⚠️ Это займёт 30-60 секунд. Все действия выполняются с человеческими задержками.
          </p>
          <ul style={{ fontSize: 13, color: "#888", marginBottom: 16 }}>
            <li>Убить чужие сессии</li>
            <li>Установить 2FA пароль</li>
            <li>Настроить приватность (телефон, last seen, фото, пересылки)</li>
          </ul>
          <button className="btn-accent" onClick={handleHarden} disabled={loading}>
            {loading ? "Усиляем защиту..." : "🔒 Запустить хардинг"}
          </button>
        </div>
      )}

      {step === "warmup" && (
        <div className="card">
          <h2>Шаг 5: Прогрев</h2>
          <p style={{ color: "#888" }}>
            Аккаунт защищён. Теперь необходимо прогреть его 24-48 часов в консервативном режиме.
          </p>
          <p style={{ fontSize: 13, color: "#ffcc00" }}>
            ⚠️ НЕ меняйте профиль (имя, био, аватар) минимум 48 часов!
          </p>
          <button className="btn-accent" onClick={() => { addLog("Прогрев доступен через /warmup"); setStep("ready"); }}>
            Перейти к финалу →
          </button>
        </div>
      )}

      {step === "ready" && (
        <div className="card" style={{ textAlign: "center" }}>
          <h2 style={{ color: "#00ff88" }}>✓ Аккаунт готов</h2>
          <p>Телефон: <strong>{accountPhone}</strong></p>
          <p>Статус: <strong style={{ color: "#00ff88" }}>HARDENED</strong></p>
          <p style={{ color: "#888", fontSize: 13 }}>
            Аккаунт защищён и привязан к прокси. Запустите прогрев через страницу /warmup.
          </p>
          <button className="btn-accent" onClick={() => navigate("/admin-dashboard")}>
            К Dashboard →
          </button>
        </div>
      )}

      {/* Operation Log */}
      {log.length > 0 && (
        <div style={{
          marginTop: 24,
          padding: 16,
          background: "rgba(0,0,0,0.3)",
          borderRadius: 8,
          border: "1px solid rgba(255,255,255,0.05)",
          maxHeight: 300,
          overflowY: "auto",
        }}>
          <div style={{ fontSize: 12, color: "#888", marginBottom: 8 }}>Лог операций:</div>
          {log.map((entry, i) => (
            <div key={i} style={{
              fontSize: 12,
              fontFamily: "'JetBrains Mono', monospace",
              color: entry.includes("❌") ? "#ff4444" : entry.includes("✓") ? "#00ff88" : "#ccc",
              padding: "2px 0",
            }}>
              {entry}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
