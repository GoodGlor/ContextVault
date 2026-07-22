import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminRepositoriesPage } from "./AdminRepositoriesPage";
import type { AdminRepository, LLMConfig } from "../api/repositories";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const REPOS: AdminRepository[] = [
  { id: "r-1", name: "Handbook", description: "the company handbook", configured: true },
  { id: "r-2", name: "Runbook", description: null, configured: false },
];

const UNCONFIGURED: LLMConfig = {
  provider: null,
  model: null,
  api_key_masked: null,
  configured: false,
};

const CONFIGURED: LLMConfig = {
  provider: "openai",
  model: "gpt-4o",
  api_key_masked: "sk-…•••4f2a",
  configured: true,
};

describe("AdminRepositoriesPage", () => {
  const fetchMock = vi.fn();
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  /** Route every call the page makes; `configs` maps repo id -> its llm-config. */
  function mock(opts: {
    repos?: AdminRepository[];
    configs?: Record<string, LLMConfig>;
    created?: AdminRepository;
    models?: string[];
    modelsStatus?: number;
  }) {
    const repos = opts.repos ?? REPOS;
    const configs = opts.configs ?? {};
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (/\/repositories\/([^/]+)\/llm-models$/.test(url) && method === "POST") {
        if (opts.modelsStatus && opts.modelsStatus >= 400) {
          return Promise.resolve(
            json({ detail: "Could not list models: invalid key" }, opts.modelsStatus),
          );
        }
        return Promise.resolve(json({ models: opts.models ?? [] }));
      }
      const m = /\/repositories\/([^/]+)\/llm-config$/.exec(url);
      if (m) {
        if (method === "PUT") {
          const body = JSON.parse(String(init?.body)) as { provider: string; model: string };
          return Promise.resolve(
            json({
              provider: body.provider,
              model: body.model,
              api_key_masked: "sk-…•••9z9z",
              configured: true,
            }),
          );
        }
        return Promise.resolve(json(configs[m[1]] ?? UNCONFIGURED));
      }
      if (url.endsWith("/admin/repositories")) return Promise.resolve(json(repos));
      if (url.endsWith("/repositories") && method === "POST") {
        return Promise.resolve(json(opts.created ?? REPOS[0], 201));
      }
      throw new Error(`unexpected fetch ${method} ${url}`);
    });
  }

  it("lists every repository with its configured state", async () => {
    mock({});
    render(<AdminRepositoriesPage />);
    expect(await screen.findByText("Handbook")).toBeInTheDocument();
    expect(screen.getByText("Runbook")).toBeInTheDocument();
    // Handbook is configured, Runbook is not.
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(screen.getByText("Not configured")).toBeInTheDocument();
  });

  it("creates a repository and shows it in the list", async () => {
    const created: AdminRepository = {
      id: "r-3",
      name: "Wiki",
      description: "internal wiki",
      configured: false,
    };
    mock({ repos: [], created });
    render(<AdminRepositoriesPage />);
    await screen.findByRole("button", { name: "Create repository" });

    await userEvent.type(screen.getByLabelText("Repository name"), "Wiki");
    await userEvent.type(screen.getByLabelText("Description"), "internal wiki");
    await userEvent.click(screen.getByRole("button", { name: "Create repository" }));

    // POSTed with the entered fields.
    const posted = fetchMock.mock.calls.find(
      (c) => (c[1]?.method ?? "GET") === "POST" && String(c[0]).endsWith("/repositories"),
    );
    expect(JSON.parse(String(posted?.[1]?.body))).toMatchObject({
      name: "Wiki",
      description: "internal wiki",
    });
    // The created repo appears in the list.
    expect(await screen.findByText("Wiki")).toBeInTheDocument();
  });

  it("configures a repository's provider/model/key", async () => {
    mock({ configs: { "r-2": UNCONFIGURED } });
    render(<AdminRepositoriesPage />);
    const runbook = (await screen.findByText("Runbook")).closest("li") as HTMLElement;

    await userEvent.click(within(runbook).getByRole("button", { name: "Configure" }));

    await userEvent.selectOptions(await screen.findByLabelText("Provider"), "anthropic");
    await userEvent.type(screen.getByLabelText("Model"), "claude-opus-4-8");
    await userEvent.type(screen.getByLabelText("API key"), "sk-ant-secret123");
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));

    const put = fetchMock.mock.calls.find((c) => (c[1]?.method ?? "GET") === "PUT");
    expect(String(put?.[0])).toContain("/repositories/r-2/llm-config");
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({
      provider: "anthropic",
      model: "claude-opus-4-8",
      api_key: "sk-ant-secret123",
    });
    // Success feedback after saving.
    expect(await screen.findByText(/saved/i)).toBeInTheDocument();
  });

  it("loads models into the dropdown and sends the entered provider/key", async () => {
    mock({ configs: { "r-2": UNCONFIGURED }, models: ["claude-opus-4-8", "claude-sonnet-5"] });
    render(<AdminRepositoriesPage />);
    const runbook = (await screen.findByText("Runbook")).closest("li") as HTMLElement;
    await userEvent.click(within(runbook).getByRole("button", { name: "Configure" }));

    await userEvent.selectOptions(await screen.findByLabelText("Provider"), "anthropic");
    await userEvent.type(screen.getByLabelText("API key"), "sk-ant-secret123");
    await userEvent.click(screen.getByRole("button", { name: "Load models" }));

    // POSTed to the list-models endpoint with the entered provider + key.
    const post = fetchMock.mock.calls.find(
      (c) => (c[1]?.method ?? "GET") === "POST" && String(c[0]).includes("/llm-models"),
    );
    expect(String(post?.[0])).toContain("/repositories/r-2/llm-models");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      provider: "anthropic",
      api_key: "sk-ant-secret123",
    });
    // A visible dropdown of the returned models appears, plus a "Loaded N models" confirmation.
    const dropdown = (await screen.findByLabelText("Choose a loaded model")) as HTMLSelectElement;
    expect(within(dropdown).getByRole("option", { name: "claude-opus-4-8" })).toBeInTheDocument();
    expect(within(dropdown).getByRole("option", { name: "claude-sonnet-5" })).toBeInTheDocument();
    expect(screen.getByText(/loaded 2 models/i)).toBeInTheDocument();

    // Picking a model from the dropdown fills the Model input.
    await userEvent.selectOptions(dropdown, "claude-sonnet-5");
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("claude-sonnet-5");
  });

  it("surfaces an error when loading models fails", async () => {
    mock({ configs: { "r-2": UNCONFIGURED }, modelsStatus: 400 });
    render(<AdminRepositoriesPage />);
    const runbook = (await screen.findByText("Runbook")).closest("li") as HTMLElement;
    await userEvent.click(within(runbook).getByRole("button", { name: "Configure" }));

    await userEvent.type(screen.getByLabelText("API key"), "bad-key");
    await userEvent.click(screen.getByRole("button", { name: "Load models" }));

    expect(await screen.findByText(/could not list models/i)).toBeInTheDocument();
  });

  it("shows the masked key for an already-configured repository", async () => {
    mock({ configs: { "r-1": CONFIGURED } });
    render(<AdminRepositoriesPage />);
    const handbook = (await screen.findByText("Handbook")).closest("li") as HTMLElement;

    await userEvent.click(within(handbook).getByRole("button", { name: "Configure" }));

    // The current key comes back masked, never in full.
    expect(await screen.findByText(/sk-…•••4f2a/)).toBeInTheDocument();
  });
});
