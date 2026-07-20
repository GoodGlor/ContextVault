import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryPage } from "./QueryPage";
import type { QueryResult } from "../api/query";
import type { Repository } from "../api/repositories";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const REPOS: Repository[] = [
  { id: "r-1", name: "Handbook", description: null },
  { id: "r-2", name: "Runbook", description: "ops" },
];

const CITED: QueryResult = {
  answer: "Retention is 30 days [1] and reviewed quarterly [2].",
  not_in_vault: false,
  citations: [
    { number: 1, chunk_id: "c1", source_id: "s-1", char_start: 0, char_end: 40 },
    { number: 2, chunk_id: "c2", source_id: "s-2", char_start: 41, char_end: 80 },
  ],
  sources: [
    {
      id: "s-1",
      title: "Retention Policy",
      original_filename: "policy.pdf",
      kind: "document",
      verified: false,
      author: null,
    },
    {
      id: "s-2",
      title: "Quarterly review note",
      original_filename: null,
      kind: "admin_note",
      verified: true,
      author: "ada",
    },
  ],
};

describe("QueryPage", () => {
  const fetchMock = vi.fn();
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  function mock(repos: Repository[], result?: QueryResult) {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes("/query")) return Promise.resolve(json(result ?? CITED));
      if (url.endsWith("/repositories")) return Promise.resolve(json(repos));
      throw new Error(`unexpected fetch ${url}`);
    });
  }

  it("shows the granted repositories in the picker", async () => {
    mock(REPOS);
    render(<QueryPage />);
    const picker = await screen.findByLabelText("Repository");
    expect(within(picker).getByRole("option", { name: "Handbook" })).toBeInTheDocument();
    expect(within(picker).getByRole("option", { name: "Runbook" })).toBeInTheDocument();
  });

  it("asks a question and renders the cited answer, verified badge, and sources", async () => {
    mock(REPOS);
    render(<QueryPage />);
    await screen.findByLabelText("Repository");

    await userEvent.type(screen.getByLabelText("Question"), "How long is retention?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    await screen.findByText(/Retention is 30 days/);
    expect(screen.getByText("Retention Policy")).toBeInTheDocument();
    expect(screen.getByText("Quarterly review note")).toBeInTheDocument();
    // The admin-note source is flagged Verified and attributed.
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText(/by ada/)).toBeInTheDocument();
    // Posted to the selected (first) repository.
    const posted = fetchMock.mock.calls.find((c) => String(c[0]).includes("/query"));
    expect(String(posted?.[0])).toContain("/repositories/r-1/query");
  });

  it("highlights the matching source when a citation is clicked", async () => {
    mock(REPOS);
    render(<QueryPage />);
    await screen.findByLabelText("Repository");
    await userEvent.type(screen.getByLabelText("Question"), "retention?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await screen.findByText(/Retention is 30 days/);

    expect(screen.getByTestId("source-s-1")).not.toHaveClass("highlighted");
    await userEvent.click(screen.getByRole("button", { name: "Jump to source 1" }));
    expect(screen.getByTestId("source-s-1")).toHaveClass("highlighted");
    expect(screen.getByTestId("source-s-2")).not.toHaveClass("highlighted");
  });

  it("shows an explicit not-in-vault state", async () => {
    mock(REPOS, {
      answer: "I could not find this in the vault.",
      not_in_vault: true,
      citations: [],
      sources: [],
    });
    render(<QueryPage />);
    await screen.findByLabelText("Repository");
    await userEvent.type(screen.getByLabelText("Question"), "unknown thing?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/not in this vault/i);
  });

  it("tells a user with no grants they have no repositories", async () => {
    mock([]);
    render(<QueryPage />);
    expect(await screen.findByText(/don’t have access to any repositories/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
  });
});
