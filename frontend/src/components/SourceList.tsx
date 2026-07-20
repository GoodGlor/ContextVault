import type { ReactNode } from "react";
import type { Citation, SourceReference } from "../api/query";

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
 * can scroll it into view. The backend exposes no user-facing source-content
 * endpoint yet, so we surface the citation's char span rather than the raw passage.
 */
export function SourceList({
  sources,
  citations,
  highlightedId,
  registerRef,
}: {
  sources: SourceReference[];
  citations: Citation[];
  highlightedId: string | null;
  registerRef: (id: string, el: HTMLLIElement | null) => void;
}): ReactNode {
  if (sources.length === 0) return null;
  return (
    <div className="sources">
      <h3>Sources</h3>
      <ul>
        {sources.map((source) => (
          <li
            key={source.id}
            ref={(el) => registerRef(source.id, el)}
            className={source.id === highlightedId ? "source-item highlighted" : "source-item"}
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
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
