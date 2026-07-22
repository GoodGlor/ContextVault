import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminSourcesPage, SOURCE_POLL_MS } from "./AdminSourcesPage";
import type { AdminRepository } from "../api/repositories";
import type { Source } from "../api/sources";

function json(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const REPOS: AdminRepository[] = [
  { id: "r-1", name: "Handbook", description: null, configured: true },
];

function source(overrides: Partial<Source> = {}): Source {
  return {
    id: "s-1",
    repository_id: "r-1",
    kind: "document",
    title: "policy.pdf",
    original_filename: "policy.pdf",
    source_url: null,
    status: "done",
    ingest_error: null,
    created_at: "2026-07-20T10:00:00Z",
    ...overrides,
  };
}

describe("AdminSourcesPage", () => {
  const fetchMock = vi.fn();
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  /** `sources` may be a static array or a function of the (0-based) list-call index. */
  function mock(opts: {
    repos?: AdminRepository[];
    sources?: Source[] | ((call: number) => Source[]);
    uploaded?: Source;
    webAdded?: Source;
  }) {
    const repos = opts.repos ?? REPOS;
    let listCall = 0;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/admin/repositories")) return Promise.resolve(json(repos));
      if (/\/repositories\/[^/]+\/web-sources$/.test(url) && method === "POST") {
        return Promise.resolve(
          json(opts.webAdded ?? source({ kind: "web", status: "pending" }), 201),
        );
      }
      if (/\/repositories\/[^/]+\/sources$/.test(url)) {
        if (method === "POST") {
          return Promise.resolve(json(opts.uploaded ?? source({ status: "pending" }), 201));
        }
        const src = opts.sources ?? [];
        const list = typeof src === "function" ? src(listCall++) : src;
        return Promise.resolve(json(list));
      }
      if (/\/sources\/[^/]+$/.test(url) && method === "DELETE") {
        return Promise.resolve(json(null, 204));
      }
      throw new Error(`unexpected fetch ${method} ${url}`);
    });
  }

  it("lists the selected repository's sources with their status", async () => {
    mock({ sources: [source({ id: "s-1", title: "policy.pdf", status: "done" })] });
    render(<AdminSourcesPage />);
    expect(await screen.findByText("policy.pdf")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("uploads a document and shows it in the list", async () => {
    mock({
      sources: [],
      uploaded: source({ id: "s-9", title: "handbook.pdf", status: "pending" }),
    });
    render(<AdminSourcesPage />);
    await screen.findByRole("button", { name: "Upload" });

    const file = new File(["hello"], "handbook.pdf", { type: "application/pdf" });
    await userEvent.upload(screen.getByLabelText("Document"), file);
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    const posted = fetchMock.mock.calls.find(
      (c) => (c[1]?.method ?? "GET") === "POST" && String(c[0]).endsWith("/sources"),
    );
    expect(posted?.[1]?.body).toBeInstanceOf(FormData);
    expect((posted?.[1]?.body as FormData).get("file")).toBeInstanceOf(File);
    // The newly uploaded source appears (pending).
    expect(await screen.findByText("handbook.pdf")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("deletes a source", async () => {
    mock({ sources: [source({ id: "s-1", title: "policy.pdf" })] });
    render(<AdminSourcesPage />);
    const row = (await screen.findByText("policy.pdf")).closest("li") as HTMLElement;

    await userEvent.click(within(row).getByRole("button", { name: "Delete" }));

    const del = fetchMock.mock.calls.find((c) => (c[1]?.method ?? "GET") === "DELETE");
    expect(String(del?.[0])).toContain("/sources/s-1");
    // Removed from the list.
    expect(screen.queryByText("policy.pdf")).not.toBeInTheDocument();
  });

  it("polls a still-ingesting source until it reaches done", async () => {
    vi.useFakeTimers();
    try {
      // First listing: processing. Every listing after: done.
      mock({
        sources: (call) => [
          source({ id: "s-1", title: "big.pdf", status: call === 0 ? "processing" : "done" }),
        ],
      });
      render(<AdminSourcesPage />);
      // Flush the mount (load repos → auto-select → list sources).
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(screen.getByText("processing")).toBeInTheDocument();

      // The poll fires and the status advances to done.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SOURCE_POLL_MS);
      });
      expect(screen.getByText("done")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the ingestion error for a failed source", async () => {
    mock({
      sources: [source({ id: "s-1", title: "broken.pdf", status: "failed", ingest_error: "boom" })],
    });
    render(<AdminSourcesPage />);
    expect(await screen.findByText("failed")).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });

  it("renders the OCR helper note", async () => {
    mock({ sources: [] });
    render(<AdminSourcesPage />);
    expect(
      await screen.findByText(/only text visible in the image is captured/i),
    ).toBeInTheDocument();
  });

  it("accepts HEIC/HEIF images in the file picker", async () => {
    mock({ sources: [] });
    render(<AdminSourcesPage />);
    const input = (await screen.findByLabelText(/document/i)) as HTMLInputElement;
    expect(input.accept).toContain(".heic");
    expect(input.accept).toContain(".heif");
  });

  it("submits a web link and appends the created source", async () => {
    mock({
      sources: [],
      webAdded: source({
        id: "w-1",
        kind: "web",
        title: "https://x.test",
        original_filename: null,
        source_url: "https://x.test",
        status: "pending",
      }),
    });
    render(<AdminSourcesPage />);
    await screen.findByRole("button", { name: "Add link" });

    await userEvent.type(screen.getByLabelText("Web link"), "https://x.test");
    await userEvent.click(screen.getByRole("button", { name: "Add link" }));

    const posted = fetchMock.mock.calls.find(
      (c) => (c[1]?.method ?? "GET") === "POST" && String(c[0]).endsWith("/web-sources"),
    );
    expect(String(posted?.[0])).toContain("/repositories/r-1/web-sources");
    expect(JSON.parse(String(posted?.[1]?.body))).toEqual({ url: "https://x.test" });

    // The newly added web source appears, linked to its URL.
    const link = await screen.findByRole("link", { name: "https://x.test" });
    expect(link).toHaveAttribute("href", "https://x.test");
  });
});
