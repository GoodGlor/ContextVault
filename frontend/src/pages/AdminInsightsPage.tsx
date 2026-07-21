import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ApiError } from "../api/client";
import { listAllRepositories, type AdminRepository } from "../api/repositories";
import { listKnowledgeGaps, type KnowledgeGap } from "../api/knowledgeGaps";
import { getAnalytics, type AnalyticsOverview } from "../api/analytics";
import { createAdminNote } from "../api/adminNotes";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** The curation cockpit: knowledge gaps → Admin Notes → usage analytics (card #40). */
export function AdminInsightsPage(): ReactNode {
  const [repos, setRepos] = useState<AdminRepository[] | null>(null);
  const [reposError, setReposError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listAllRepositories()
      .then((rs) => !cancelled && setRepos(rs))
      .catch(
        (err: unknown) =>
          !cancelled && setReposError(errorMessage(err, "Failed to load repositories.")),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  if (reposError !== null) return <p className="error">{reposError}</p>;
  if (repos === null) return <p>Loading…</p>;

  return (
    <div className="admin-insights">
      <h1>Insights</h1>
      <KnowledgeGapsPanel repos={repos} />
      <AnalyticsPanel />
    </div>
  );
}

/** Ranked knowledge gaps for a repo, each answerable inline with an Admin Note. */
function KnowledgeGapsPanel({ repos }: { repos: AdminRepository[] }): ReactNode {
  const [selected, setSelected] = useState(repos[0]?.id ?? "");
  const [gaps, setGaps] = useState<KnowledgeGap[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answered, setAnswered] = useState<string | null>(null);

  useEffect(() => {
    if (selected === "") return;
    let cancelled = false;
    setGaps(null);
    setError(null);
    setAnswered(null);
    listKnowledgeGaps(selected)
      .then((g) => !cancelled && setGaps(g))
      .catch((err: unknown) => !cancelled && setError(errorMessage(err, "Failed to load gaps.")));
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const onAnswered = (question: string) => {
    // The gap closes once the note is ingested; drop it from the to-do list now.
    setGaps((prev) => prev?.filter((g) => g.question !== question) ?? prev);
    setAnswered(question);
  };

  return (
    <section aria-label="Knowledge gaps">
      <h2>Knowledge gaps</h2>
      <label htmlFor="gap-repo">Repository</label>
      <select id="gap-repo" value={selected} onChange={(e) => setSelected(e.target.value)}>
        {repos.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>

      {answered !== null && (
        <p className="success">
          Admin Note saved for “{answered}”. It will close the gap once ingested.
        </p>
      )}
      {error !== null && <p className="error">{error}</p>}

      {gaps === null ? (
        <p>Loading gaps…</p>
      ) : gaps.length === 0 ? (
        <p>No knowledge gaps — every asked question was answerable. 🎉</p>
      ) : (
        <ul className="gap-list">
          {gaps.map((gap) => (
            <GapRow
              key={gap.question}
              gap={gap}
              repositoryId={selected}
              onAnswered={() => onAnswered(gap.question)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function GapRow({
  gap,
  repositoryId,
  onAnswered,
}: {
  gap: KnowledgeGap;
  repositoryId: string;
  onAnswered: () => void;
}): ReactNode {
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSave = async (e: FormEvent) => {
    e.preventDefault();
    if (content.trim() === "") return;
    setSaving(true);
    setError(null);
    try {
      await createAdminNote(repositoryId, { title: gap.question, content: content.trim() });
      onAnswered();
    } catch (err) {
      setError(errorMessage(err, "Could not save the Admin Note."));
      setSaving(false);
    }
  };

  const lastAsked = new Date(gap.last_asked_at).toLocaleDateString();
  const titleId = `note-title-${gap.question}`;
  const answerId = `note-answer-${gap.question}`;

  return (
    <li className="gap-item">
      <span className="gap-question">{gap.question}</span>
      <span className="gap-signal">
        asked {gap.ask_count}× by {gap.user_count} {gap.user_count === 1 ? "user" : "users"} · last{" "}
        {lastAsked}
      </span>
      {!editing ? (
        <button type="button" onClick={() => setEditing(true)}>
          Answer this gap
        </button>
      ) : (
        <form className="note-editor" onSubmit={onSave}>
          <label htmlFor={titleId}>Note title</label>
          <input id={titleId} value={gap.question} readOnly />
          <label htmlFor={answerId}>Answer</label>
          <textarea id={answerId} value={content} onChange={(e) => setContent(e.target.value)} />
          <button type="submit" disabled={saving || content.trim() === ""}>
            Save Admin Note
          </button>
          {error !== null && <p className="error">{error}</p>}
        </form>
      )}
    </li>
  );
}

/** Read-only usage dashboard over GET /analytics. */
function AnalyticsPanel(): ReactNode {
  const [data, setData] = useState<AnalyticsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAnalytics()
      .then((d) => !cancelled && setData(d))
      .catch(
        (err: unknown) => !cancelled && setError(errorMessage(err, "Failed to load analytics.")),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section aria-label="Analytics">
      <h2>Analytics</h2>
      {error !== null ? (
        <p className="error">{error}</p>
      ) : data === null ? (
        <p>Loading analytics…</p>
      ) : (
        <>
          <div className="stat-row">
            <Stat label="Total queries" value={data.total_queries} />
            <Stat label="Answered" value={data.answered} />
            <Stat label="Not in vault" value={data.not_in_vault} />
            <Stat label="Gap rate" value={`${Math.round(data.not_in_vault_rate * 100)}%`} />
          </div>

          <h3>Per repository</h3>
          {data.per_repository.length === 0 ? (
            <p>No queries yet.</p>
          ) : (
            <table className="analytics-table">
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Queries</th>
                  <th>Gaps</th>
                </tr>
              </thead>
              <tbody>
                {data.per_repository.map((r) => (
                  <tr key={r.repository_id}>
                    <td>{r.repository_name}</td>
                    <td>{r.query_count}</td>
                    <td>{r.not_in_vault_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3>Top questions</h3>
          <ul className="top-questions">
            {data.top_questions.map((q) => (
              <li key={q.question}>
                <span className="q-text">{q.question}</span>
                <span className="q-count">{q.ask_count}×</span>
              </li>
            ))}
          </ul>

          <h3>Most active users</h3>
          <ul className="active-users">
            {data.active_users.map((u) => (
              <li key={u.user_id}>
                <span className="u-name">{u.username}</span>
                <span className="u-count">{u.query_count} queries</span>
              </li>
            ))}
          </ul>

          <h3>By day</h3>
          <ul className="by-day">
            {data.by_day.map((d) => (
              <li key={d.day}>
                {d.day}: {d.total} total, {d.not_in_vault} gaps
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number | string }): ReactNode {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}
