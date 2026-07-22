import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * End-to-end: an admin adds the two new source kinds — an OCR image and a web
 * link — to a fresh repository, against the *real* stack (backend + Postgres +
 * frontend, brought up by `./dev.sh`). Exercises the upload/add flows, the kind
 * badges, the OCR text-only contract, and the web-link form through a real
 * browser. Run with the stack up: `npm run test:e2e`.
 */

const ADMIN = { username: "admin", password: "adminpass123" };

// A 1x1 transparent PNG: a valid image with no text. OCR extracts nothing, so
// the source must end FAILED with the text-only message — the design contract.
const BLANK_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

// A 16x16 white HEIC (iPhone format): decodable but text-less. If HEIC decoding
// works end-to-end it OCRs to nothing and ends FAILED with the text-only message
// ("No text found in image.") — a decode failure would instead say "Could not
// read image file.", so this fixture proves the pillow-heif path is wired up.
const BLANK_HEIC = Buffer.from(
  "AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWljbWlhZgAAAXxtZXRhAAAAAAAAACFoZGxyAAAAAAAAAABwaWN0AAAAAAAAAAAAAAAAAAAAACJpbG9jAAAAAERAAAEAAQAAAAABoAABAAAAAAAAACsAAAAjaWluZgAAAAAAAQAAABVpbmZlAgAAAAABAABodmMxAAAAAA5waXRtAAAAAAABAAAA/GlwcnAAAADcaXBjbwAAAHVodmNDAQNwAAAAAAAAAAAAHvAA/P34+AAADwNgAAEAGEABDAH//wNwAAADAJAAAAMAAAMAHroCQGEAAQApQgEBA3AAAAMAkAAAAwAAAwAeoCCBBZbqrprm4CGgwIAAAAyAAAADAIRiAAEABkQBwXPBiQAAABNjb2xybmNseAABAA0ABoAAAAAUaXNwZQAAAAAAAABAAAAAQAAAAChjbGFwAAAAEAAAAAEAAAAQAAAAAf///9AAAAAC////0AAAAAIAAAAQcGl4aQAAAAADCAgIAAAAGGlwbWEAAAAAAAAAAQABBYECAwWEAAAAM21kYXQAAAAnKAGvEyExlvhOUKeW/WMCzQyVTFq5T6Vz3QpQk3J+uP7yh5PFYoLg",
  "base64",
);

async function signIn(page: Page): Promise<void> {
  // The app defaults to Ukrainian; pin the browser to English so the English-string
  // assertions below hold. (The language switch itself is covered by unit tests.)
  await page.addInitScript(() => window.localStorage.setItem("contextvault.locale", "en"));
  await page.goto("/");
  // Unauthenticated visitors are bounced to the login screen.
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Username").fill(ADMIN.username);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Ask a repository" })).toBeVisible();
}

async function createRepository(page: Page, name: string): Promise<void> {
  await page.getByRole("link", { name: "Repositories" }).click();
  await expect(page.getByRole("heading", { name: "Repositories" })).toBeVisible();
  await page.getByLabel("Repository name").fill(name);
  await page.getByLabel("Description").fill("created by the sources e2e test");
  await page.getByRole("button", { name: "Create repository" }).click();
  await expect(page.locator("li.repo-item", { hasText: name })).toBeVisible();
}

test("admin adds an image (OCR) source and a web-link source", async ({ page }) => {
  await signIn(page);

  // A uniquely-named repository so reruns never collide.
  const repoName = `E2E Sources ${Date.now()}`;
  await createRepository(page, repoName);

  // Go to Sources and select the repository we just made.
  await page.getByRole("link", { name: "Sources" }).click();
  await expect(page.getByRole("heading", { name: "Sources" })).toBeVisible();
  await page.getByLabel("Repository").selectOption({ label: repoName });

  // The OCR helper note and the web-link form are present on the page.
  await expect(page.getByText(/only text visible in the image is captured/i)).toBeVisible();
  await expect(page.getByLabel("Web link")).toBeVisible();

  // --- Web-link source: adding a URL creates a row tagged `web` that links out. ---
  const url = "https://example.com/article";
  await page.getByLabel("Web link").fill(url);
  await page.getByRole("button", { name: "Add link" }).click();

  const webRow = page.locator("li.source-item", { hasText: url });
  await expect(webRow).toBeVisible();
  await expect(webRow.locator("span.badge.kind-web")).toBeVisible();
  await expect(webRow.getByRole("link", { name: url })).toHaveAttribute("href", url);

  // --- Image source: uploading a text-less image creates a row tagged `image`
  // which OCR must reject per the text-only contract (ends FAILED). ---
  await page.getByLabel("Document").setInputFiles({
    name: "blank.png",
    mimeType: "image/png",
    buffer: BLANK_PNG,
  });
  await page.getByRole("button", { name: "Upload" }).click();

  const imageRow = page.locator("li.source-item", { hasText: "blank.png" });
  await expect(imageRow).toBeVisible();
  await expect(imageRow.locator("span.badge.kind-image")).toBeVisible();

  // The page polls ingestion status; a wordless image fails the OCR text-only
  // contract, so the row lands on FAILED with the expected message.
  await expect(imageRow.locator("span.badge.status-failed")).toBeVisible({ timeout: 20_000 });
  await expect(imageRow.getByText("No text found in image.")).toBeVisible();

  // --- HEIC source: an iPhone-format image is classified `image` and decoded
  // via pillow-heif. This one is text-less, so it too must reach the OCR
  // text-only failure ("No text found in image.") — proving HEIC decodes rather
  // than erroring at "Could not read image file." ---
  await page.getByLabel("Document").setInputFiles({
    name: "photo.heic",
    mimeType: "image/heic",
    buffer: BLANK_HEIC,
  });
  await page.getByRole("button", { name: "Upload" }).click();

  const heicRow = page.locator("li.source-item", { hasText: "photo.heic" });
  await expect(heicRow).toBeVisible();
  await expect(heicRow.locator("span.badge.kind-image")).toBeVisible();
  await expect(heicRow.locator("span.badge.status-failed")).toBeVisible({ timeout: 20_000 });
  await expect(heicRow.getByText("No text found in image.")).toBeVisible();
});
