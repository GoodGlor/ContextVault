import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import { listRepositories, type Repository } from "../api/repositories";
import { queryRepository, type QueryResult } from "../api/query";
import { QueryTurn } from "../components/QueryTurn";

interface Turn {
  id: string;
  question: string;
  result: QueryResult;
  repositoryId: string;
}

/** The core user experience: pick a granted repo, ask, get a cited answer. */
export function QueryPage(): ReactNode {
  const { t } = useTranslation();
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
        setReposError(err instanceof ApiError ? err.detail : t("query.failedRepos"));
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  const onAsk = async (e: FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (selected === "" || q === "") return;
    setAsking(true);
    setAskError(null);
    try {
      const result = await queryRepository(selected, q);
      turnSeq.current += 1;
      setTurns((prev) => [
        ...prev,
        { id: String(turnSeq.current), question: q, result, repositoryId: selected },
      ]);
      setQuestion("");
    } catch (err) {
      setAskError(err instanceof ApiError ? err.detail : t("common.somethingWrong"));
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
        <p>{t("query.loadingRepos")}</p>
      </div>
    );
  }

  if (repos.length === 0) {
    return (
      <div className="page">
        <h1>{t("query.title")}</h1>
        <p>{t("query.noAccess")}</p>
      </div>
    );
  }

  return (
    <div className="page query-page">
      <h1>{t("query.title")}</h1>

      <div className="conversation">
        {turns.map((turn) => (
          <QueryTurn
            key={turn.id}
            question={turn.question}
            result={turn.result}
            repositoryId={turn.repositoryId}
          />
        ))}
      </div>

      <form className="ask-form" onSubmit={onAsk}>
        <label>
          {t("query.repository")}
          <select
            aria-label={t("query.repository")}
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
          {t("query.question")}
          <textarea
            aria-label={t("query.question")}
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={t("query.questionPlaceholder")}
            required
          />
        </label>
        {askError !== null && (
          <p className="form-error" role="alert">
            {askError}
          </p>
        )}
        <button type="submit" disabled={asking}>
          {asking ? t("query.asking") : t("query.ask")}
        </button>
      </form>
    </div>
  );
}
