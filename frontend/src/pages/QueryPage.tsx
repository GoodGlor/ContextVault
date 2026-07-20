import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ApiError } from "../api/client";
import { listRepositories, type Repository } from "../api/repositories";
import { queryRepository, type QueryResult } from "../api/query";
import { QueryTurn } from "../components/QueryTurn";

interface Turn {
  id: string;
  question: string;
  result: QueryResult;
}

/** The core user experience: pick a granted repo, ask, get a cited answer. */
export function QueryPage(): ReactNode {
  const [repos, setRepos] = useState<Repository[] | null>(null);
  const [reposError, setReposError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const turnSeq = useRef(0);

  useEffect(() => {
    let cancelled = false;
    listRepositories()
      .then((rs) => {
        if (cancelled) return;
        setRepos(rs);
        if (rs.length > 0) setSelected(rs[0].id);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setReposError(err instanceof ApiError ? err.detail : "Failed to load repositories.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onAsk = async (e: FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (selected === "" || q === "") return;
    setAsking(true);
    setAskError(null);
    try {
      const result = await queryRepository(selected, q);
      turnSeq.current += 1;
      setTurns((prev) => [...prev, { id: String(turnSeq.current), question: q, result }]);
      setQuestion("");
    } catch (err) {
      setAskError(err instanceof ApiError ? err.detail : "Something went wrong. Try again.");
    } finally {
      setAsking(false);
    }
  };

  if (reposError !== null) {
    return (
      <div className="page">
        <p className="form-error" role="alert">
          {reposError}
        </p>
      </div>
    );
  }

  if (repos === null) {
    return (
      <div className="page">
        <p>Loading your repositories…</p>
      </div>
    );
  }

  if (repos.length === 0) {
    return (
      <div className="page">
        <h1>Ask a repository</h1>
        <p>You don’t have access to any repositories yet. Ask an admin for a grant.</p>
      </div>
    );
  }

  return (
    <div className="page query-page">
      <h1>Ask a repository</h1>

      <div className="conversation">
        {turns.map((turn) => (
          <QueryTurn key={turn.id} question={turn.question} result={turn.result} />
        ))}
      </div>

      <form className="ask-form" onSubmit={onAsk}>
        <label>
          Repository
          <select
            aria-label="Repository"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Question
          <textarea
            aria-label="Question"
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask anything grounded in this repository…"
            required
          />
        </label>
        {askError !== null && (
          <p className="form-error" role="alert">
            {askError}
          </p>
        )}
        <button type="submit" disabled={asking}>
          {asking ? "Asking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
