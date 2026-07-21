import { api } from "./client";

// Mirrors the source schemas in src/contextvault/api/sources.py.

export type SourceKind = "document" | "admin_note";

// Mirrors SourceStatus in src/contextvault/models/enums.py — the ingestion
// pipeline state (parse→chunk→embed→store).
export type SourceStatus = "pending" | "processing" | "done" | "failed";

/** A non-terminal status is still being ingested, so the UI keeps polling it. */
export function isIngesting(status: SourceStatus): boolean {
  return status === "pending" || status === "processing";
}

export interface Source {
  id: string;
  repository_id: string;
  kind: SourceKind;
  title: string;
  original_filename: string | null;
  status: SourceStatus;
  ingest_error: string | null;
  created_at: string;
}

/** List a repository's sources, oldest first (admin-only). */
export function listSources(repositoryId: string): Promise<Source[]> {
  return api.get<Source[]>(`/repositories/${repositoryId}/sources`);
}

/** Fetch a single source, including its ingestion status (admin-only). */
export function getSource(sourceId: string): Promise<Source> {
  return api.get<Source>(`/sources/${sourceId}`);
}

// Mirrors SourceContentResponse in src/contextvault/api/sources.py.
export interface SourceContent {
  id: string;
  repository_id: string;
  title: string;
  kind: SourceKind;
  content: string | null;
}

/** Read a cited source's passage text — any authenticated user with an active
 *  grant on the repository (403 otherwise). */
export function getSourceContent(repositoryId: string, sourceId: string): Promise<SourceContent> {
  return api.get<SourceContent>(`/repositories/${repositoryId}/sources/${sourceId}`);
}

/** Upload a document to a repository; ingestion runs in the background. */
export function uploadSource(repositoryId: string, file: File): Promise<Source> {
  const form = new FormData();
  form.append("file", file);
  return api.upload<Source>(`/repositories/${repositoryId}/sources`, form);
}

/** Delete a source; its chunks cascade away with it (admin-only). */
export function deleteSource(sourceId: string): Promise<void> {
  return api.del<void>(`/sources/${sourceId}`);
}
