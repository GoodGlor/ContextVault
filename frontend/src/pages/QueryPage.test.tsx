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
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

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

  it("sends the running conversation history on a follow-up question", async () => {
    mock(REPOS);
    render(<QueryPage />);
    await screen.findByLabelText("Repository");

    // First turn.
    await userEvent.type(screen.getByLabelText("Question"), "What is the PTO policy?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/Retention is 30 days/);

    // Follow-up turn.
    await userEvent.type(screen.getByLabelText("Question"), "and for part-timers?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    const queryCalls = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/query"));
    expect(queryCalls).toHaveLength(2);
    // The first request carries no history; the follow-up carries the prior turn.
    expect(JSON.parse(String(queryCalls[0][1]?.body))).toEqual({
      question: "What is the PTO policy?",
      history: [],
    });
    expect(JSON.parse(String(queryCalls[1][1]?.body))).toEqual({
      question: "and for part-timers?",
      history: [{ question: "What is the PTO policy?", answer: CITED.answer }],
    });
  });

  it("starts a fresh conversation when the repository changes", async () => {
    mock(REPOS);
    render(<QueryPage />);
    const picker = await screen.findByLabelText("Repository");

    await userEvent.type(screen.getByLabelText("Question"), "How long is retention?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/Retention is 30 days/);

    // Switching repositories clears the transcript...
    await userEvent.selectOptions(picker, "r-2");
    expect(screen.queryByText(/Retention is 30 days/)).not.toBeInTheDocument();

    // ...so the next question is sent with no carried-over history.
    await userEvent.type(screen.getByLabelText("Question"), "fresh start?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    const lastQuery = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/query")).at(-1);
    expect(String(lastQuery?.[0])).toContain("/repositories/r-2/query");
    expect(JSON.parse(String(lastQuery?.[1]?.body))).toEqual({
      question: "fresh start?",
      history: [],
    });
  });

  it("highlights the matching source when a citation is clicked", async () => {
    mock(REPOS);
    render(<QueryPage />);
    await screen.findByLabelText("Repository");
    await userEvent.type(screen.getByLabelText("Question"), "retention?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
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
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/not in this vault/i);
  });

  it("tells a user with no grants they have no repositories", async () => {
    mock([]);
    render(<QueryPage />);
    expect(await screen.findByText(/don’t have access to any repositories/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Question")).not.toBeInTheDocument();
  });
});
