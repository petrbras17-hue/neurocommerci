import { useEffect, useState } from "react";
import { apiFetch, foldersApi, TelegramFolder } from "../api";
import { useAuth } from "../auth";

type AccountRow = { id: number; phone: string; status: string; health_status: string };

function statusBadgeClass(status: string): string {
  if (status === "active" || status === "created") return "badge-green";
  if (status === "pending") return "badge-yellow";
  if (status === "error" || status === "failed") return "badge-red";
  return "badge-gray";
}

export function FoldersPage() {
  const { accessToken } = useAuth();
  const [folders, setFolders] = useState<TelegramFolder[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [showCreateModal, setShowCreateModal] = useState(false);

  // form state
  const [folderName, setFolderName] = useState("");
  const [accountId, setAccountId] = useState<number | "">("");
  const [channelUsernames, setChannelUsernames] = useState("");

  const loadFolders = async () => {
    if (!accessToken) return;
    try {
      const payload = await foldersApi.list(accessToken);
      setFolders(payload.items);
    } catch {
      // silent
    }
  };

  const loadAccounts = async () => {
    if (!accessToken) return;
    try {
      const payload = await apiFetch<{ items: AccountRow[]; total: number }>("/v1/web/accounts", { accessToken });
      setAccounts(payload.items);
    } catch {
      // silent
    }
  };

  useEffect(() => { void Promise.all([loadFolders(), loadAccounts()]).catch(() => {}); }, [accessToken]);

  const handleCreate = async () => {
    if (!accessToken) return;
    if (!folderName.trim()) { setStatusMessage("Введите название папки."); return; }
    if (accountId === "") { setStatusMessage("Выберите аккаунт."); return; }
    const channels = channelUsernames.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!channels.length) { setStatusMessage("Добавьте хотя бы один канал."); return; }
    setBusy(true);
    setStatusMessage("");
    try {
      await foldersApi.create(accessToken, {
        account_id: Number(accountId),
        folder_name: folderName.trim(),
        channel_usernames: channels,
      });
      setShowCreateModal(false);
      setFolderName("");
      setAccountId("");
      setChannelUsernames("");
      setStatusMessage("Папка создана.");
      await loadFolders();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "create_folder_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!accessToken) return;
    setBusy(true);
    setStatusMessage("");
    try {
      await foldersApi.delete(accessToken, id);
      setStatusMessage("Папка удалена.");
      await loadFolders();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "delete_folder_failed");
    } finally {
      setBusy(false);
    }
  };

  const handleCopyInvite = async (folder: TelegramFolder) => {
    if (!accessToken) return;
    try {
      let link = folder.invite_link;
      if (!link) {
        const res = await foldersApi.getInvite(accessToken, folder.id);
        link = res.invite_link;
      }
      if (link) {
        await navigator.clipboard.writeText(link);
        setStatusMessage("Ссылка-приглашение скопирована в буфер обмена.");
      } else {
        setStatusMessage("Ссылка-приглашение ещё не создана.");
      }
    } catch {
      setStatusMessage("Не удалось скопировать ссылку.");
    }
  };

  const phoneById = (id: number) => accounts.find((a) => a.id === id)?.phone ?? `#${id}`;

  return (
    <div className="page-grid">
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Folder Manager</div>
              <h2>Менеджер папок</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Папка — это коллекция каналов в Telegram для конкретного аккаунта.</li>
            <li>Можно создать ссылку-приглашение для поделиться набором каналов.</li>
            <li>Управляйте папками разных аккаунтов из одного интерфейса.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статус</div>
              <h2>Папки</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block"><strong>Всего папок</strong><span>{folders.length}</span></div>
            <div className="info-block"><strong>Активных</strong><span>{folders.filter((f) => f.status === "active" || f.status === "created").length}</span></div>
            <div className="info-block">
              <strong>Каналов всего</strong>
              <span>{folders.reduce((acc, f) => acc + f.channel_usernames.length, 0)}</span>
            </div>
            <div className="info-block">
              <strong>Со ссылкой</strong>
              <span>{folders.filter((f) => f.invite_link).length}</span>
            </div>
          </div>
        </article>
      </section>

      {statusMessage ? <div className="status-banner">{statusMessage}</div> : null}

      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Список папок</div>
            <h2>Ваши папки</h2>
          </div>
          <div className="badge-row">
            <button className="primary-button" type="button" onClick={() => setShowCreateModal(true)}>
              + Создать папку
            </button>
          </div>
        </div>
        {folders.length ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Название</th>
                  <th>Аккаунт</th>
                  <th>Каналов</th>
                  <th>Статус</th>
                  <th>Ссылка-приглашение</th>
                  <th>Создано</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {folders.map((folder) => (
                  <tr key={folder.id}>
                    <td><strong>{folder.folder_name}</strong></td>
                    <td>{phoneById(folder.account_id)}</td>
                    <td>{folder.channel_usernames.length}</td>
                    <td><span className={`pill ${statusBadgeClass(folder.status)}`}>{folder.status}</span></td>
                    <td>
                      {folder.invite_link ? (
                        <span className="muted" style={{ fontSize: 12, wordBreak: "break-all" }}>
                          {folder.invite_link.length > 40 ? folder.invite_link.slice(0, 40) + "…" : folder.invite_link}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>{folder.created_at ?? "—"}</td>
                    <td>
                      <div className="badge-row" style={{ gap: 6 }}>
                        <button
                          className="secondary-button"
                          type="button"
                          disabled={busy}
                          onClick={() => void handleCopyInvite(folder)}
                          style={{ fontSize: 12 }}
                        >
                          Копировать ссылку
                        </button>
                        <button
                          className="ghost-button"
                          type="button"
                          disabled={busy}
                          onClick={() => void handleDelete(folder.id)}
                          style={{ fontSize: 12 }}
                        >
                          Удалить
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">Нет папок. Создайте первую папку для организации каналов.</p>
        )}
      </section>

      {showCreateModal ? (
        <div className="modal-overlay" onClick={() => setShowCreateModal(false)}>
          <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-header">
              <div>
                <div className="eyebrow">Новая папка</div>
                <h2>Создать папку</h2>
              </div>
            </div>
            <div className="stack-form">
              <label className="field">
                <span>Название папки</span>
                <input
                  value={folderName}
                  onChange={(e) => setFolderName(e.target.value)}
                  placeholder="Например: Топ-каналы по маркетингу"
                />
              </label>
              <label className="field">
                <span>Аккаунт</span>
                <select value={accountId} onChange={(e) => setAccountId(e.target.value === "" ? "" : Number(e.target.value))}>
                  <option value="">— выберите аккаунт —</option>
                  {accounts.map((a) => <option key={a.id} value={a.id}>{a.phone}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Каналы (каждый с новой строки)</span>
                <textarea
                  className="assistant-textarea"
                  value={channelUsernames}
                  onChange={(e) => setChannelUsernames(e.target.value)}
                  placeholder="@channel1&#10;@channel2&#10;@channel3"
                  rows={6}
                />
              </label>
              <div className="actions-row">
                <button className="primary-button" type="button" disabled={busy} onClick={() => void handleCreate()}>
                  {busy ? "Создаём…" : "Создать папку"}
                </button>
                <button className="ghost-button" type="button" onClick={() => setShowCreateModal(false)}>
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
