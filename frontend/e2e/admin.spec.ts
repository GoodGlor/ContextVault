import { test, expect } from "@playwright/test";

/**
 * End-to-end: an admin signs in and manages a repository against the *real* stack
 * (backend + Postgres + frontend, brought up by `./dev.sh`). Exercises the full
 * chain — JWT auth, the admin nav, repository creation, and the live list — through
 * a real browser.
 */

const ADMIN = { username: "admin", password: "adminpass123" };

async function signIn(page: import("@playwright/test").Page) {
  await page.goto("/");
  // Unauthenticated visitors are bounced to the login screen.
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Lands on the query home.
  await expect(page.getByRole("heading", { name: "Ask a repository" })).toBeVisible();
}

test("admin signs in, navigates the admin nav, and creates a repository", async ({ page }) => {
  await signIn(page);

  // Navigate via the (now spaced) admin nav.
  await page.getByRole("link", { name: "Repositories" }).click();
  await expect(page.getByRole("heading", { name: "Repositories" })).toBeVisible();

  // Create a uniquely-named repository so reruns never collide.
  const name = `E2E Vault ${Date.now()}`;
  await page.getByLabel("Repository name").fill(name);
  await page.getByLabel("Description").fill("created by the e2e test");
  await page.getByRole("button", { name: "Create repository" }).click();

  // It appears in the live list, flagged as not-yet-configured.
  const row = page.locator("li.repo-item", { hasText: name });
  await expect(row).toBeVisible();
  await expect(row.getByText("Not configured")).toBeVisible();

  // Model dropdown (feature B): opening config and clicking "Load models" with a
  // bad key hits the real /llm-models endpoint, which asks the provider and gets
  // rejected — the UI must surface that as a clean error rather than crashing.
  // (This proves the endpoint + button + error wiring end-to-end without needing a
  // valid provider key.)
  await row.getByRole("button", { name: "Configure" }).click();
  await expect(row.getByRole("button", { name: "Load models" })).toBeVisible();
  await row.getByLabel("API key").fill("sk-not-a-real-key");
  await row.getByRole("button", { name: "Load models" }).click();
  await expect(row.getByText(/could not (list|load) models|no api key/i)).toBeVisible({
    timeout: 20_000,
  });
  // Close the panel again so it doesn't interfere with later assertions.
  await row.getByRole("button", { name: "Configure" }).click();

  // The other admin surfaces are reachable and render.
  await page.getByRole("link", { name: "Insights" }).click();
  await expect(page.getByRole("heading", { name: "Insights" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
});
