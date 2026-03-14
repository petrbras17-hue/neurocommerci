import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "../../auth";
import { apiFetch } from "../../api";

interface InboxEntry {
  id: number;
  account_id: number;
  account_phone: string | null;
  peer_id: number;
  peer_name: string | null;
  peer_username: string | null;
  last_message_text: string | null;
  last_message_at: string | null;
  unread_count: number;
  is_auto_responding: boolean;
}

interface DmMsg {
  id: number;
  inbox_id: number;
  sender: "us" | "them";
  text: string;
  created_at: string | null;
}

interface AutoResponder {
  id: number;
  account_id: number | null;
  product_name: string | null;
  product_description: string | null;
  tone: string;
  max_responses_per_day: number;
  responses_today: number;
  is_active: boolean;
}

export function UnifiedInboxPage() {
  const { accessToken } = useAuth();
  const [inbox, setInbox] = useState<InboxEntry[]>([]);
  const [selectedEntry, setSelectedEntry] = useState<InboxEntry | null>(null);
  const [messages, setMessages] = useState<DmMsg[]>([]);
  const [messageText, setMessageText] = useState("");
  const [sending, setSending] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [autoResponders, setAutoResponders] = useState<AutoResponder[]>([]);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const fetchInbox = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: InboxEntry[] }>("/v1/admin/dialogs/inbox?limit=100", {
        accessToken,
      });
      setInbox(data.items);
    } catch (e) {
      console.error("fetch inbox:", e);
    }
  }, [accessToken]);

  const fetchAutoResponders = useCallback(async () => {
    try {
      const data = await apiFetch<{ items: AutoResponder[] }>("/v1/admin/auto-responder", {
        accessToken,
      });
      setAutoResponders(data.items);
    } catch (e) {
      console.error("fetch auto-responders:", e);
    }
  }, [accessToken]);

  useEffect(() => {
    void fetchInbox();
    void fetchAutoResponders();
  }, [fetchInbox, fetchAutoResponders]);

  const fetchConversation = useCallback(
    async (accountId: number, peerId: number) => {
      try {
        const data = await apiFetch<{ items: DmMsg[] }>(
          `/v1/admin/dialogs/inbox/${accountId}/${peerId}?limit=200`,
          { accessToken }
        );
        setMessages(data.items);
        setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
      } catch (e) {
        console.error("fetch conversation:", e);
      }
    },
    [accessToken]
  );

  const handleSelectEntry = (entry: InboxEntry) => {
    setSelectedEntry(entry);
    void fetchConversation(entry.account_id, entry.peer_id);
  };

  const handleSend = async () => {
    if (!selectedEntry || !messageText.trim()) return;
    setSending(true);
    try {
      await apiFetch(
        `/v1/admin/dialogs/inbox/${selectedEntry.account_id}/${selectedEntry.peer_id}/send`,
        { method: "POST", json: { text: messageText }, accessToken }
      );
      setMessageText("");
      await fetchConversation(selectedEntry.account_id, selectedEntry.peer_id);
    } catch (e) {
      console.error("send dm:", e);
    } finally {
      setSending(false);
    }
  };

  const handleSync = async (accountId: number) => {
    setSyncing(true);
    try {
      await apiFetch(`/v1/admin/dialogs/inbox/${accountId}/sync`, {
        method: "POST",
        accessToken,
      });
      await fetchInbox();
      if (selectedEntry && selectedEntry.account_id === accountId) {
        await fetchConversation(selectedEntry.account_id, selectedEntry.peer_id);
      }
    } catch (e) {
      console.error("sync:", e);
    } finally {
      setSyncing(false);
    }
  };

  const handleToggleAutoResponder = async (accountId: number) => {
    const existing = autoResponders.find((r) => r.account_id === accountId);
    if (existing) {
      try {
        await apiFetch(`/v1/admin/auto-responder/${existing.id}`, {
          method: "PUT",
          json: { is_active: !existing.is_active },
          accessToken,
        });
        await fetchAutoResponders();
      } catch (e) {
        console.error("toggle auto-responder:", e);
      }
    } else {
      try {
        await apiFetch("/v1/admin/auto-responder", {
          method: "POST",
          json: { account_id: accountId, is_active: true },
          accessToken,
        });
        await fetchAutoResponders();
      } catch (e) {
        console.error("create auto-responder:", e);
      }
    }
  };

  // Group inbox by account_id
  const accountIds = [...new Set(inbox.map((i) => i.account_id))];
  const isAutoResponding = (accountId: number) =>
    autoResponders.some((r) => r.account_id === accountId && r.is_active);

  return (
    <div style={{ padding: 24, height: "calc(100vh - 120px)", display: "flex", flexDirection: "column" }}>
      <h1 style={{ color: "#00ff88", marginBottom: 16, fontSize: 22, flexShrink: 0 }}>
        Unified Inbox
      </h1>

      <div style={{ display: "flex", flex: 1, gap: 16, minHeight: 0 }}>
        {/* Left: Account selector */}
        <div
          style={{
            width: 200,
            flexShrink: 0,
            background: "#111113",
            borderRadius: 8,
            border: "1px solid #222",
            overflowY: "auto",
            padding: 8,
          }}
        >
          <div style={{ color: "#888", fontSize: 11, padding: "4px 8px", marginBottom: 4 }}>
            ACCOUNTS
          </div>
          {accountIds.map((aid) => {
            const entries = inbox.filter((i) => i.account_id === aid);
            const totalUnread = entries.reduce((s, e) => s + e.unread_count, 0);
            const phone = entries[0]?.account_phone || `#${aid}`;
            return (
              <div
                key={aid}
                style={{
                  padding: "8px 10px",
                  borderRadius: 6,
                  cursor: "pointer",
                  background:
                    selectedEntry && selectedEntry.account_id === aid ? "#1a2a1a" : "transparent",
                  marginBottom: 2,
                }}
                onClick={() => {
                  const first = entries[0];
                  if (first) handleSelectEntry(first);
                }}
              >
                <div style={{ color: "#eee", fontSize: 13 }}>{phone}</div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ color: "#666", fontSize: 11 }}>{entries.length} chats</span>
                  {totalUnread > 0 && (
                    <span
                      style={{
                        background: "#00ff88",
                        color: "#0a0a0b",
                        borderRadius: 10,
                        padding: "1px 6px",
                        fontSize: 10,
                        fontWeight: 700,
                      }}
                    >
                      {totalUnread}
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleSync(aid);
                    }}
                    disabled={syncing}
                    style={{
                      fontSize: 10,
                      padding: "2px 6px",
                      background: "#1a1a1d",
                      color: "#888",
                      border: "1px solid #333",
                      borderRadius: 3,
                      cursor: "pointer",
                    }}
                  >
                    Sync
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleToggleAutoResponder(aid);
                    }}
                    style={{
                      fontSize: 10,
                      padding: "2px 6px",
                      background: isAutoResponding(aid) ? "#00ff8833" : "#1a1a1d",
                      color: isAutoResponding(aid) ? "#00ff88" : "#888",
                      border: `1px solid ${isAutoResponding(aid) ? "#00ff88" : "#333"}`,
                      borderRadius: 3,
                      cursor: "pointer",
                    }}
                  >
                    Auto
                  </button>
                </div>
              </div>
            );
          })}
          {accountIds.length === 0 && (
            <p style={{ color: "#555", fontSize: 12, padding: 8 }}>No DMs yet.</p>
          )}
        </div>

        {/* Middle: Conversation list */}
        <div
          style={{
            width: 260,
            flexShrink: 0,
            background: "#111113",
            borderRadius: 8,
            border: "1px solid #222",
            overflowY: "auto",
            padding: 8,
          }}
        >
          <div style={{ color: "#888", fontSize: 11, padding: "4px 8px", marginBottom: 4 }}>
            CONVERSATIONS
          </div>
          {inbox
            .filter(
              (i) =>
                !selectedEntry || i.account_id === selectedEntry.account_id
            )
            .map((entry) => (
              <div
                key={entry.id}
                onClick={() => handleSelectEntry(entry)}
                style={{
                  padding: "8px 10px",
                  borderRadius: 6,
                  cursor: "pointer",
                  background:
                    selectedEntry?.id === entry.id ? "#1a2a1a" : "transparent",
                  borderLeft:
                    selectedEntry?.id === entry.id ? "2px solid #00ff88" : "2px solid transparent",
                  marginBottom: 2,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "#eee", fontSize: 13 }}>
                    {entry.peer_name || entry.peer_username || `User ${entry.peer_id}`}
                  </span>
                  {entry.unread_count > 0 && (
                    <span
                      style={{
                        background: "#00ff88",
                        color: "#0a0a0b",
                        borderRadius: 10,
                        padding: "1px 6px",
                        fontSize: 10,
                        fontWeight: 700,
                      }}
                    >
                      {entry.unread_count}
                    </span>
                  )}
                </div>
                <div
                  style={{
                    color: "#666",
                    fontSize: 11,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    maxWidth: 220,
                  }}
                >
                  {entry.last_message_text || "..."}
                </div>
                {entry.last_message_at && (
                  <div style={{ color: "#444", fontSize: 10 }}>
                    {new Date(entry.last_message_at).toLocaleString()}
                  </div>
                )}
              </div>
            ))}
        </div>

        {/* Right: Chat view */}
        <div
          style={{
            flex: 1,
            background: "#111113",
            borderRadius: 8,
            border: "1px solid #222",
            display: "flex",
            flexDirection: "column",
            minWidth: 0,
          }}
        >
          {selectedEntry ? (
            <>
              {/* Header */}
              <div
                style={{
                  padding: "12px 16px",
                  borderBottom: "1px solid #222",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <strong style={{ color: "#eee" }}>
                    {selectedEntry.peer_name || selectedEntry.peer_username || `User ${selectedEntry.peer_id}`}
                  </strong>
                  {selectedEntry.peer_username && (
                    <span style={{ color: "#666", fontSize: 12, marginLeft: 8 }}>
                      @{selectedEntry.peer_username}
                    </span>
                  )}
                </div>
                <span style={{ color: "#555", fontSize: 11 }}>
                  via {selectedEntry.account_phone || `Account #${selectedEntry.account_id}`}
                </span>
              </div>

              {/* Messages */}
              <div
                style={{
                  flex: 1,
                  overflowY: "auto",
                  padding: 16,
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                {messages.map((m) => (
                  <div
                    key={m.id}
                    style={{
                      alignSelf: m.sender === "us" ? "flex-end" : "flex-start",
                      maxWidth: "70%",
                    }}
                  >
                    <div
                      style={{
                        background: m.sender === "us" ? "#00ff8822" : "#1a1a1d",
                        border: `1px solid ${m.sender === "us" ? "#00ff8844" : "#333"}`,
                        borderRadius: 12,
                        padding: "8px 12px",
                        color: "#eee",
                        fontSize: 13,
                        lineHeight: 1.4,
                      }}
                    >
                      {m.text}
                    </div>
                    <div
                      style={{
                        color: "#555",
                        fontSize: 10,
                        marginTop: 2,
                        textAlign: m.sender === "us" ? "right" : "left",
                      }}
                    >
                      {m.created_at ? new Date(m.created_at).toLocaleTimeString() : ""}
                    </div>
                  </div>
                ))}
                <div ref={chatEndRef} />
              </div>

              {/* Input */}
              <div
                style={{
                  padding: 12,
                  borderTop: "1px solid #222",
                  display: "flex",
                  gap: 8,
                }}
              >
                <input
                  value={messageText}
                  onChange={(e) => setMessageText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void handleSend();
                    }
                  }}
                  placeholder="Type a message..."
                  style={{
                    flex: 1,
                    padding: "8px 12px",
                    background: "#0a0a0b",
                    border: "1px solid #333",
                    borderRadius: 8,
                    color: "#eee",
                    fontSize: 13,
                  }}
                />
                <button
                  onClick={() => void handleSend()}
                  disabled={sending || !messageText.trim()}
                  style={{
                    padding: "8px 20px",
                    background: "#00ff88",
                    color: "#0a0a0b",
                    border: "none",
                    borderRadius: 8,
                    fontWeight: 700,
                    cursor: "pointer",
                    opacity: sending || !messageText.trim() ? 0.5 : 1,
                  }}
                >
                  Send
                </button>
              </div>
            </>
          ) : (
            <div
              style={{
                flex: 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#555",
              }}
            >
              Select a conversation to view messages
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
