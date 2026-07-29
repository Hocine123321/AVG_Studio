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

import { beginActivity, labelForRequest } from "./activity";

const TOKEN = new URLSearchParams(location.search).get("token") ?? "";

/** The token this session was opened with, for callers that need it directly (e.g. the `/ws/events` query param). */
export function getToken(): string {
  return TOKEN;
}

async function request<T>(method: "GET" | "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const hasBody = body !== undefined;
  // Registered here, in the ONE wrapper every call goes through, so the
  // global status bar can never be missing a request that is genuinely in
  // flight - and so no screen has to remember to report its own.
  // A write outranks a read in the status bar: "Saving" must not be
  // replaced by a background "Loading" that happens to still be in flight.
  const endActivity = beginActivity(labelForRequest(method, path), method === "GET" ? "low" : "medium");
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: {
        "X-AVG-Token": TOKEN,
        ...(hasBody ? { "Content-Type": "application/json" } : {}),
      },
      body: hasBody ? JSON.stringify(body) : undefined,
    });
  } finally {
    endActivity();
  }

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

export function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>("PATCH", path, body);
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
export type ScriptSubstate = "pending" | "partial" | "done";

export interface PhaseRecord {
  name?: string;
  status: PhaseStatus;
  started?: string | null;
  finished?: string | null;
  duration_ms?: number | null;
  error?: string | null;
}

/** Mirrors `avg.core.models.Take` (Slice 5). One attempt at a project - see
 * `TakeStrip.tsx`/`NewTakePanel.tsx` for the screen built around this. */
export interface Take {
  schema_version: number;
  id: number;
  label: string;
  created: string;
  settings_snapshot: Record<string, unknown>;
  changed_from: number | null;
  changed_ids: string[];
  rerolled_from: string | null;
  phases: Record<string, PhaseRecord>;
  master_output: string | null;
}

export interface ProjectDoc {
  id: string;
  title: string;
  aspect_ratio: string;
  created?: string;
  short_edge?: number;
  master_output?: string | null;
  phases: Record<string, PhaseRecord>;
  /** Both default-filled by the backend (`[]`/`null`) for a project that
   * predates Slice 5 or has never been touched via the takes routes - see
   * `avg.core.takes.migrate_to_takes`. Absent here only if a caller builds
   * a `ProjectDoc`-shaped object by hand rather than from a real response;
   * every real one already carries these two fields. */
  takes?: Take[];
  current_take_id?: number | null;
  /** Present on `GET /api/projects`, where it is derived from the current
   * take's artifacts. `POST /api/projects` returns the persisted ProjectDoc
   * directly, so callers must tolerate it being absent until the next list. */
  script_substate?: ScriptSubstate;
  /** Hidden from Home's default view. Absent on documents written before
   * Slice 6C, which is why every read treats undefined as false. */
  archived?: boolean;
}

export interface ProjectsResponse {
  projects: ProjectDoc[];
  /** False once the startup library scan has finished. The route waits for
   * that scan, so a response always carries `false` - the flag exists for
   * any caller that polls before the route can answer. */
  loading?: boolean;
  /** Total bytes across every project folder, summed by that same scan
   * rather than per request: it is a full recursive stat of the library. */
  storage_bytes?: number;
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

/** `POST /api/projects` - creates a project for `topic` under the active
 * channel and starts the full pipeline on it. `overrides` is `{id: value}`
 * for any per-video override of a channel-scoped setting (see
 * `avg.core.settings_schema`'s `scope="channel"` ids) - applied to this one
 * video only, never written back to the channel document. */
export function createProject(topic: string, overrides: Record<string, unknown> = {}): Promise<ProjectDoc> {
  return apiPost<ProjectDoc>("/api/projects", { topic, overrides });
}

/** `POST /api/projects/archive`. One call for a whole selection, never one
 * request per project: a bulk action that half-applies is worse than one
 * that fails outright, and the route validates every id before it writes
 * anything. Archiving only hides a project from Home - no file is moved or
 * deleted, and the same call with `archived: false` undoes it. */
export function archiveProjects(ids: string[], archived: boolean): Promise<{ archived: boolean; ids: string[] }> {
  return apiPost<{ archived: boolean; ids: string[] }>("/api/projects/archive", { ids, archived });
}

// ---- error log ----

export type ErrorSeverity = "critical" | "needs_review" | "low";

/** One recorded failure. Mirrors `avg.core.error_store.ErrorRecord`.
 * `detail` is a traceback or a provider body; it is already redacted
 * server-side, so it can be shown and copied as-is. */
export interface AppError {
  id: string;
  when: string;
  severity: ErrorSeverity;
  source: string;
  summary: string;
  detail: string;
  context: Record<string, string>;
}

export function getErrors(): Promise<{ errors: AppError[] }> {
  return apiGet<{ errors: AppError[] }>("/api/errors");
}

export function clearErrors(): Promise<{ cleared: boolean }> {
  return apiDelete<{ cleared: boolean }>("/api/errors");
}

/** Report a failure only the frontend can see - a dropped socket, an image
 * that would not load. Fire-and-forget by convention: a failure to report
 * a failure must never surface as a second error. */
export function reportError(body: {
  severity: ErrorSeverity;
  source: string;
  summary: string;
  detail?: string;
  context?: Record<string, string>;
}): Promise<{ recorded: string }> {
  return apiPost<{ recorded: string }>("/api/errors", body);
}

// ---- component health ----

export interface HealthComponent {
  name: string;
  ok: boolean;
  version: string;
  detail: string;
  /** Whether the app can work without it. An optional component that is
   * missing is worth knowing about, not worth reading as a broken app. */
  required?: boolean;
}

export interface HealthResponse {
  components: HealthComponent[];
  ok: boolean;
}

/** `GET /api/health`. `refresh` is the Recheck button and skips the
 * backend's five-minute cache, paying for the `ffmpeg -version` subprocess
 * again. A broken component is a row with `ok: false`, never an error
 * response - reporting the breakage is the point. */
export function getHealth(refresh = false): Promise<HealthResponse> {
  return apiGet<HealthResponse>(`/api/health${refresh ? "?refresh=true" : ""}`);
}

/** One stage of the App Health pipeline. The order the backend returns them
 * in is meaningful - each stage assumes the one before it passed - so the
 * screen must not sort or regroup these. */
export interface HealthStage {
  key: string;
  name: string;
  detail: string;
  ok: boolean;
  checks: HealthComponent[];
}

export interface HealthReport {
  ok: boolean;
  stages: HealthStage[];
}

export function getHealthReport(refresh = false): Promise<HealthReport> {
  return apiGet<HealthReport>(`/api/health/report${refresh ? "?refresh=true" : ""}`);
}

// ---- take artifacts ----

/** `GET /api/projects/{id}/takes/{take}/artifacts` - artifact key to size
 * in bytes, or to a FILE COUNT for a directory artifact like `images`.
 * Only what actually exists: a phase that failed halfway leaves some of
 * its artifacts and not others. */
export interface TakeArtifacts {
  files: Record<string, number>;
  /** Phase key -> the artifact keys that phase owns AND that exist. The
   * mapping is the backend's (`avg/core/artifacts.py`); a copy of it here
   * would be another hand-kept duplicate like `phases.ts`. */
  by_phase: Record<string, string[]>;
}

export function getTakeArtifacts(projectId: string, takeId: number): Promise<TakeArtifacts> {
  return apiGet<TakeArtifacts>(
    `/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}/artifacts`,
  );
}

/** The filenames inside a directory artifact, sorted. */
export function getTakeArtifactList(
  projectId: string,
  takeId: number,
  key: string,
): Promise<{ files: string[] }> {
  return apiGet<{ files: string[] }>(
    `/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}/artifact-list/${encodeURIComponent(key)}`,
  );
}

/** A URL, not a fetch: `<img>` and `<audio>` cannot set a header, so these
 * go through the same `mediaUrl` helper `videoUrl`/`imageUrl` already use -
 * one token scheme for every media route, not a second. */
export function artifactUrl(projectId: string, takeId: number, key: string): string {
  return mediaUrl(
    `/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}/artifact/${encodeURIComponent(key)}`,
  );
}

/** One file out of a directory artifact. `name` is a PATH SEGMENT, matching
 * the route's `/{key}/{name}` shape - not a query parameter. */
export function artifactFileUrl(projectId: string, takeId: number, key: string, name: string): string {
  return mediaUrl(
    `/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}/artifact/${encodeURIComponent(key)}/${encodeURIComponent(name)}`,
  );
}

// ---- working-models cache ----

/** `GET /api/models/cache`. `providers` holds ONLY models a probe proved
 * working; a provider that was probed and had nothing work appears with an
 * empty list, which is a different statement from being absent. `updated:
 * null` means no sweep has ever run - the UI says "build the cache" and
 * never invents a model list. See `avg/core/model_cache.py`. */
export interface ModelCacheResponse {
  updated: string | null;
  providers: Record<string, string[]>;
  building: boolean;
}

export function getModelCache(): Promise<ModelCacheResponse> {
  return apiGet<ModelCacheResponse>("/api/models/cache");
}

/** Start a probe sweep. 202 when it starts, 409 when one is already
 * running - probing is one call per model per keyed provider, so two at
 * once would double the spend for nothing. */
export function buildModelCache(): Promise<{ started: boolean }> {
  return apiPost<{ started: boolean }>("/api/models/cache/build", {});
}

// ---- cost table ----

/** `GET /api/costs`. Served rather than hardcoded here on purpose: these
 * are maintained estimates that change, and `phases.ts` is already one
 * hand-kept copy of backend data too many. `leaf_cost` is keyed by DISPLAY
 * leaf (`draft`/`segmentation`, not `script`); `phase_leaves` maps each
 * pipeline phase key onto its leaves. */
export interface CostTable {
  currency: string;
  leaf_cost: Record<string, number>;
  phase_leaves: Record<string, string[]>;
  length_multiplier: Record<string, number>;
  base_cost: number;
}

export function getCosts(): Promise<CostTable> {
  return apiGet<CostTable>("/api/costs");
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
  /** Where the options come from at runtime when the catalogue cannot
   * enumerate them. `"model_cache"` means the working-models cache fills
   * the list; the field keeps its `type`, so an unbuilt cache still allows
   * a typed value rather than locking the setting. */
  source?: string | null;
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

export function createChannel(label: string, note = ""): Promise<ChannelDoc> {
  return apiPost<ChannelDoc>("/api/channels", { label, note });
}

/** `label`/`note` omitted entirely leave that field untouched server-side
 * (see `avg.api.routes_settings.ChannelUpdate`) - only send what changed. */
export function updateChannel(id: string, patch: { label?: string; note?: string }): Promise<ChannelDoc> {
  return request<ChannelDoc>("PATCH", `/api/channels/${encodeURIComponent(id)}`, patch);
}

export function deleteChannel(id: string): Promise<{ deleted: boolean }> {
  return apiDelete<{ deleted: boolean }>(`/api/channels/${encodeURIComponent(id)}`);
}

export function activateChannel(id: string): Promise<{ active_channel_id: string }> {
  return apiPost<{ active_channel_id: string }>(`/api/channels/${encodeURIComponent(id)}/activate`);
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

// ---- takes (Slice 5, Task 4) ----
// Mirrors `avg.api.routes_takes` - see that module's docstring for the full
// contract (current vs. selected, the one-job-at-a-time guard, etc).

export interface TakesResponse {
  takes: Take[];
  current_take_id: number | null;
  /** The whole project's size on disk, in bytes - server-computed (see
   * `routes_takes._folder_size_bytes`), never walked from the browser. */
  disk_bytes: number;
}

/** `POST .../takes/preview` and the real `POST .../takes` share this body
 * shape - `overrides` is `{settingId: value}` for any `scope="channel"`
 * catalogue id, `reroll_from` names a phase key to re-run with nothing
 * changed. At least one of the two must be set (the route 400s otherwise). */
export interface TakeRequestBody {
  overrides?: Record<string, unknown>;
  reroll_from?: string | null;
}

export interface TakePreviewResult {
  rerun: string[];
  keeps: string[];
  /** `null` means "no history to estimate from" - never render a fabricated
   * number (or the word "unknown") in its place; see `NewTakePanel.tsx`. */
  estimate_seconds: number | null;
  unknown: string[];
}

export function getTakes(projectId: string): Promise<TakesResponse> {
  return apiGet<TakesResponse>(`/api/projects/${encodeURIComponent(projectId)}/takes`);
}

export function previewTake(projectId: string, body: TakeRequestBody): Promise<TakePreviewResult> {
  return apiPost<TakePreviewResult>(`/api/projects/${encodeURIComponent(projectId)}/takes/preview`, body);
}

export function createTake(projectId: string, body: TakeRequestBody): Promise<Take> {
  return apiPost<Take>(`/api/projects/${encodeURIComponent(projectId)}/takes`, body);
}

export function deleteTake(projectId: string, takeId: number): Promise<{ deleted: boolean; current_take_id: number | null }> {
  return apiDelete(`/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}`);
}

/** Pins `takeId` as the project's CURRENT take - distinct from merely
 * selecting it to look at in Studio, which never calls this (see
 * `avg.core.takes`'s module docstring for why conflating the two would be
 * a bug). Nothing in this slice's UI calls this yet; it exists so a later
 * "make current" action has a ready-made client function. */
export function selectTake(projectId: string, takeId: number): Promise<{ current_take_id: number }> {
  return apiPost(`/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}/select`);
}

export function renameTake(projectId: string, takeId: number, label: string): Promise<Take> {
  return apiPatch<Take>(`/api/projects/${encodeURIComponent(projectId)}/takes/${takeId}`, { label });
}
