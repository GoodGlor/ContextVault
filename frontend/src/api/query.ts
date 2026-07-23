import { api } from "./client";
import type { SourceKind } from "./sources";

// Mirrors the query schemas in src/contextvault/api/query.py.

export type { SourceKind };

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

/** One prior exchange sent back so a follow-up question resolves its references. */
export interface ConversationTurnInput {
  question: string;
  answer: string;
}

/** Ask a question against one repository; returns the grounded answer + citations.
 *
 * ``history`` is the prior turns of this conversation (oldest first). The backend
 * uses them as context for a follow-up question — they never become citable
 * sources — and keeps only the most recent turns. */
export function queryRepository(
  repositoryId: string,
  question: string,
  history: ConversationTurnInput[] = [],
): Promise<QueryResult> {
  return api.post<QueryResult>(`/repositories/${repositoryId}/query`, { question, history });
}
