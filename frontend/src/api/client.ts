// A small typed fetch wrapper for the ContextVault REST API.
//
// Every request is prefixed with `/api` (the Vite dev server proxies that to the
// FastAPI backend; in production the SPA is served behind the same origin). The
// client attaches the JWT bearer token from a pluggable provider, JSON-encodes
// bodies, and turns any non-2xx response into a typed `ApiError` carrying the
// backend's `detail` string. A 401 fires the `onUnauthorized` hook so the auth
// layer can clear an expired session and bounce to the login screen.

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

type TokenProvider = () => string | null;

let getToken: TokenProvider = () => null;
let onUnauthorized: (() => void) | null = null;

/** Wire the client to the current auth session (token source + 401 handler). */
export function configureApi(opts: {
  getToken?: TokenProvider;
  onUnauthorized?: (() => void) | null;
}): void {
  if (opts.getToken !== undefined) getToken = opts.getToken;
  if (opts.onUnauthorized !== undefined) onUnauthorized = opts.onUnauthorized;
}

async function extractDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      // FastAPI validation errors arrive as a list of {msg, loc, ...}.
      return body.detail
        .map((e) => (e && typeof e === "object" && "msg" in e ? String(e.msg) : String(e)))
        .join("; ");
    }
  } catch {
    // fall through to a generic message
  }
  return res.statusText || `Request failed (${res.status})`;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`/api${path}`, { ...init, headers });

  if (res.status === 401 && onUnauthorized) onUnauthorized();

  if (!res.ok) throw new ApiError(res.status, await extractDetail(res));

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Convenience helpers for the common verbs. */
export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: "PUT",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  del: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, {
      method: "DELETE",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
};
