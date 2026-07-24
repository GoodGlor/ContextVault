import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RepositoryProvider } from "./RepositoryProvider";
import { useCurrentRepository } from "./RepositoryContext";

// Auth is faked per-test so we can flip role → which list endpoint is used.
const roleRef = { current: "member" as "member" | "admin" };
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ session: { role: roleRef.current, username: "u", userId: "1" } }),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function Probe() {
  const { repos, currentRepoId, setCurrentRepoId, loading } = useCurrentRepository();
  if (loading) return <p>loading</p>;
  return (
    <div>
      <span data-testid="current">{currentRepoId}</span>
      <span data-testid="count">{repos.length}</span>
      <button onClick={() => setCurrentRepoId("r-2")}>pick2</button>
    </div>
  );
}

describe("RepositoryProvider", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    roleRef.current = "member";
    localStorage.clear();
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("defaults to the first repo and calls the granted-list endpoint for members", async () => {
    fetchMock.mockResolvedValue(
      json([
        { id: "r-1", name: "A" },
        { id: "r-2", name: "B" },
      ]),
    );
    render(
      <RepositoryProvider>
        <Probe />
      </RepositoryProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("r-1"));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/repositories");
  });

  it("uses the admin all-repos endpoint when the session is admin", async () => {
    roleRef.current = "admin";
    fetchMock.mockResolvedValue(
      json([{ id: "r-1", name: "A", description: null, configured: true }]),
    );
    render(
      <RepositoryProvider>
        <Probe />
      </RepositoryProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("1"));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/repositories");
  });

  it("restores a still-valid stored repo, and persists a new selection", async () => {
    localStorage.setItem("contextvault.currentRepo", "r-2");
    fetchMock.mockResolvedValue(
      json([
        { id: "r-1", name: "A" },
        { id: "r-2", name: "B" },
      ]),
    );
    render(
      <RepositoryProvider>
        <Probe />
      </RepositoryProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("r-2"));
    await act(() => userEvent.click(screen.getByText("pick2")));
    expect(localStorage.getItem("contextvault.currentRepo")).toBe("r-2");
  });

  it("falls back to the first repo when the stored id is gone", async () => {
    localStorage.setItem("contextvault.currentRepo", "stale");
    fetchMock.mockResolvedValue(json([{ id: "r-1", name: "A" }]));
    render(
      <RepositoryProvider>
        <Probe />
      </RepositoryProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("current")).toHaveTextContent("r-1"));
  });
});
