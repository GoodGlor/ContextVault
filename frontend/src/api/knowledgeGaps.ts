import { api } from "./client";

// Mirrors KnowledgeGapResponse in src/contextvault/api/knowledge_gaps.py.
export interface KnowledgeGap {
  question: string;
  ask_count: number;
  user_count: number;
  last_asked_at: string;
}

/** Ranked knowledge gaps for a repository (admin-only): questions the vault
 *  could not answer, aggregated and most-asked first. */
export function listKnowledgeGaps(repositoryId: string, limit = 50): Promise<KnowledgeGap[]> {
  return api.get<KnowledgeGap[]>(`/repositories/${repositoryId}/knowledge-gaps?limit=${limit}`);
}
