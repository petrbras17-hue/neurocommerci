import { useEffect, useState } from "react";
import { apiFetch, profileApi, ProfileTemplate } from "../api";
import { useAuth } from "../auth";

type AccountRow = {
  id: number;
  phone: string;
  status: string;
  health_status: string;
};

type AccountsResponse = {
  items: AccountRow[];
  total: number;
};

const GENDER_OPTIONS = [
  { value: "any", label: "Любой" },
  { value: "male", label: "Мужской" },
  { value: "female", label: "Женский" },
];

const AVATAR_STYLE_OPTIONS = [
  { value: "ai_generated", label: "AI-генерация" },
  { value: "library", label: "Библиотека" },
  { value: "custom", label: "Свой" },
];

function statusBadgeClass(status: string): string {
  if (status === "alive" || status === "active") return "badge-green";
  if (status === "error") return "badge-red";
  return "badge-gray";
}

export function ProfilesPage() {
  const { accessToken } = useAuth();

  const [templates, setTemplates] = useState<ProfileTemplate[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [selectedAccountIds, setSelectedAccountIds] = useState<number[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);

  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");

  // Create template form state
  const [showCreateTemplate, setShowCreateTemplate] = useState(false);
  const [tplName, setTplName] = useState("");
  const [tplGender, setTplGender] = useState("any");
  const [tplGeo, setTplGeo] = useState("");
  const [tplBio, setTplBio] = useState("");
  const [tplChannelName, setTplChannelName] = useState("");
  const [tplChannelDesc, setTplChannelDesc] = useState("");
  const [tplFirstPost, setTplFirstPost] = useState("");
  const [tplAvatarStyle, setTplAvatarStyle] = useState("ai_generated");

  // Create channel form state
  const [channelName, setChannelName] = useState("");
  const [channelDescription, setChannelDescription] = useState("");
  const [channelFirstPost, setChannelFirstPost] = useState("");
  const [channelAccountId, setChannelAccountId] = useState<number | null>(null);

  const loadTemplates = async () => {
    if (!accessToken) return;
    const payload = await profileApi.listTemplates(accessToken);
    setTemplates(payload.items);
    if (payload.items.length && !selectedTemplateId) {
      setSelectedTemplateId(payload.items[0].id);
    }
  };

  const loadAccounts = async () => {
    if (!accessToken) return;
    const payload = await apiFetch<AccountsResponse>("/v1/web/accounts", { accessToken });
    setAccounts(payload.items);
    if (payload.items.length && !channelAccountId) {
      setChannelAccountId(payload.items[0].id);
    }
  };

  useEffect(() => {
    void Promise.all([loadTemplates(), loadAccounts()]).catch(() => {});
  }, [accessToken]);

  const handleCreateTemplate = async () => {
    if (!accessToken || !tplName.trim()) {
      setStatusMessage("Введите название шаблона.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await profileApi.createTemplate(accessToken, {
        name: tplName.trim(),
        gender: tplGender,
        geo: tplGeo || null,
        bio_template: tplBio || null,
        channel_name_template: tplChannelName || null,
        channel_description_template: tplChannelDesc || null,
        channel_first_post_template: tplFirstPost || null,
        avatar_style: tplAvatarStyle,
      });
      setShowCreateTemplate(false);
      setTplName("");
      setTplGeo("");
      setTplBio("");
      setTplChannelName("");
      setTplChannelDesc("");
      setTplFirstPost("");
      setStatusMessage("Шаблон профиля создан.");
      await loadTemplates();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "create_template_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleMassGenerate = async () => {
    if (!accessToken) return;
    if (!selectedAccountIds.length) {
      setStatusMessage("Выберите хотя бы один аккаунт.");
      return;
    }
    if (!selectedTemplateId) {
      setStatusMessage("Выберите шаблон профиля.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      const result = await profileApi.massGenerate(accessToken, selectedAccountIds, selectedTemplateId);
      setStatusMessage(`Задача генерации профилей поставлена в очередь. Job #${result.job_id}.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "mass_generate_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleCreateChannel = async () => {
    if (!accessToken || !channelAccountId) {
      setStatusMessage("Выберите аккаунт для создания канала.");
      return;
    }
    if (!channelName.trim()) {
      setStatusMessage("Введите название канала.");
      return;
    }
    setBusy(true);
    setStatusMessage("");
    try {
      await profileApi.createChannel(accessToken, channelAccountId, {
        name: channelName.trim(),
        description: channelDescription,
        first_post: channelFirstPost,
      });
      setChannelName("");
      setChannelDescription("");
      setChannelFirstPost("");
      setStatusMessage("Канал создан и закреплён у аккаунта.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "create_channel_failed");
    } finally {
      setBusy(false);
    }
  };

  const toggleAccountSelection = (id: number) => {
    setSelectedAccountIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  };

  const selectedTemplate = templates.find((t) => t.id === selectedTemplateId) ?? null;

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Profile Factory</div>
              <h2>Фабрика профилей</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Создаёт AI-профили для аккаунтов: имя, биография, аватар.</li>
            <li>Массовая генерация по шаблону — до 50 аккаунтов за раз.</li>
            <li>Создаёт личный канал и прикрепляет первый пост для каждого аккаунта.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Шаблоны и аккаунты</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block">
              <strong>Шаблонов</strong>
              <span>{templates.length}</span>
            </div>
            <div className="info-block">
              <strong>Аккаунтов</strong>
              <span>{accounts.length}</span>
            </div>
            <div className="info-block">
              <strong>Выбрано</strong>
              <span>{selectedAccountIds.length}</span>
            </div>
            <div className="info-block">
              <strong>Живых</strong>
              <span>{accounts.filter((a) => a.health_status === "alive").length}</span>
            </div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      {/* Profile templates */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Шаблоны профилей</div>
            <h2>Ваши шаблоны</h2>
          </div>
          <div className="badge-row">
            <button className="primary-button" type="button" onClick={() => setShowCreateTemplate(true)}>
              + Создать шаблон
            </button>
          </div>
        </div>
        {templates.length ? (
          <div className="creative-list">
            {templates.map((tpl) => (
              <div
                key={tpl.id}
                className={`creative-item ${selectedTemplateId === tpl.id ? "selected" : ""}`}
                style={{ cursor: "pointer" }}
                onClick={() => setSelectedTemplateId(tpl.id)}
              >
                <div className="thread-meta">
                  <strong>{tpl.name}</strong>
                  <span className="pill badge-gray">{tpl.gender ?? "любой"}</span>
                  {tpl.geo ? <span className="muted">Гео: {tpl.geo}</span> : null}
                  <span className="muted">Аватар: {tpl.avatar_style ?? "—"}</span>
                </div>
                {tpl.bio_template ? (
                  <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
                    {tpl.bio_template.slice(0, 100)}
                    {tpl.bio_template.length > 100 ? "…" : ""}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="muted">Нет шаблонов. Создайте шаблон для генерации AI-профилей.</p>
        )}
      </section>

      {/* Template preview */}
      {selectedTemplate ? (
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Предпросмотр шаблона</div>
              <h2>{selectedTemplate.name}</h2>
            </div>
          </div>
          <div className="field-list">
            <div className="field-row">
              <strong>Пол</strong>
              <span className="field-value">{selectedTemplate.gender ?? "—"}</span>
            </div>
            <div className="field-row">
              <strong>Гео</strong>
              <span className="field-value">{selectedTemplate.geo ?? "—"}</span>
            </div>
            <div className="field-row">
              <strong>Биография</strong>
              <span className="field-value">{selectedTemplate.bio_template ?? "—"}</span>
            </div>
            <div className="field-row">
              <strong>Название канала</strong>
              <span className="field-value">{selectedTemplate.channel_name_template ?? "—"}</span>
            </div>
            <div className="field-row">
              <strong>Описание канала</strong>
              <span className="field-value">{selectedTemplate.channel_description_template ?? "—"}</span>
            </div>
            <div className="field-row">
              <strong>Первый пост</strong>
              <span className="field-value">{selectedTemplate.channel_first_post_template ?? "—"}</span>
            </div>
            <div className="field-row">
              <strong>Стиль аватара</strong>
              <span className="field-value">{selectedTemplate.avatar_style ?? "—"}</span>
            </div>
          </div>
        </section>
      ) : null}

      {/* Mass generate */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Массовая генерация</div>
            <h2>Применить шаблон к аккаунтам</h2>
          </div>
        </div>
        <div className="stack-form">
          <label className="field">
            <span>Шаблон профиля</span>
            <select
              value={selectedTemplateId ?? ""}
              onChange={(e) => setSelectedTemplateId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">— выберите шаблон —</option>
              {templates.map((tpl) => (
                <option key={tpl.id} value={tpl.id}>
                  {tpl.name}
                </option>
              ))}
            </select>
          </label>
          <div className="field">
            <span>Выберите аккаунты ({selectedAccountIds.length} выбрано)</span>
            <div className="thread-list" style={{ maxHeight: 200, overflowY: "auto" }}>
              {accounts.map((acc) => (
                <label
                  key={acc.id}
                  style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", cursor: "pointer" }}
                >
                  <input
                    type="checkbox"
                    checked={selectedAccountIds.includes(acc.id)}
                    onChange={() => toggleAccountSelection(acc.id)}
                  />
                  <span>{acc.phone}</span>
                  <span className={`pill ${statusBadgeClass(acc.health_status)}`}>
                    {acc.health_status}
                  </span>
                </label>
              ))}
            </div>
          </div>
          <div className="inline-note">
            Генерация профилей изменяет имя, биографию и аватар аккаунтов. Задача выполняется с задержками между аккаунтами для снижения рисков.
          </div>
          <div className="actions-row">
            <button
              className="primary-button"
              type="button"
              disabled={busy || !selectedAccountIds.length || !selectedTemplateId}
              onClick={() => void handleMassGenerate()}
            >
              {busy ? "Генерируем…" : `Сгенерировать профили для ${selectedAccountIds.length} аккаунт${selectedAccountIds.length === 1 ? "а" : "ов"}`}
            </button>
          </div>
        </div>
      </section>

      {/* Create channel */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Создание канала</div>
            <h2>Личный канал аккаунта</h2>
          </div>
        </div>
        <div className="stack-form">
          <label className="field">
            <span>Аккаунт</span>
            <select
              value={channelAccountId ?? ""}
              onChange={(e) => setChannelAccountId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">— выберите аккаунт —</option>
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id}>
                  {acc.phone} ({acc.health_status})
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Название канала</span>
            <input
              value={channelName}
              onChange={(e) => setChannelName(e.target.value)}
              placeholder="Название личного канала"
            />
          </label>
          <label className="field">
            <span>Описание канала</span>
            <textarea
              className="assistant-textarea"
              value={channelDescription}
              onChange={(e) => setChannelDescription(e.target.value)}
              placeholder="Описание канала..."
              rows={3}
            />
          </label>
          <label className="field">
            <span>Первый пост</span>
            <textarea
              className="assistant-textarea"
              value={channelFirstPost}
              onChange={(e) => setChannelFirstPost(e.target.value)}
              placeholder="Текст первого поста в канале..."
              rows={4}
            />
          </label>
          <button
            className="secondary-button"
            type="button"
            disabled={busy || !channelAccountId || !channelName.trim()}
            onClick={() => void handleCreateChannel()}
          >
            {busy ? "Создаём канал…" : "Создать и закрепить канал"}
          </button>
        </div>
      </section>

      {/* Create template modal */}
      {showCreateTemplate ? (
        <div className="modal-overlay" onClick={() => setShowCreateTemplate(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()} style={{ maxHeight: "80vh", overflowY: "auto" }}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Новый шаблон</div>
                <h2>Создать шаблон профиля</h2>
              </div>
            </div>
            <div className="stack-form">
              <label className="field">
                <span>Название шаблона</span>
                <input
                  value={tplName}
                  onChange={(e) => setTplName(e.target.value)}
                  placeholder="Например: Женский профиль RU"
                />
              </label>
              <div className="two-column-grid" style={{ gap: 12 }}>
                <label className="field">
                  <span>Пол</span>
                  <select value={tplGender} onChange={(e) => setTplGender(e.target.value)}>
                    {GENDER_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Гео</span>
                  <input
                    value={tplGeo}
                    onChange={(e) => setTplGeo(e.target.value)}
                    placeholder="Москва, Киев, Алматы..."
                  />
                </label>
              </div>
              <label className="field">
                <span>Шаблон биографии (до 70 символов)</span>
                <textarea
                  className="assistant-textarea"
                  value={tplBio}
                  onChange={(e) => setTplBio(e.target.value.slice(0, 70))}
                  placeholder="Блогер | Путешествую по {geo} | Пишу о {topic}"
                  rows={2}
                />
                <span className="muted" style={{ fontSize: 11 }}>{tplBio.length}/70</span>
              </label>
              <label className="field">
                <span>Шаблон названия канала</span>
                <input
                  value={tplChannelName}
                  onChange={(e) => setTplChannelName(e.target.value)}
                  placeholder="Канал {name} | {geo}"
                />
              </label>
              <label className="field">
                <span>Шаблон описания канала</span>
                <textarea
                  className="assistant-textarea"
                  value={tplChannelDesc}
                  onChange={(e) => setTplChannelDesc(e.target.value)}
                  placeholder="Личный канал о {topic}..."
                  rows={2}
                />
              </label>
              <label className="field">
                <span>Шаблон первого поста</span>
                <textarea
                  className="assistant-textarea"
                  value={tplFirstPost}
                  onChange={(e) => setTplFirstPost(e.target.value)}
                  placeholder="Привет! Меня зовут {name}..."
                  rows={3}
                />
              </label>
              <label className="field">
                <span>Стиль аватара</span>
                <select value={tplAvatarStyle} onChange={(e) => setTplAvatarStyle(e.target.value)}>
                  {AVATAR_STYLE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
              <div className="actions-row">
                <button
                  className="primary-button"
                  type="button"
                  disabled={busy}
                  onClick={() => void handleCreateTemplate()}
                >
                  {busy ? "Создаём…" : "Создать шаблон"}
                </button>
                <button className="ghost-button" type="button" onClick={() => setShowCreateTemplate(false)}>
                  Отмена
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
