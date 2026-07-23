import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

/**
 * End-to-end: the repository LLM config panel (redesigned).
 *
 * Runs against the real stack (auth, repo creation, and the config PUT/GET all hit
 * Postgres and the encrypted-key storage) — only the provider's *model-list* call is
 * intercepted, since listing models would otherwise need a live provider key. It
 * proves the two fixes the redesign delivers, in a real browser:
 *   1. the model is a single dropdown (no free-text input), and
 *   2. once a key is stored, the model can be changed and saved WITHOUT re-entering
 *      the key (the PUT carries no `api_key`).
 */

const ADMIN = { username: "admin", password: "adminpass123" };

async function signIn(page: Page) {
  await page.addInitScript(() => window.localStorage.setItem("contextvault.locale", "en"));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Ask a repository" })).toBeVisible();
}

test("configure a repo, then change its model without re-entering the key", async ({ page }) => {
  await signIn(page);

  // Stub the provider model-list (no live key needed); let everything else be real.
  await page.route(/\/repositories\/[^/]+\/llm-models$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ models: ["gpt-4o", "gpt-4o-mini"] }),
    });
  });

  // Capture the config PUTs (but let them hit the real backend so the key is stored).
  const puts: Array<{ provider: string; model: string; api_key?: string }> = [];
  await page.route(/\/repositories\/[^/]+\/llm-config$/, async (route: Route) => {
    if (route.request().method() === "PUT") puts.push(route.request().postDataJSON());
    await route.continue();
  });

  // A fresh repository.
  const repoName = `Config E2E ${Date.now()}`;
  await page.getByRole("link", { name: "Repositories" }).click();
  await page.getByLabel("Repository name").fill(repoName);
  await page.getByRole("button", { name: "Create repository" }).click();
  const row = page.locator("li.repo-item", { hasText: repoName });
  await expect(row).toBeVisible();

  // --- First configure: provider + key + a model chosen from the dropdown. ---
  await row.getByRole("button", { name: "Configure" }).click();
  await row.getByLabel("Provider").selectOption("openai");
  await row.getByLabel("API key").fill("sk-test-key-123");
  await row.getByRole("button", { name: "Load models" }).click();

  // The model is a single <select> (no free-text input).
  const modelSelect = row.getByLabel("Model");
  await expect(modelSelect).toBeVisible();
  await modelSelect.selectOption("gpt-4o");
  await row.getByRole("button", { name: "Save configuration" }).click();
  await expect(row.getByText(/saved/i)).toBeVisible();

  // The first save carried the entered key.
  expect(puts[0]).toEqual({ provider: "openai", model: "gpt-4o", api_key: "sk-test-key-123" });

  // --- Key now stored: the panel no longer asks for it. ---
  await expect(row.getByLabel("API key")).toHaveCount(0);
  await expect(row.getByRole("button", { name: "Replace key" })).toBeVisible();
  // The model dropdown auto-loaded with the saved model preselected.
  await expect(modelSelect).toHaveValue("gpt-4o");

  // --- Change the model and save again — no key re-entry. ---
  await modelSelect.selectOption("gpt-4o-mini");
  await row.getByRole("button", { name: "Save configuration" }).click();
  await expect(row.getByText(/saved/i)).toBeVisible();

  // The second save changed the model and carried NO api_key (stored key kept).
  expect(puts).toHaveLength(2);
  expect(puts[1]).toEqual({ provider: "openai", model: "gpt-4o-mini" });
  expect(puts[1].api_key).toBeUndefined();
});
