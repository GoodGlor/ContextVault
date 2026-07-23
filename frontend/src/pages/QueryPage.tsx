import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import { listRepositories, type Repository } from "../api/repositories";
import { queryRepository, type QueryResult } from "../api/query";
import { clearConversation, getConversation } from "../api/conversations";
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
  const bottomRef = useRef<HTMLDivElement>(null);

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

  // Keep the newest message in view as the conversation grows. (scrollIntoView is
  // unimplemented in jsdom; the optional call no-ops in tests.)
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [turns, asking]);

  // A conversation belongs to one repository. Switching repos changes `selected`;
  // the effect below loads that repository's own saved conversation (or starts
  // fresh when it has none), so stale history from the previous repo never leaks in.
  const onSelectRepo = (id: string) => {
    setSelected(id);
    setAskError(null);
  };

  // Restore this user's saved conversation for the selected repository — this
  // runs on mount (once a repo is auto-selected) and again on every repo switch.
  useEffect(() => {
    if (selected === "") return;
    let cancelled = false;
    setAskError(null);
    getConversation(selected)
      .then((c) => {
        if (cancelled) return;
        setTurns(
          c.turns.map((t, i) => ({
            id: `saved-${i}`,
            question: t.question,
            result: {
              answer: t.answer,
              not_in_vault: t.not_in_vault,
              citations: t.citations,
              sources: t.sources,
            },
            repositoryId: selected,
          })),
        );
        turnSeq.current = c.turns.length;
      })
      .catch(() => {
        if (!cancelled) setTurns([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const submit = async () => {
    const q = question.trim();
    if (selected === "" || q === "" || asking) return;
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

  // Delete this user's saved thread for the selected repository and reset the
  // on-screen transcript to match.
  const onClear = async () => {
    if (selected === "") return;
    try {
      await clearConversation(selected);
      setTurns([]);
      turnSeq.current = 0;
    } catch (err) {
      setAskError(err instanceof ApiError ? err.detail : t("common.somethingWrong"));
    }
  };

  const onAsk = (e: FormEvent) => {
    e.preventDefault();
    void submit();
  };

  // Enter sends; Shift+Enter inserts a newline (standard chat-composer behaviour).
  const onComposerKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
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
    <div className="page query-page chat">
      <div className="chat-header">
        <h1>{t("query.title")}</h1>
        <label className="chat-repo">
          {t("query.repository")}
          <select
            aria-label={t("query.repository")}
            value={selected}
            onChange={(e) => onSelectRepo(e.target.value)}
          >
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </label>
        {turns.length > 0 && (
          <button type="button" className="chat-clear" onClick={() => void onClear()}>
            {t("query.clearConversation")}
          </button>
        )}
      </div>

      <div className="chat-log">
        {turns.length === 0 && !asking && <p className="chat-empty">{t("query.emptyChat")}</p>}
        {turns.map((turn) => (
          <QueryTurn
            key={turn.id}
            question={turn.question}
            result={turn.result}
            repositoryId={turn.repositoryId}
          />
        ))}
        {asking && (
          <div className="turn">
            <div className="msg-row assistant">
              <div className="bubble thinking" role="status">
                {t("query.asking")}
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form className="chat-composer" onSubmit={onAsk}>
        {askError !== null && (
          <p className="form-error" role="alert">
            {askError}
          </p>
        )}
        <div className="composer-row">
          <textarea
            aria-label={t("query.question")}
            rows={1}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onComposerKeyDown}
            placeholder={t("query.messagePlaceholder")}
          />
          <button type="submit" disabled={asking || question.trim() === ""}>
            {asking ? t("query.asking") : t("query.send")}
          </button>
        </div>
      </form>
    </div>
  );
}
