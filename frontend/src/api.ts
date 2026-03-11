export type ApiOptions = {
  method?: string;
  headers?: Record<string, string>;
  body?: BodyInit | null;
  json?: unknown;
  accessToken?: string | null;
};

export type JobStatusResponse = {
  id: number;
  job_type: string;
  status: "queued" | "running" | "succeeded" | "failed";
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  result_summary: Record<string, unknown>;
};

export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers || {}) };
  let body = options.body ?? null;

  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }

  if (options.accessToken) {
    headers.Authorization = `Bearer ${options.accessToken}`;
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body,
    credentials: "include"
  });

  if (!response.ok) {
    let detail = `http_${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || payload.error || detail;
    } catch {
      // Keep default detail.
    }
    throw new Error(detail);
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  return (await response.text()) as T;
}

// ─── Farm Orchestrator API ───────────────────────────────────────────────────

export type FarmStatus = "stopped" | "running" | "paused";
export type ThreadStatus = "idle" | "subscribing" | "monitoring" | "commenting" | "cooldown" | "quarantine" | "error" | "stopped";
export type EventSeverity = "info" | "warn" | "error";

export type FarmConfig = {
  id: number;
  name: string;
  status: FarmStatus;
  mode: string;
  max_threads: number;
  comment_prompt: string | null;
  comment_tone: string;
  comment_language: string;
  comment_percentage: number;
  delay_before_comment_min: number;
  delay_before_comment_max: number;
  ai_protection_mode: string;
  auto_responder_enabled: boolean;
  auto_responder_prompt: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type FarmThread = {
  id: number;
  farm_id: number;
  account_id: number;
  account_phone: string | null;
  thread_index: number;
  status: ThreadStatus;
  assigned_channels: unknown[];
  stats_comments_sent: number;
  stats_comments_failed: number;
  stats_last_comment_at: string | null;
  stats_last_error: string | null;
  health_score: number;
  quarantine_until: string | null;
  started_at: string | null;
  updated_at: string | null;
};

export type FarmEvent = {
  id: number;
  farm_id: number;
  thread_id: number | null;
  event_type: string;
  severity: EventSeverity;
  message: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
};

export type ChannelDatabase = {
  id: number;
  name: string;
  source: string;
  status: string;
  channel_count?: number;
  created_at: string | null;
};

export type ChannelEntry = {
  id: number;
  database_id: number;
  telegram_id: number | null;
  username: string | null;
  title: string | null;
  member_count: number | null;
  has_comments: boolean;
  language: string | null;
  category: string | null;
  blacklisted: boolean;
  success_rate: number | null;
  created_at: string | null;
};

export type ParsingJob = {
  id: number;
  job_type: string;
  status: string;
  keywords: string[];
  filters: Record<string, unknown>;
  max_results: number;
  results_count: number;
  target_database_id: number | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  created_at: string | null;
};

export type ProfileTemplate = {
  id: number;
  name: string;
  gender: string | null;
  geo: string | null;
  bio_template: string | null;
  channel_name_template: string | null;
  channel_description_template: string | null;
  channel_first_post_template: string | null;
  avatar_style: string | null;
  created_at: string | null;
};

export const farmApi = {
  list: (token: string) =>
    apiFetch<{ items: FarmConfig[]; total: number }>("/v1/farm", { accessToken: token }),

  get: (token: string, id: number) =>
    apiFetch<FarmConfig>(`/v1/farm/${id}`, { accessToken: token }),

  create: (token: string, data: Partial<FarmConfig>) =>
    apiFetch<FarmConfig>("/v1/farm", { method: "POST", accessToken: token, json: data }),

  update: (token: string, id: number, data: Partial<FarmConfig>) =>
    apiFetch<FarmConfig>(`/v1/farm/${id}`, { method: "PUT", accessToken: token, json: data }),

  delete: (token: string, id: number) =>
    apiFetch<void>(`/v1/farm/${id}`, { method: "DELETE", accessToken: token }),

  start: (token: string, id: number, payload: { account_ids: number[]; channel_database_id: number }) =>
    apiFetch<{ status: string }>(`/v1/farm/${id}/start`, { method: "POST", accessToken: token, json: payload }),

  stop: (token: string, id: number) =>
    apiFetch<{ status: string }>(`/v1/farm/${id}/stop`, { method: "POST", accessToken: token }),

  pause: (token: string, id: number) =>
    apiFetch<{ status: string }>(`/v1/farm/${id}/pause`, { method: "POST", accessToken: token }),

  resume: (token: string, id: number) =>
    apiFetch<{ status: string }>(`/v1/farm/${id}/resume`, { method: "POST", accessToken: token }),

  getThreads: (token: string, id: number) =>
    apiFetch<{ items: FarmThread[]; total: number }>(`/v1/farm/${id}/threads`, { accessToken: token }),

  getEvents: (token: string, id: number, limit = 50) =>
    apiFetch<{ items: FarmEvent[]; total: number }>(`/v1/farm/${id}/events?limit=${limit}`, { accessToken: token }),
};

export const channelDbApi = {
  list: (token: string) =>
    apiFetch<{ items: ChannelDatabase[]; total: number }>("/v1/channel-db", { accessToken: token }),

  get: (token: string, id: number) =>
    apiFetch<{ database: ChannelDatabase; channels: ChannelEntry[]; total: number }>(`/v1/channel-db/${id}`, { accessToken: token }),

  create: (token: string, name: string) =>
    apiFetch<ChannelDatabase>("/v1/channel-db", { method: "POST", accessToken: token, json: { name } }),

  importChannels: (token: string, id: number, links: string[]) =>
    apiFetch<{ imported: number; skipped: number }>(`/v1/channel-db/${id}/import`, {
      method: "POST",
      accessToken: token,
      json: { links },
    }),

  blacklistChannel: (token: string, dbId: number, channelId: number, blacklisted: boolean) =>
    apiFetch<void>(`/v1/channel-db/${dbId}/channels/${channelId}/blacklist`, {
      method: "POST",
      accessToken: token,
      json: { blacklisted },
    }),

  deleteChannel: (token: string, dbId: number, channelId: number) =>
    apiFetch<void>(`/v1/channel-db/${dbId}/channels/${channelId}`, {
      method: "DELETE",
      accessToken: token,
    }),
};

export const parserApi = {
  startChannelParsing: (
    token: string,
    payload: {
      keywords: string[];
      filters: Record<string, unknown>;
      max_results: number;
      account_id: number | null;
      target_database_id: number | null;
    }
  ) => apiFetch<ParsingJob>("/v1/parser/channels", { method: "POST", accessToken: token, json: payload }),

  listJobs: (token: string) =>
    apiFetch<{ items: ParsingJob[]; total: number }>("/v1/parser/jobs", { accessToken: token }),

  getJob: (token: string, id: number) =>
    apiFetch<ParsingJob>(`/v1/parser/jobs/${id}`, { accessToken: token }),
};

export const profileApi = {
  listTemplates: (token: string) =>
    apiFetch<{ items: ProfileTemplate[]; total: number }>("/v1/profiles/templates", { accessToken: token }),

  createTemplate: (token: string, data: Partial<ProfileTemplate>) =>
    apiFetch<ProfileTemplate>("/v1/profiles/templates", { method: "POST", accessToken: token, json: data }),

  generate: (token: string, accountId: number, templateId: number) =>
    apiFetch<{ job_id: number; status: string }>(`/v1/profiles/generate`, {
      method: "POST",
      accessToken: token,
      json: { account_id: accountId, template_id: templateId },
    }),

  massGenerate: (token: string, accountIds: number[], templateId: number) =>
    apiFetch<{ job_id: number; status: string }>("/v1/profiles/mass-generate", {
      method: "POST",
      accessToken: token,
      json: { account_ids: accountIds, template_id: templateId },
    }),

  apply: (token: string, accountId: number) =>
    apiFetch<{ status: string }>(`/v1/profiles/apply/${accountId}`, {
      method: "POST",
      accessToken: token,
    }),

  createChannel: (
    token: string,
    accountId: number,
    data: { name: string; description: string; first_post: string }
  ) =>
    apiFetch<{ status: string; channel_id?: number }>(`/v1/profiles/create-channel/${accountId}`, {
      method: "POST",
      accessToken: token,
      json: data,
    }),
};

// ─── Warmup API ───────────────────────────────────────────────────────────────

export type WarmupMode = "conservative" | "moderate" | "aggressive";
export type WarmupStatus = "stopped" | "running";
export type WarmupSessionStatus = "pending" | "running" | "completed" | "failed";

export type WarmupConfig = {
  id: number;
  name: string;
  mode: WarmupMode;
  status: WarmupStatus;
  account_count: number;
  safety_limit_per_hour: number;
  active_hours_start: string;
  active_hours_end: string;
  session_duration_minutes: number;
  interval_between_sessions_hours: number;
  enable_reactions: boolean;
  enable_read_channels: boolean;
  enable_dialogs: boolean;
  target_channels: string[];
  created_at: string | null;
  updated_at: string | null;
};

export type WarmupSession = {
  id: number;
  config_id: number;
  account_id: number;
  account_phone: string | null;
  status: WarmupSessionStatus;
  actions_performed: number;
  started_at: string | null;
  completed_at: string | null;
  next_session_at: string | null;
};

export const warmupApi = {
  list: (token: string) =>
    apiFetch<{ items: WarmupConfig[]; total: number }>("/v1/warmup", { accessToken: token }),

  get: (token: string, id: number) =>
    apiFetch<WarmupConfig>(`/v1/warmup/${id}`, { accessToken: token }),

  create: (token: string, data: Partial<WarmupConfig>) =>
    apiFetch<WarmupConfig>("/v1/warmup", { method: "POST", accessToken: token, json: data }),

  update: (token: string, id: number, data: Partial<WarmupConfig>) =>
    apiFetch<WarmupConfig>(`/v1/warmup/${id}`, { method: "PUT", accessToken: token, json: data }),

  delete: (token: string, id: number) =>
    apiFetch<void>(`/v1/warmup/${id}`, { method: "DELETE", accessToken: token }),

  start: (token: string, id: number) =>
    apiFetch<{ status: string }>(`/v1/warmup/${id}/start`, { method: "POST", accessToken: token }),

  stop: (token: string, id: number) =>
    apiFetch<{ status: string }>(`/v1/warmup/${id}/stop`, { method: "POST", accessToken: token }),

  getSessions: (token: string, id: number) =>
    apiFetch<{ items: WarmupSession[]; total: number }>(`/v1/warmup/${id}/sessions`, { accessToken: token }),
};

// ─── Health API ───────────────────────────────────────────────────────────────

export type AccountHealthScore = {
  account_id: number;
  account_phone: string | null;
  health_score: number;
  survivability_score: number;
  flood_wait_count: number;
  spam_block_count: number;
  successful_actions: number;
  factors: Record<string, number>;
  recent_events: Array<{
    event_type: string;
    severity: string;
    message: string | null;
    created_at: string | null;
  }>;
  calculated_at: string | null;
};

export type QuarantinedAccount = {
  account_id: number;
  account_phone: string | null;
  quarantine_reason: string | null;
  quarantine_until: string | null;
};

export const healthApi = {
  listScores: (token: string) =>
    apiFetch<{ items: AccountHealthScore[]; total: number }>("/v1/health/scores", { accessToken: token }),

  getScore: (token: string, accountId: number) =>
    apiFetch<AccountHealthScore>(`/v1/health/scores/${accountId}`, { accessToken: token }),

  recalculate: (token: string) =>
    apiFetch<{ status: string }>("/v1/health/recalculate", { method: "POST", accessToken: token }),
};

export const quarantineApi = {
  list: (token: string) =>
    apiFetch<{ items: QuarantinedAccount[]; total: number }>("/v1/health/quarantine", { accessToken: token }),

  liftQuarantine: (token: string, accountId: number) =>
    apiFetch<{ status: string }>(`/v1/health/quarantine/${accountId}/lift`, { method: "POST", accessToken: token }),
};

// --- Mass Reactions API ---
export type ReactionJob = {
  id: number; channel_username: string; reaction_type: string;
  account_ids: number[]; post_id: number | null;
  status: string; total_reactions: number;
  successful_reactions: number; failed_reactions: number;
  error: string | null; created_at: string | null; completed_at: string | null;
};

export const reactionsApi = {
  create: (token: string, data: {channel_username: string; reaction_type: string; account_ids: number[]; post_id?: number}) =>
    apiFetch<ReactionJob>("/v1/reactions", {method: "POST", accessToken: token, json: data}),
  list: (token: string) => apiFetch<{items: ReactionJob[]; total: number}>("/v1/reactions", {accessToken: token}),
  get: (token: string, id: number) => apiFetch<ReactionJob>(`/v1/reactions/${id}`, {accessToken: token}),
};

// --- Neuro Chatting API ---
export type ChattingConfig = {
  id: number; name: string; status: string; mode: string;
  target_channels: string[]; prompt_template: string | null;
  max_messages_per_hour: number; min_delay_seconds: number; max_delay_seconds: number;
  account_ids: number[]; created_at: string | null; updated_at: string | null;
};

export const chattingApi = {
  list: (token: string) => apiFetch<{items: ChattingConfig[]; total: number}>("/v1/chatting", {accessToken: token}),
  create: (token: string, data: Partial<ChattingConfig>) =>
    apiFetch<ChattingConfig>("/v1/chatting", {method: "POST", accessToken: token, json: data}),
  start: (token: string, id: number) =>
    apiFetch<{status: string}>(`/v1/chatting/${id}/start`, {method: "POST", accessToken: token}),
  stop: (token: string, id: number) =>
    apiFetch<{status: string}>(`/v1/chatting/${id}/stop`, {method: "POST", accessToken: token}),
  delete: (token: string, id: number) =>
    apiFetch<void>(`/v1/chatting/${id}`, {method: "DELETE", accessToken: token}),
};

// --- Neuro Dialogs API ---
export type DialogConfig = {
  id: number; name: string; status: string; dialog_type: string;
  account_pairs: number[][]; prompt_template: string | null;
  messages_per_session: number; session_interval_hours: number;
  created_at: string | null; updated_at: string | null;
};

export const dialogsApi = {
  list: (token: string) => apiFetch<{items: DialogConfig[]; total: number}>("/v1/dialogs", {accessToken: token}),
  create: (token: string, data: Partial<DialogConfig>) =>
    apiFetch<DialogConfig>("/v1/dialogs", {method: "POST", accessToken: token, json: data}),
  start: (token: string, id: number) =>
    apiFetch<{status: string}>(`/v1/dialogs/${id}/start`, {method: "POST", accessToken: token}),
  stop: (token: string, id: number) =>
    apiFetch<{status: string}>(`/v1/dialogs/${id}/stop`, {method: "POST", accessToken: token}),
  delete: (token: string, id: number) =>
    apiFetch<void>(`/v1/dialogs/${id}`, {method: "DELETE", accessToken: token}),
};

// --- User Parser API ---
export type UserParsingResult = {
  id: number; channel_username: string | null;
  user_telegram_id: number | null; username: string | null;
  first_name: string | null; last_name: string | null;
  bio: string | null; is_premium: boolean; last_seen: string | null;
  parsed_at: string | null;
};

export const userParserApi = {
  parse: (token: string, data: {channel_username: string; account_id: number}) =>
    apiFetch<{status: string; job_id: number}>("/v1/user-parser/parse", {method: "POST", accessToken: token, json: data}),
  listResults: (token: string, channel?: string) =>
    apiFetch<{items: UserParsingResult[]; total: number}>(
      `/v1/user-parser/results${channel ? `?channel=${encodeURIComponent(channel)}` : ""}`, {accessToken: token}),
};

// --- Folder Manager API ---
export type TelegramFolder = {
  id: number; account_id: number; folder_name: string;
  folder_id: number | null; invite_link: string | null;
  channel_usernames: string[]; status: string;
  created_at: string | null; updated_at: string | null;
};

export const foldersApi = {
  list: (token: string) => apiFetch<{items: TelegramFolder[]; total: number}>("/v1/folders", {accessToken: token}),
  create: (token: string, data: {account_id: number; folder_name: string; channel_usernames: string[]}) =>
    apiFetch<TelegramFolder>("/v1/folders", {method: "POST", accessToken: token, json: data}),
  delete: (token: string, id: number) =>
    apiFetch<void>(`/v1/folders/${id}`, {method: "DELETE", accessToken: token}),
  getInvite: (token: string, id: number) =>
    apiFetch<{invite_link: string | null}>(`/v1/folders/${id}/invite`, {accessToken: token}),
};

// --- Channel Map API ---
export type ChannelMapEntry = {
  id: number; telegram_id: number | null; username: string | null;
  title: string | null; category: string | null; subcategory: string | null;
  language: string | null; member_count: number;
  has_comments: boolean; avg_post_reach: number | null;
  engagement_rate: number | null; last_indexed_at: string | null;
  created_at: string | null;
};

export const channelMapApi = {
  list: (token: string, params?: {category?: string; language?: string; min_members?: number}) => {
    const q = new URLSearchParams();
    if (params?.category) q.set("category", params.category);
    if (params?.language) q.set("language", params.language);
    if (params?.min_members) q.set("min_members", String(params.min_members));
    const qs = q.toString();
    return apiFetch<{items: ChannelMapEntry[]; total: number}>(`/v1/channel-map${qs ? `?${qs}` : ""}`, {accessToken: token});
  },
  search: (token: string, data: {query?: string; category?: string; language?: string; min_members?: number; limit?: number}) =>
    apiFetch<{items: ChannelMapEntry[]; total: number}>("/v1/channel-map/search", {method: "POST", accessToken: token, json: data}),
  categories: (token: string) => apiFetch<{categories: string[]}>("/v1/channel-map/categories", {accessToken: token}),
  stats: (token: string) => apiFetch<Record<string, unknown>>("/v1/channel-map/stats", {accessToken: token}),
};

// --- Campaigns API ---
export type CampaignStatus = "draft" | "active" | "paused" | "completed" | "archived";
export type Campaign = {
  id: number; name: string; status: CampaignStatus;
  campaign_type: string; account_ids: number[];
  channel_database_id: number | null;
  comment_prompt: string | null; comment_tone: string | null;
  comment_language: string; schedule_type: string;
  schedule_config: Record<string, unknown> | null;
  budget_daily_actions: number; budget_total_actions: number | null;
  total_actions_performed: number; total_comments_sent: number;
  total_reactions_sent: number;
  started_at: string | null; completed_at: string | null;
  created_at: string | null; updated_at: string | null;
};

export type CampaignRun = {
  id: number; campaign_id: number; status: string;
  actions_performed: number; comments_sent: number;
  reactions_sent: number; errors: number;
  started_at: string | null; completed_at: string | null;
  run_log: unknown | null;
};

export const campaignsApi = {
  list: (token: string) => apiFetch<{items: Campaign[]; total: number}>("/v1/campaigns", {accessToken: token}),
  get: (token: string, id: number) => apiFetch<Campaign & {runs: CampaignRun[]}>(`/v1/campaigns/${id}`, {accessToken: token}),
  create: (token: string, data: Partial<Campaign>) =>
    apiFetch<Campaign>("/v1/campaigns", {method: "POST", accessToken: token, json: data}),
  update: (token: string, id: number, data: Partial<Campaign>) =>
    apiFetch<Campaign>(`/v1/campaigns/${id}`, {method: "PUT", accessToken: token, json: data}),
  delete: (token: string, id: number) => apiFetch<void>(`/v1/campaigns/${id}`, {method: "DELETE", accessToken: token}),
  start: (token: string, id: number) =>
    apiFetch<{status: string}>(`/v1/campaigns/${id}/start`, {method: "POST", accessToken: token}),
  pause: (token: string, id: number) =>
    apiFetch<{status: string}>(`/v1/campaigns/${id}/pause`, {method: "POST", accessToken: token}),
  resume: (token: string, id: number) =>
    apiFetch<{status: string}>(`/v1/campaigns/${id}/resume`, {method: "POST", accessToken: token}),
  stop: (token: string, id: number) =>
    apiFetch<{status: string}>(`/v1/campaigns/${id}/stop`, {method: "POST", accessToken: token}),
  runs: (token: string, id: number) =>
    apiFetch<{items: CampaignRun[]; total: number}>(`/v1/campaigns/${id}/runs`, {accessToken: token}),
  analytics: (token: string, id: number) =>
    apiFetch<Record<string, unknown>>(`/v1/campaigns/${id}/analytics`, {accessToken: token}),
};

// --- Analytics API ---
export type DashboardData = {
  total_comments: number; total_reactions: number;
  total_flood_waits: number; total_spam_blocks: number;
  days: number;
  active_campaigns: number;
  recent_events: unknown[];
  daily_breakdown: Array<{date: string; comments: number; reactions: number; errors: number}>;
  top_channels: Array<{channel: string; actions: number; success_rate: number}>;
  account_activity: Array<{account_id: number; phone: string | null; actions: number; health_score: number}>;
};

export const analyticsApi = {
  dashboard: (token: string, days = 7) =>
    apiFetch<DashboardData>(`/v1/analytics/dashboard?days=${days}`, {accessToken: token}),
  roi: (token: string) => apiFetch<Record<string, unknown>>("/v1/analytics/roi", {accessToken: token}),
};

// ─── Job polling ──────────────────────────────────────────────────────────────

export async function pollJob(
  accessToken: string,
  jobId: number,
  options: { timeoutMs?: number; intervalMs?: number } = {}
): Promise<JobStatusResponse> {
  const timeoutMs = options.timeoutMs ?? 30000;
  const intervalMs = options.intervalMs ?? 1000;
  const startedAt = Date.now();

  while (true) {
    const job = await apiFetch<JobStatusResponse>(`/v1/jobs/${jobId}`, { accessToken });
    if (job.status === "succeeded" || job.status === "failed") {
      return job;
    }
    if (Date.now() - startedAt >= timeoutMs) {
      return job;
    }
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
  }
}
