import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AdminDataPage } from "./AdminDataPage";
import { RepositoryContext, type RepositoryContextValue } from "../repository/RepositoryContext";

const repoValue: RepositoryContextValue = {
  repos: [{ id: "r-1", name: "Handbook", description: null }],
  currentRepoId: "r-1",
  setCurrentRepoId: vi.fn(),
  loading: false,
  error: null,
};

function renderData(initial = "/admin/data") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <RepositoryContext.Provider value={repoValue}>
        <AdminDataPage />
      </RepositoryContext.Provider>
    </MemoryRouter>,
  );
}

describe("AdminDataPage", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValue(new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it("shows the Documents tab selected by default", () => {
    renderData();
    expect(screen.getByRole("tab", { name: /documents/i })).toHaveAttribute("aria-selected", "true");
  });

  it("honors ?tab=database on load", () => {
    renderData("/admin/data?tab=database");
    expect(screen.getByRole("tab", { name: /database/i })).toHaveAttribute("aria-selected", "true");
  });

  it("switches tabs on click", async () => {
    renderData();
    await userEvent.click(screen.getByRole("tab", { name: /database/i }));
    expect(screen.getByRole("tab", { name: /database/i })).toHaveAttribute("aria-selected", "true");
  });
});
