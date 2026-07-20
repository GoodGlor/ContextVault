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
