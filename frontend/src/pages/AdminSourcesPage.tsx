import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import { listAllRepositories, type AdminRepository } from "../api/repositories";
import {
  addWebSource,
  deleteSource,
  isIngesting,
  listSources,
  uploadSource,
  type Source,
} from "../api/sources";

/** How often to re-poll while any source is still being ingested. */
export const SOURCE_POLL_MS = 2000;

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** Admin surface for a repository's sources: upload, watch ingestion, delete. */
export function AdminSourcesPage(): ReactNode {
  const { t } = useTranslation();
  const [repos, setRepos] = useState<AdminRepository[] | null>(null);
  const [reposError, setReposError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");

  const [sources, setSources] = useState<Source[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [webUrl, setWebUrl] = useState("");
  const [addingWeb, setAddingWeb] = useState(false);
  const [webError, setWebError] = useState<string | null>(null);

  // Load the admin's full repository list and default to the first one.
  useEffect(() => {
    let cancelled = false;
    listAllRepositories()
      .then((rs) => {
        if (cancelled) return;
        setRepos(rs);
        if (rs.length > 0) setSelected(rs[0].id);
      })
      .catch(
        (err: unknown) =>
          !cancelled && setReposError(errorMessage(err, t("adminSources.errorLoadRepos"))),
      );
    return () => {
      cancelled = true;
    };
  }, [t]);

  // (Re)load sources whenever the selected repository changes.
  useEffect(() => {
    if (selected === "") return;
    let cancelled = false;
    setSources(null);
    setSourcesError(null);
    listSources(selected)
      .then((s) => !cancelled && setSources(s))
      .catch(
        (err: unknown) =>
          !cancelled && setSourcesError(errorMessage(err, t("adminSources.errorLoadSources"))),
      );
    return () => {
      cancelled = true;
    };
  }, [selected, t]);

  // Poll while anything is still ingesting; re-runs (rescheduling) each time the
  // list changes, and stops once every source has reached a terminal state.
  useEffect(() => {
    if (selected === "" || sources === null) return;
    if (!sources.some((s) => isIngesting(s.status))) return;
    const timer = setTimeout(() => {
      listSources(selected)
        .then(setSources)
        .catch(() => {
          /* transient; the next tick retries */
        });
    }, SOURCE_POLL_MS);
    return () => clearTimeout(timer);
  }, [sources, selected]);

  const onFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    setFiles(Array.from(e.target.files ?? []));
  };

  const onUpload = async (e: FormEvent) => {
    e.preventDefault();
    if (selected === "" || files.length === 0) return;
    setUploading(true);
    setUploadError(null);
    // Upload every selected file independently; one failure must not sink the
    // others, so settle them all and append the ones that succeeded.
    const results = await Promise.allSettled(files.map((f) => uploadSource(selected, f)));
    const created = results
      .filter((r): r is PromiseFulfilledResult<Source> => r.status === "fulfilled")
      .map((r) => r.value);
    if (created.length > 0) {
      setSources((prev) => [...(prev ?? []), ...created]);
    }
    const failed = results.length - created.length;
    if (failed > 0) {
      setUploadError(t("adminSources.errorUploadSome", { failed, total: results.length }));
    } else {
      setFiles([]);
      if (fileInput.current) fileInput.current.value = "";
    }
    setUploading(false);
  };

  const onAddWeb = async (e: FormEvent) => {
    e.preventDefault();
    if (selected === "" || webUrl.trim() === "") return;
    setAddingWeb(true);
    setWebError(null);
    try {
      const created = await addWebSource(selected, webUrl.trim());
      setSources((prev) => [...(prev ?? []), created]);
      setWebUrl("");
    } catch (err) {
      setWebError(errorMessage(err, t("adminSources.errorAddLink")));
    } finally {
      setAddingWeb(false);
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteSource(id);
      setSources((prev) => prev?.filter((s) => s.id !== id) ?? prev);
    } catch (err) {
      setSourcesError(errorMessage(err, t("adminSources.errorDelete")));
    }
  };

  if (reposError !== null) {
    return <p className="error">{reposError}</p>;
  }
  if (repos === null) {
    return <p>{t("adminSources.loadingRepos")}</p>;
  }
  if (repos.length === 0) {
    return <p>{t("adminSources.noRepos")}</p>;
  }

  const statusLabels: Record<Source["status"], string> = {
    pending: t("adminSources.statusPending"),
    processing: t("adminSources.statusProcessing"),
    done: t("adminSources.statusDone"),
    failed: t("adminSources.statusFailed"),
  };

  return (
    <section className="admin-sources">
      <h1>{t("adminSources.title")}</h1>

      <label htmlFor="source-repo">{t("adminSources.repositoryLabel")}</label>
      <select id="source-repo" value={selected} onChange={(e) => setSelected(e.target.value)}>
        {repos.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>

      <form className="source-upload" onSubmit={onUpload}>
        <label htmlFor="source-file">{t("adminSources.documentLabel")}</label>
        <input
          id="source-file"
          type="file"
          multiple
          ref={fileInput}
          onChange={onFileChange}
          accept=".txt,.pdf,.docx,.png,.jpg,.jpeg,.webp,.tiff,.bmp,.heic,.heif"
        />
        <p className="form-hint">{t("adminSources.ocrHint")}</p>
        <button type="submit" disabled={uploading || files.length === 0}>
          {files.length > 1
            ? t("adminSources.uploadButtonCount", { n: files.length })
            : t("adminSources.uploadButton")}
        </button>
        {uploadError !== null && <p className="error">{uploadError}</p>}
      </form>

      <form className="source-web" onSubmit={onAddWeb}>
        <label htmlFor="source-url">{t("adminSources.webLinkLabel")}</label>
        <input
          id="source-url"
          type="url"
          placeholder={t("adminSources.urlPlaceholder")}
          value={webUrl}
          onChange={(e) => setWebUrl(e.target.value)}
        />
        <button type="submit" disabled={addingWeb || webUrl.trim() === ""}>
          {t("adminSources.addLinkButton")}
        </button>
        {webError !== null && <p className="error">{webError}</p>}
      </form>

      {sourcesError !== null && <p className="error">{sourcesError}</p>}
      {sources === null ? (
        <p>{t("adminSources.loadingSources")}</p>
      ) : sources.length === 0 ? (
        <p>{t("adminSources.noSources")}</p>
      ) : (
        <ul className="source-list">
          {sources.map((s) => (
            <li key={s.id} className="source-item">
              <span className={`badge kind-${s.kind}`}>{s.kind}</span>
              {s.kind === "web" && s.source_url !== null ? (
                <a className="source-title" href={s.source_url} target="_blank" rel="noreferrer">
                  {s.title}
                </a>
              ) : (
                <span className="source-title">{s.title}</span>
              )}
              <span className={`badge status-${s.status}`}>{statusLabels[s.status]}</span>
              {s.status === "failed" && s.ingest_error !== null && (
                <span className="source-error">{s.ingest_error}</span>
              )}
              <button type="button" onClick={() => onDelete(s.id)}>
                {t("adminSources.deleteButton")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
