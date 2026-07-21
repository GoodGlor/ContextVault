import { useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import type { Citation, SourceReference } from "../api/query";
import { getSourceContent } from "../api/sources";

/** Format the citation character spans that point at a given source. */
function spansFor(sourceId: string, citations: Citation[]): string {
  const spans = citations
    .filter((c) => c.source_id === sourceId && c.char_start !== null && c.char_end !== null)
    .map((c) => `${c.char_start}–${c.char_end}`);
  return spans.length ? `chars ${spans.join(", ")}` : "";
}

/** Map each source to the citation numbers that reference it, in ascending order. */
function numbersFor(sourceId: string, citations: Citation[]): number[] {
  return citations
    .filter((c) => c.source_id === sourceId)
    .map((c) => c.number)
    .sort((a, b) => a - b);
}

/**
 * The cited sources for one answer. `highlightedId` (set when the user clicks an
 * inline citation) marks the matching entry; each entry carries a ref so the page
 * can scroll it into view. Each source can also load its raw passage on demand from
 * the user-scoped source-content endpoint (card #90).
 */
export function SourceList({
  sources,
  citations,
  highlightedId,
  registerRef,
  repositoryId,
}: {
  sources: SourceReference[];
  citations: Citation[];
  highlightedId: string | null;
  registerRef: (id: string, el: HTMLLIElement | null) => void;
  repositoryId: string;
}): ReactNode {
  if (sources.length === 0) return null;
  return (
    <div className="sources">
      <h3>Sources</h3>
      <ul>
        {sources.map((source) => (
          <SourceItem
            key={source.id}
            source={source}
            citations={citations}
            highlighted={source.id === highlightedId}
            registerRef={registerRef}
            repositoryId={repositoryId}
          />
        ))}
      </ul>
    </div>
  );
}

function SourceItem({
  source,
  citations,
  highlighted,
  registerRef,
  repositoryId,
}: {
  source: SourceReference;
  citations: Citation[];
  highlighted: boolean;
  registerRef: (id: string, el: HTMLLIElement | null) => void;
  repositoryId: string;
}): ReactNode {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onView = async () => {
    if (content !== null || loading) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getSourceContent(repositoryId, source.id);
      setContent(result.content ?? "(this source has no stored text)");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load the passage.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <li
      ref={(el) => registerRef(source.id, el)}
      className={highlighted ? "source-item highlighted" : "source-item"}
      data-testid={`source-${source.id}`}
    >
      <span className="source-numbers">
        {numbersFor(source.id, citations).map((n) => (
          <span key={n} className="citation-chip static">
            {n}
          </span>
        ))}
      </span>
      <span className="source-body">
        <span className="source-title">
          {source.title}
          {source.verified && <span className="verified-badge">Verified</span>}
        </span>
        <span className="source-meta">
          {source.author ? `by ${source.author} · ` : ""}
          {source.original_filename ?? source.kind}
          {spansFor(source.id, citations) ? ` · ${spansFor(source.id, citations)}` : ""}
        </span>
        <button type="button" className="view-passage" onClick={onView} disabled={loading}>
          {loading ? "Loading…" : "View passage"}
        </button>
        {content !== null && <p className="source-passage">{content}</p>}
        {error !== null && <p className="error">{error}</p>}
      </span>
    </li>
  );
}
