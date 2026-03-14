import { useEffect, useState, useCallback } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";
import { OperationLogPanel } from "../../components/admin/OperationLogPanel";

interface WarmupConfig {
  id: number;
  name: string;
  status: string;
  mode: string;
  schedule_start_hour: number;
  schedule_end_hour: number;
  sessions_per_day: number;
  session_duration_minutes: number;
  enable_story_viewing: boolean;
  enable_channel_joining: boolean;
  enable_dialogs: boolean;
  max_channels_to_join: number;
}

interface SessionProgress {
  id: number;
  account_id: number;
  status: string;
  actions_completed: number;
  channels_visited: number;
  stories_viewed: number;
  channels_joined: number;
  days_warmed: number;
  progress_pct: number;
  started_at: string | null;
  completed_at: string | null;
}

export function WarmupControlPage() {
  const { accessToken, workspaceId } = useAuth();
  const [configs, setConfigs] = useState<WarmupConfig[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [progress, setProgress] = useState<SessionProgress[]>([]);
  const [loading, setLoading] = useState(true);

  // Schedule form
  const [scheduleForm, setScheduleForm] = useState({
    schedule_start_hour: 9,
    schedule_end_hour: 22,
    sessions_per_day: 3,
    session_duration_minutes: 30,
    enable_story_viewing: true,
    enable_channel_joining: true,
    enable_dialogs: false,
    max_channels_to_join: 3,
  });

  const loadConfigs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiFetch<{ items: WarmupConfig[] }>("/v1/warmup", {
        accessToken,
      });
      setConfigs(data.items || []);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, [accessToken]);

  const loadProgress = useCallback(
    async (configId: number) => {
      try {
        const data = await apiFetch<{
          sessions: SessionProgress[];
        }>(`/v1/warmup/${configId}/progress`, { accessToken });
        setProgress(data.sessions || []);
      } catch {
        /* ignore */
      }
    },
    [accessToken]
  );

  useEffect(() => {
    loadConfigs();
  }, [loadConfigs]);

  useEffect(() => {
    if (selectedId) {
      loadProgress(selectedId);
      const interval = setInterval(() => loadProgress(selectedId), 5000);
      return () => clearInterval(interval);
    }
  }, [selectedId, loadProgress]);

  useEffect(() => {
    if (selectedId) {
      const cfg = configs.find((c) => c.id === selectedId);
      if (cfg) {
        setScheduleForm({
          schedule_start_hour: cfg.schedule_start_hour ?? 9,
          schedule_end_hour: cfg.schedule_end_hour ?? 22,
          sessions_per_day: cfg.sessions_per_day ?? 3,
          session_duration_minutes: cfg.session_duration_minutes ?? 30,
          enable_story_viewing: cfg.enable_story_viewing ?? true,
          enable_channel_joining: cfg.enable_channel_joining ?? true,
          enable_dialogs: cfg.enable_dialogs ?? false,
          max_channels_to_join: cfg.max_channels_to_join ?? 3,
        });
      }
    }
  }, [selectedId, configs]);

  const saveSchedule = async () => {
    if (!selectedId) return;
    try {
      await apiFetch(`/v1/warmup/${selectedId}/schedule`, {
        accessToken,
        method: "PUT",
        body: scheduleForm,
      });
      loadConfigs();
    } catch {
      /* ignore */
    }
  };

  const doAction = async (action: string) => {
    if (!selectedId) return;
    try {
      await apiFetch(`/v1/warmup/${selectedId}/${action}`, {
        accessToken,
        method: "POST",
      });
      loadConfigs();
    } catch {
      /* ignore */
    }
  };

  const selectedConfig = configs.find((c) => c.id === selectedId);

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      <h1 style={{ marginBottom: 20 }}>Warmup v2 Control</h1>

      {/* Config selector */}
      <div style={{ display: "flex", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
        {loading && <div className="muted">Загрузка...</div>}
        {configs.map((cfg) => (
          <button
            key={cfg.id}
            onClick={() => setSelectedId(cfg.id)}
            className={`btn ${selectedId === cfg.id ? "btn-primary" : "btn-ghost"}`}
          >
            {cfg.name} [{cfg.status}]
          </button>
        ))}
      </div>

      {selectedConfig && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
          {/* Left: Schedule config */}
          <div className="card" style={{ padding: 20 }}>
            <h3 style={{ marginBottom: 16 }}>Расписание</h3>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <label>
                <span className="muted">Начало (час)</span>
                <input
                  type="range"
                  min={0}
                  max={23}
                  value={scheduleForm.schedule_start_hour}
                  onChange={(e) =>
                    setScheduleForm((f) => ({
                      ...f,
                      schedule_start_hour: +e.target.value,
                    }))
                  }
                />
                <span>{scheduleForm.schedule_start_hour}:00</span>
              </label>
              <label>
                <span className="muted">Конец (час)</span>
                <input
                  type="range"
                  min={0}
                  max={23}
                  value={scheduleForm.schedule_end_hour}
                  onChange={(e) =>
                    setScheduleForm((f) => ({
                      ...f,
                      schedule_end_hour: +e.target.value,
                    }))
                  }
                />
                <span>{scheduleForm.schedule_end_hour}:00</span>
              </label>
              <label>
                <span className="muted">Сессий в день</span>
                <input
                  type="number"
                  min={1}
                  max={24}
                  value={scheduleForm.sessions_per_day}
                  onChange={(e) =>
                    setScheduleForm((f) => ({
                      ...f,
                      sessions_per_day: +e.target.value,
                    }))
                  }
                  style={{
                    background: "#1a1a1a",
                    color: "#ccc",
                    border: "1px solid #333",
                    borderRadius: 4,
                    padding: "4px 8px",
                    width: "100%",
                  }}
                />
              </label>
              <label>
                <span className="muted">Длительность (мин)</span>
                <input
                  type="number"
                  min={5}
                  max={480}
                  value={scheduleForm.session_duration_minutes}
                  onChange={(e) =>
                    setScheduleForm((f) => ({
                      ...f,
                      session_duration_minutes: +e.target.value,
                    }))
                  }
                  style={{
                    background: "#1a1a1a",
                    color: "#ccc",
                    border: "1px solid #333",
                    borderRadius: 4,
                    padding: "4px 8px",
                    width: "100%",
                  }}
                />
              </label>
            </div>

            {/* Feature toggles */}
            <h4 style={{ marginTop: 16, marginBottom: 8 }}>Функции</h4>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {[
                {
                  key: "enable_story_viewing" as const,
                  label: "Просмотр сторис",
                },
                {
                  key: "enable_channel_joining" as const,
                  label: "Вступление в каналы",
                },
                { key: "enable_dialogs" as const, label: "Диалоги" },
              ].map(({ key, label }) => (
                <label key={key} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    checked={scheduleForm[key]}
                    onChange={(e) =>
                      setScheduleForm((f) => ({ ...f, [key]: e.target.checked }))
                    }
                  />
                  {label}
                </label>
              ))}
              <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span className="muted">Макс каналов для вступления:</span>
                <input
                  type="number"
                  min={0}
                  max={20}
                  value={scheduleForm.max_channels_to_join}
                  onChange={(e) =>
                    setScheduleForm((f) => ({
                      ...f,
                      max_channels_to_join: +e.target.value,
                    }))
                  }
                  style={{
                    background: "#1a1a1a",
                    color: "#ccc",
                    border: "1px solid #333",
                    borderRadius: 4,
                    padding: "2px 6px",
                    width: 60,
                  }}
                />
              </label>
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button className="btn btn-primary" onClick={saveSchedule}>
                Сохранить
              </button>
              <button className="btn btn-primary" onClick={() => doAction("start")}>
                Start
              </button>
              <button className="btn btn-ghost" onClick={() => doAction("stop")}>
                Stop
              </button>
            </div>
          </div>

          {/* Right: Progress */}
          <div className="card" style={{ padding: 20 }}>
            <h3 style={{ marginBottom: 16 }}>
              Прогресс ({progress.length} сессий)
            </h3>
            {progress.length === 0 && (
              <div className="muted">Нет активных сессий</div>
            )}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 12,
                maxHeight: 400,
                overflowY: "auto",
              }}
            >
              {progress.map((s) => (
                <div
                  key={s.id}
                  style={{
                    background: "#111",
                    border: "1px solid #222",
                    borderRadius: 6,
                    padding: 12,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                    <span>
                      Аккаунт #{s.account_id}
                    </span>
                    <span
                      style={{
                        color:
                          s.status === "running"
                            ? "#00ff88"
                            : s.status === "completed"
                            ? "#888"
                            : s.status === "failed"
                            ? "#ff4444"
                            : "#ffaa00",
                      }}
                    >
                      {s.status}
                    </span>
                  </div>
                  {/* Progress bar */}
                  <div
                    style={{
                      background: "#222",
                      borderRadius: 4,
                      height: 6,
                      marginBottom: 8,
                    }}
                  >
                    <div
                      style={{
                        background: "#00ff88",
                        width: `${Math.min(100, s.progress_pct)}%`,
                        height: "100%",
                        borderRadius: 4,
                        transition: "width 0.3s",
                      }}
                    />
                  </div>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(4, 1fr)",
                      gap: 4,
                      fontSize: 11,
                      color: "#888",
                    }}
                  >
                    <span>Actions: {s.actions_completed}</span>
                    <span>Channels: {s.channels_visited}</span>
                    <span>Stories: {s.stories_viewed}</span>
                    <span>Joined: {s.channels_joined}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Operation log panel */}
      {workspaceId && (
        <div style={{ marginTop: 24 }}>
          <h3 style={{ marginBottom: 12 }}>Live Warmup Logs</h3>
          <OperationLogPanel
            workspaceId={workspaceId}
            moduleFilter="warmup"
            compact
          />
        </div>
      )}
    </div>
  );
}
