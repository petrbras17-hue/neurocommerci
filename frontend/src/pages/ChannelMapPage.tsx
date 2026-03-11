import { useEffect, useState } from "react";
import { channelMapApi, ChannelMapEntry } from "../api";
import { useAuth } from "../auth";

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function ChannelMapPage() {
  const { accessToken } = useAuth();

  const [items, setItems] = useState<ChannelMapEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [categories, setCategories] = useState<string[]>([]);
  const [stats, setStats] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Filter state
  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("");
  const [selectedLanguage, setSelectedLanguage] = useState("");
  const [minMembers, setMinMembers] = useState(0);

  const LANGUAGE_OPTIONS = [
    { value: "", label: "Все языки" },
    { value: "ru", label: "Русский" },
    { value: "en", label: "English" },
    { value: "uk", label: "Українська" },
    { value: "kz", label: "Қазақша" },
  ];

  const loadCategories = async () => {
    if (!accessToken) return;
    try {
      const payload = await channelMapApi.categories(accessToken);
      setCategories(payload.categories ?? []);
    } catch {
      // categories optional
    }
  };

  const loadStats = async () => {
    if (!accessToken) return;
    try {
      const payload = await channelMapApi.stats(accessToken);
      setStats(payload);
    } catch {
      // stats optional
    }
  };

  const doSearch = async () => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      const payload = await channelMapApi.search(accessToken, {
        query: query.trim() || undefined,
        category: selectedCategory || undefined,
        language: selectedLanguage || undefined,
        min_members: minMembers > 0 ? minMembers : undefined,
        limit: 200,
      });
      setItems(payload.items);
      setTotal(payload.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "search_failed");
    } finally {
      setBusy(false);
    }
  };

  const loadAll = async () => {
    if (!accessToken) return;
    setBusy(true);
    setError("");
    try {
      const payload = await channelMapApi.list(accessToken, {
        category: selectedCategory || undefined,
        language: selectedLanguage || undefined,
        min_members: minMembers > 0 ? minMembers : undefined,
      });
      setItems(payload.items);
      setTotal(payload.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load_failed");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void Promise.all([loadAll(), loadCategories(), loadStats()]).catch(() => {});
  }, [accessToken]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      void doSearch();
    } else {
      void loadAll();
    }
  };

  const applyFilter = (cat: string) => {
    setSelectedCategory(cat);
  };

  useEffect(() => {
    if (!accessToken) return;
    if (query.trim()) {
      void doSearch();
    } else {
      void loadAll();
    }
  }, [selectedCategory, selectedLanguage, minMembers]);

  const totalIndexed = typeof stats.total === "number" ? stats.total : total;
  const byCategory = (stats.by_category as Record<string, number> | undefined) ?? {};

  return (
    <div className="page-grid">
      {/* Header */}
      <section className="two-column-grid">
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Channel Intelligence</div>
              <h2>Карта каналов</h2>
            </div>
          </div>
          <ul className="bullet-list">
            <li>Индексированные Telegram-каналы с метриками охвата и вовлечённости.</li>
            <li>Используйте фильтры для поиска целевых площадок под кампании.</li>
            <li>Каналы с активными комментариями помечены отдельно.</li>
          </ul>
        </article>
        <article className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Статистика индекса</div>
              <h2>Охват базы</h2>
            </div>
          </div>
          <div className="status-grid">
            <div className="info-block">
              <strong>Всего каналов</strong>
              <span>{formatNumber(totalIndexed)}</span>
            </div>
            <div className="info-block">
              <strong>Категорий</strong>
              <span>{categories.length || Object.keys(byCategory).length}</span>
            </div>
            <div className="info-block">
              <strong>Найдено</strong>
              <span>{total}</span>
            </div>
            <div className="info-block">
              <strong>С комментариями</strong>
              <span>{items.filter((i) => i.has_comments).length}</span>
            </div>
          </div>
        </article>
      </section>

      {/* Category pills */}
      {categories.length > 0 && (
        <section className="panel wide">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Фильтр по категории</div>
              <h2>Категории</h2>
            </div>
          </div>
          <div className="badge-row" style={{ flexWrap: "wrap", gap: 8 }}>
            <button
              type="button"
              className={selectedCategory === "" ? "pill badge-green" : "pill badge-gray"}
              onClick={() => applyFilter("")}
            >
              Все
            </button>
            {categories.map((cat) => (
              <button
                key={cat}
                type="button"
                className={selectedCategory === cat ? "pill badge-green" : "pill badge-gray"}
                onClick={() => applyFilter(cat)}
              >
                {cat}
                {byCategory[cat] ? ` (${byCategory[cat]})` : ""}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* Search form */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Поиск</div>
            <h2>Найти каналы</h2>
          </div>
        </div>
        <form className="stack-form" onSubmit={handleSearch}>
          <div className="two-column-grid" style={{ gap: 12 }}>
            <label className="field">
              <span>Ключевое слово</span>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Например: маркетинг, крипта, e-commerce..."
              />
            </label>
            <label className="field">
              <span>Язык</span>
              <select value={selectedLanguage} onChange={(e) => setSelectedLanguage(e.target.value)}>
                {LANGUAGE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="field">
            <span>Минимум подписчиков: {minMembers > 0 ? formatNumber(minMembers) : "не задано"}</span>
            <input
              type="range"
              min={0}
              max={100000}
              step={1000}
              value={minMembers}
              onChange={(e) => setMinMembers(Number(e.target.value))}
            />
          </label>
          <div className="actions-row">
            <button className="primary-button" type="submit" disabled={busy}>
              {busy ? "Ищем…" : "Найти"}
            </button>
            <button
              className="ghost-button"
              type="button"
              disabled={busy}
              onClick={() => {
                setQuery("");
                setSelectedCategory("");
                setSelectedLanguage("");
                setMinMembers(0);
                void loadAll();
              }}
            >
              Сбросить
            </button>
          </div>
        </form>
      </section>

      {error ? <div className="status-banner">{error}</div> : null}

      {/* Results table */}
      <section className="panel wide">
        <div className="panel-header">
          <div>
            <div className="eyebrow">Результаты</div>
            <h2>Каналы {total > 0 ? `(${total})` : ""}</h2>
          </div>
          <div className="badge-row">
            <button className="ghost-button" type="button" disabled={busy} onClick={() => void loadAll()}>
              Обновить
            </button>
          </div>
        </div>
        {items.length ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Канал</th>
                  <th>Категория</th>
                  <th>Язык</th>
                  <th>Подписчики</th>
                  <th>Комментарии</th>
                  <th>Охват</th>
                  <th>ER%</th>
                  <th>Обновлён</th>
                </tr>
              </thead>
              <tbody>
                {items.map((ch) => (
                  <tr key={ch.id}>
                    <td>
                      <div>
                        <strong>
                          {ch.username ? (
                            <a
                              href={`https://t.me/${ch.username}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{ color: "inherit" }}
                            >
                              @{ch.username}
                            </a>
                          ) : (
                            `#${ch.id}`
                          )}
                        </strong>
                        {ch.title ? (
                          <div className="muted" style={{ fontSize: 11 }}>{ch.title}</div>
                        ) : null}
                      </div>
                    </td>
                    <td>{ch.category ?? "—"}{ch.subcategory ? ` / ${ch.subcategory}` : ""}</td>
                    <td>{ch.language ?? "—"}</td>
                    <td>{formatNumber(ch.member_count)}</td>
                    <td>
                      <span className={`pill ${ch.has_comments ? "badge-green" : "badge-gray"}`}>
                        {ch.has_comments ? "Есть" : "Нет"}
                      </span>
                    </td>
                    <td>{ch.avg_post_reach != null ? formatNumber(ch.avg_post_reach) : "—"}</td>
                    <td>
                      {ch.engagement_rate != null ? (
                        <span>{(ch.engagement_rate * 100).toFixed(2)}%</span>
                      ) : "—"}
                    </td>
                    <td className="muted" style={{ fontSize: 11 }}>
                      {ch.last_indexed_at ? ch.last_indexed_at.slice(0, 10) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : busy ? (
          <p className="muted">Загружаем…</p>
        ) : (
          <p className="muted">Каналы не найдены. Попробуйте изменить фильтры или запустите индексирование через парсер.</p>
        )}
      </section>

      {/* Stats breakdown by category */}
      {Object.keys(byCategory).length > 0 && (
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="eyebrow">Распределение</div>
              <h2>По категориям</h2>
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {Object.entries(byCategory)
              .sort(([, a], [, b]) => b - a)
              .map(([cat, count]) => {
                const maxVal = Math.max(...Object.values(byCategory));
                const pct = maxVal > 0 ? (count / maxVal) * 100 : 0;
                return (
                  <div key={cat} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ minWidth: 140, fontSize: 13 }}>{cat}</span>
                    <div style={{ flex: 1, height: 8, background: "#2d2d2d", borderRadius: 4, overflow: "hidden" }}>
                      <div style={{ width: `${pct}%`, height: "100%", background: "#6366f1", borderRadius: 4 }} />
                    </div>
                    <span className="muted" style={{ minWidth: 40, textAlign: "right", fontSize: 12 }}>{count}</span>
                  </div>
                );
              })}
          </div>
        </section>
      )}
    </div>
  );
}
