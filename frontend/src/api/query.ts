import { api } from "./client";

// Mirrors the query schemas in src/contextvault/api/query.py.

export type SourceKind = "document" | "admin_note";

export interface Citation {
  number: number;
  chunk_id: string;
  source_id: string;
  char_start: number | null;
  char_end: number | null;
}

export interface SourceReference {
  id: string;
  title: string;
  original_filename: string | null;
  kind: SourceKind;
  verified: boolean;
  author: string | null;
}

export interface QueryResult {
  answer: string;
  not_in_vault: boolean;
  citations: Citation[];
  sources: SourceReference[];
}

/** Ask a question against one repository; returns the grounded answer + citations. */
export function queryRepository(repositoryId: string, question: string): Promise<QueryResult> {
  return api.post<QueryResult>(`/repositories/${repositoryId}/query`, { question });
}
