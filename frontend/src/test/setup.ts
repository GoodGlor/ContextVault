import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach } from "vitest";
import { cleanup } from "@testing-library/react";
import i18n from "../i18n";

// Node 25 exposes a non-functional experimental `localStorage` global that shadows
// jsdom's, so we install a small in-memory Storage the app can actually read/write.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();
  get length(): number {
    return this.store.size;
  }
  clear(): void {
    this.store.clear();
  }
  getItem(key: string): string | null {
    return this.store.has(key) ? (this.store.get(key) as string) : null;
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

const storage = new MemoryStorage();
for (const target of [globalThis, window]) {
  Object.defineProperty(target, "localStorage", {
    value: storage,
    configurable: true,
    writable: true,
  });
}

// The app defaults to Ukrainian; the existing suite asserts on English strings, so
// pin the test locale to English before each test (kept in sync via i18n keys).
beforeEach(() => {
  void i18n.changeLanguage("en");
});

// Unmount React trees and reset storage between tests so no state leaks across them.
afterEach(() => {
  cleanup();
  localStorage.clear();
});
