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

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
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
