/**
 * Owns the session token and every HTTP call to the FastAPI bridge.
 *
 * The token arrives once, in the page URL (`avg.shell` opens the window at
 * `http://127.0.0.1:<port>/?token=<token>`). This module reads it exactly
 * once from `location.search` at module load time and attaches it to every
 * request as `X-AVG-Token` - no other module reads `location.search` or the
 * header name itself, so if the transport ever changes (cookie, different
 * header, whatever) this is the only file that has to change.
 */

const TOKEN = new URLSearchParams(location.search).get("token") ?? "";

/** The token this session was opened with, for callers that need it directly (e.g. the `/ws/events` query param). */
export function getToken(): string {
  return TOKEN;
}

async function request<T>(method: "GET" | "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
  const hasBody = body !== undefined;
  const res = await fetch(path, {
    method,
    headers: {
      "X-AVG-Token": TOKEN,
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
    },
    body: hasBody ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      // ignore - body isn't always readable/relevant
    }
    throw new Error(`${method} ${path} failed: ${res.status} ${res.statusText}${detail ? ` - ${detail}` : ""}`);
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>("GET", path);
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("POST", path, body);
}

export function apiDelete<T>(path: string): Promise<T> {
  return request<T>("DELETE", path);
}

/** Builds a `/ws/...` URL carrying the token as a query param, since browsers cannot set headers on a WebSocket handshake. */
export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const sep = path.includes("?") ? "&" : "?";
  return `${proto}//${location.host}${path}${sep}token=${encodeURIComponent(TOKEN)}`;
}

/**
 * Builds a same-origin URL carrying the token as a `?token=` query param -
 * for the handful of routes loaded by plain `<img src>` / `<video src>`
 * tags rather than `fetch`, which means the browser issues the request
 * itself and can't attach the `X-AVG-Token` header every other call in
 * this file uses. `avg/api/deps.py`'s `require_token` accepts either.
 */
function mediaUrl(path: string): string {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(TOKEN)}`;
}

export function thumbnailUrl(projectId: string): string {
  return mediaUrl(`/api/projects/${encodeURIComponent(projectId)}/thumbnail`);
}

export function videoUrl(projectId: string): string {
  return mediaUrl(`/api/projects/${encodeURIComponent(projectId)}/video`);
}

export function imageUrl(projectId: string, filename: string): string {
  return mediaUrl(`/api/projects/${encodeURIComponent(projectId)}/images/${encodeURIComponent(filename)}`);
}

export interface ProjectImagesResponse {
  images: string[];
}

// ---- API-shaped types (mirrors avg.core.models on the Python side) ----

export type PhaseStatus = "pending" | "running" | "done" | "failed";

export interface PhaseRecord {
  name?: string;
  status: PhaseStatus;
  started?: string | null;
  finished?: string | null;
  duration_ms?: number | null;
  error?: string | null;
}

export interface ProjectDoc {
  id: string;
  title: string;
  aspect_ratio: string;
  created?: string;
  short_edge?: number;
  master_output?: string | null;
  phases: Record<string, PhaseRecord>;
}

export interface ProjectsResponse {
  projects: ProjectDoc[];
}

// ---- settings / first-run welcome ----

export interface SettingsSuggestion {
  path: string;
  project_count: number;
  source: "legacy" | "default";
}

/** `POST /api/settings`'s saved document - no `suggestion` field, unlike `SettingsDoc` below (that's only ever computed on a GET). */
export interface SavedSettings {
  schema_version: number;
  output_root: string;
  onboarded: boolean;
}

export interface SettingsDoc {
  schema_version: number;
  output_root: string | null;
  onboarded: boolean;
  suggestion: SettingsSuggestion;
}

export function getSettings(): Promise<SettingsDoc> {
  return apiGet<SettingsDoc>("/api/settings");
}

export function saveSettings(outputRoot: string): Promise<SavedSettings> {
  return apiPost<SavedSettings>("/api/settings", { output_root: outputRoot });
}

export function scanFolder(path: string): Promise<{ project_count: number }> {
  return apiGet<{ project_count: number }>(`/api/settings/scan?path=${encodeURIComponent(path)}`);
}

// ---- settings catalogue / channels / provider keys (Slice 2 Task 4) ----
// Mirrors `avg.core.settings_schema.SettingSpec` and `avg.api.routes_settings`.

export type SettingType = "bool" | "int" | "float" | "text" | "choice" | "path" | "secret";
export type SettingScope = "app" | "channel";

export interface SettingSpec {
  id: string;
  group: string;
  label: string;
  help: string;
  type: SettingType;
  default: unknown;
  scope: SettingScope;
  choices: string[] | null;
}

export interface SettingsSchemaResponse {
  settings: SettingSpec[];
}

/** `app`/`channel` are already-resolved current values (default-filled) for
 * every setting of that scope - a `type="secret"` id's "value" here is a
 * boolean (whether a key is set), never the key itself; see
 * `routes_settings._app_values`. */
export interface SettingsValuesResponse {
  app: Record<string, unknown>;
  channel: Record<string, unknown>;
  active_channel_id: string | null;
}

export interface ChannelDoc {
  schema_version: number;
  id: string;
  label: string;
  note: string;
  settings: Record<string, unknown>;
}

export interface ChannelsResponse {
  channels: ChannelDoc[];
  active_channel_id: string | null;
}

export function getSettingsSchema(): Promise<SettingsSchemaResponse> {
  return apiGet<SettingsSchemaResponse>("/api/settings/schema");
}

export function getSettingsValues(): Promise<SettingsValuesResponse> {
  return apiGet<SettingsValuesResponse>("/api/settings/values");
}

/** Saves a batch of `{id: value}` pairs in one request - the server
 * validates and (for channel-scoped ids) requires an active channel for
 * the WHOLE batch before writing anything, so a mixed batch either lands
 * completely or not at all (see `routes_settings.post_settings_values`). */
export function saveSettingsValues(values: Record<string, unknown>): Promise<SettingsValuesResponse> {
  return apiPost<SettingsValuesResponse>("/api/settings/values", { values });
}

export function getChannels(): Promise<ChannelsResponse> {
  return apiGet<ChannelsResponse>("/api/channels");
}

/** `{provider: bool}` - whether a key is set, never the key. */
export function getKeys(): Promise<Record<string, boolean>> {
  return apiGet<Record<string, boolean>>("/api/keys");
}

export function setProviderKey(provider: string, key: string): Promise<Record<string, boolean>> {
  return apiPost<Record<string, boolean>>(`/api/keys/${encodeURIComponent(provider)}`, { key });
}

export function deleteProviderKey(provider: string): Promise<Record<string, boolean>> {
  return apiDelete<Record<string, boolean>>(`/api/keys/${encodeURIComponent(provider)}`);
}
