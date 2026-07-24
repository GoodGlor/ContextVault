import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import { listAllRepositories, listRepositories, type Repository } from "../api/repositories";
import { useAuth } from "../auth/AuthContext";
import { RepositoryContext, type RepositoryContextValue } from "./RepositoryContext";

const STORAGE_KEY = "contextvault.currentRepo";

function readStored(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}
function writeStored(id: string): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* localStorage may be unavailable; selection just won't persist */
  }
}

/** Owns the single "current repository" for the app. The sidebar switcher writes
 *  it; repo-scoped pages read it via useCurrentRepository(). Admins see all
 *  repositories, members only granted ones. */
export function RepositoryProvider({ children }: { children: ReactNode }): ReactNode {
  const { t } = useTranslation();
  const { session } = useAuth();
  const isAdmin = session?.role === "admin";

  const [repos, setRepos] = useState<Repository[]>([]);
  const [currentRepoId, setCurrent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const load = isAdmin ? listAllRepositories() : listRepositories();
    load
      .then((rs) => {
        if (cancelled) return;
        setRepos(rs);
        const stored = readStored();
        const next = rs.some((r) => r.id === stored) ? stored : (rs[0]?.id ?? "");
        setCurrent(next);
        writeStored(next);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.detail : t("repository.errorLoad"));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isAdmin, t]);

  const setCurrentRepoId = useCallback((id: string) => {
    setCurrent(id);
    writeStored(id);
  }, []);

  const value = useMemo<RepositoryContextValue>(
    () => ({ repos, currentRepoId, setCurrentRepoId, loading, error }),
    [repos, currentRepoId, setCurrentRepoId, loading, error],
  );

  return <RepositoryContext.Provider value={value}>{children}</RepositoryContext.Provider>;
}
