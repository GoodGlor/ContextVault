import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch, configureApi } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiFetch", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    configureApi({ getToken: () => null, onUnauthorized: null });
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("prefixes the path with /api and returns parsed JSON", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    const result = await apiFetch<{ ok: boolean }>("/health");
    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith("/api/health", expect.anything());
  });

  it("attaches the bearer token from the configured provider", async () => {
    configureApi({ getToken: () => "tok-abc" });
    fetchMock.mockResolvedValue(jsonResponse({}));
    await apiFetch("/repositories");
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer tok-abc");
  });

  it("throws an ApiError carrying the backend detail on non-2xx", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Invalid username or password" }, 401));
    await expect(apiFetch("/auth/login")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      detail: "Invalid username or password",
    });
  });

  it("fires onUnauthorized exactly once on a 401", async () => {
    const onUnauthorized = vi.fn();
    configureApi({ onUnauthorized });
    fetchMock.mockResolvedValue(jsonResponse({ detail: "nope" }, 401));
    await expect(apiFetch("/x")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("flattens FastAPI validation error lists into one message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: [{ msg: "field required" }, { msg: "too short" }] }, 422),
    );
    await expect(apiFetch("/x")).rejects.toMatchObject({
      detail: "field required; too short",
    });
  });
});
