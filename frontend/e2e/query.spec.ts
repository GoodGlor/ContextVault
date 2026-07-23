import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

/**
 * End-to-end: the query page as a chat with memory.
 *
 * Everything runs against the *real* stack (auth, repository creation, the grant,
 * and the granted-repository listing all hit Postgres) — except the one piece that
 * would otherwise require a live, non-deterministic LLM: the `/query` call the
 * browser makes is intercepted at the browser boundary and fulfilled with a canned
 * grounded answer. That keeps the test reliable while still proving, in a real
 * browser, that (a) the exchange renders as chat bubbles and (b) a follow-up
 * question carries the running conversation history. Backend memory threading
 * (the "Conversation so far" preamble, retrieval contextualisation) is covered by
 * the pytest suite.
 */

const ADMIN = { username: "admin", password: "adminpass123" };

async function signIn(page: Page) {
  // The app defaults to Ukrainian; pin English so the string assertions hold.
  await page.addInitScript(() => window.localStorage.setItem("contextvault.locale", "en"));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Ask a repository" })).toBeVisible();
}

function answer(text: string, sourceTitle: string) {
  return {
    answer: `${text} [1].`,
    not_in_vault: false,
    citations: [{ number: 1, chunk_id: "c1", source_id: "src-1", char_start: 0, char_end: 20 }],
    sources: [
      {
        id: "src-1",
        title: sourceTitle,
        original_filename: "policy.pdf",
        kind: "document",
        verified: false,
        author: null,
      },
    ],
  };
}

test("query page is a chat, and a follow-up carries conversation history", async ({ page }) => {
  await signIn(page);

  // --- Real backend setup: a repository the admin is granted on, so it shows up
  // in the query picker. ---
  const repoName = `Chat E2E ${Date.now()}`;
  await page.getByRole("link", { name: "Repositories" }).click();
  await page.getByLabel("Repository name").fill(repoName);
  await page.getByRole("button", { name: "Create repository" }).click();
  await expect(page.locator("li.repo-item", { hasText: repoName })).toBeVisible();

  // Grant the admin access to the new repo (Users page → Repository access).
  await page.getByRole("link", { name: "Users" }).click();
  const access = page.getByRole("region", { name: "Repository access" });
  await access.getByLabel("Repository").selectOption({ label: repoName });
  await access.getByLabel("Grant to").selectOption({ label: "admin" });
  await access.getByRole("button", { name: "Grant access" }).click();
  // The grant now lists the admin.
  await expect(access.getByRole("list")).toContainText("admin");

  // --- Intercept the LLM-backed /query call with a deterministic answer, and
  // record the request bodies so we can assert the history sent on the follow-up. ---
  const bodies: Array<{ question: string; history: Array<{ question: string; answer: string }> }> =
    [];
  const answers = [
    answer("The retention period is 30 days", "Retention Policy"),
    answer("Part-timers receive pro-rated leave", "Retention Policy"),
  ];
  await page.route(/\/repositories\/[^/]+\/query$/, async (route: Route) => {
    bodies.push(route.request().postDataJSON());
    const payload = answers[Math.min(bodies.length - 1, answers.length - 1)];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });

  // Back to the chat (SPA nav via the brand link preserves the session).
  await page.locator(".app-brand").click();
  await expect(page.getByRole("heading", { name: "Ask a repository" })).toBeVisible();
  await page.getByLabel("Repository").selectOption({ label: repoName });

  // --- First turn: ask, and see the exchange render as chat bubbles. ---
  const composer = page.getByLabel("Question");
  await composer.fill("How long is retention?");
  await page.getByRole("button", { name: "Send" }).click();

  // The question renders as a user bubble, the answer as an assistant bubble with
  // its cited source.
  await expect(
    page.locator(".msg-row.user .bubble", { hasText: "How long is retention?" }),
  ).toBeVisible();
  await expect(
    page.locator(".msg-row.assistant .bubble", { hasText: "The retention period is 30 days" }),
  ).toBeVisible();
  await expect(page.getByText("Retention Policy")).toBeVisible();

  // --- Follow-up turn: the running history must travel with it. ---
  await composer.fill("and for part-timers?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(
    page.locator(".msg-row.assistant .bubble", { hasText: "Part-timers receive pro-rated leave" }),
  ).toBeVisible();

  // Both prior bubbles are still on screen — this is a conversation, not a reset.
  await expect(
    page.locator(".msg-row.user .bubble", { hasText: "How long is retention?" }),
  ).toBeVisible();

  // The first request carried no history; the follow-up carried the first Q&A.
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toEqual({ question: "How long is retention?", history: [] });
  expect(bodies[1]).toEqual({
    question: "and for part-timers?",
    history: [
      { question: "How long is retention?", answer: "The retention period is 30 days [1]." },
    ],
  });
});
