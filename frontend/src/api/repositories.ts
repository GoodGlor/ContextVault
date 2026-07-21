import { api } from "./client";

// Mirrors RepositoryResponse in src/contextvault/api/repositories.py.
export interface Repository {
  id: string;
  name: string;
  description: string | null;
}

/** The repositories the current user can actively reach (their granted picker). */
export function listRepositories(): Promise<Repository[]> {
  return api.get<Repository[]>("/repositories");
}

// --- admin repository management (card #37) ---------------------------------

// Mirrors LLMProviderName in src/contextvault/models/enums.py.
export type LLMProvider = "gemini" | "openai" | "openrouter" | "anthropic";

/** Display labels for the provider select, in a stable order. */
export const LLM_PROVIDERS: { value: LLMProvider; label: string }[] = [
  { value: "anthropic", label: "Anthropic" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Google (Gemini)" },
  { value: "openrouter", label: "OpenRouter" },
];

// Mirrors AdminRepositoryResponse in src/contextvault/api/repositories.py.
export interface AdminRepository {
  id: string;
  name: string;
  description: string | null;
  configured: boolean;
}

// Mirrors LLMConfigResponse in src/contextvault/api/repositories.py. The key is
// write-only: it comes back only masked, never in full after entry.
export interface LLMConfig {
  provider: LLMProvider | null;
  model: string | null;
  api_key_masked: string | null;
  configured: boolean;
}

/** Every repository with its config state (admin-only). */
export function listAllRepositories(): Promise<AdminRepository[]> {
  return api.get<AdminRepository[]>("/admin/repositories");
}

/** Create a repository (admin-only); it starts unconfigured. */
export function createRepository(input: {
  name: string;
  description?: string | null;
}): Promise<AdminRepository> {
  return api.post<AdminRepository>("/repositories", input);
}

/** Update a repository's name and/or description (admin-only). Omitted fields are
 *  left unchanged; `description: null` clears it. */
export function updateRepository(
  id: string,
  input: { name?: string; description?: string | null },
): Promise<AdminRepository> {
  return api.patch<AdminRepository>(`/repositories/${id}`, input);
}

/** Delete a repository (admin-only); the caller must echo its name. Its sources,
 *  chunks, and grants cascade away with it. */
export function deleteRepository(id: string, confirmName: string): Promise<void> {
  return api.del<void>(`/repositories/${id}`, { confirm_name: confirmName });
}

/** Read a repository's LLM config (key masked; nulls if unconfigured). */
export function getLlmConfig(repositoryId: string): Promise<LLMConfig> {
  return api.get<LLMConfig>(`/repositories/${repositoryId}/llm-config`);
}

/** Set (or replace) a repository's LLM provider/model/key. */
export function setLlmConfig(
  repositoryId: string,
  input: { provider: LLMProvider; model: string; api_key: string },
): Promise<LLMConfig> {
  return api.put<LLMConfig>(`/repositories/${repositoryId}/llm-config`, input);
}
