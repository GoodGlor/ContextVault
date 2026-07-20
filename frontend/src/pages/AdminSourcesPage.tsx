import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import { ApiError } from "../api/client";
import { listAllRepositories, type AdminRepository } from "../api/repositories";
import { deleteSource, isIngesting, listSources, uploadSource, type Source } from "../api/sources";

/** How often to re-poll while any source is still being ingested. */
export const SOURCE_POLL_MS = 2000;

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** Admin surface for a repository's sources: upload, watch ingestion, delete. */
export function AdminSourcesPage(): ReactNode {
  const [repos, setRepos] = useState<AdminRepository[] | null>(null);
  const [reposError, setReposError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");

  const [sources, setSources] = useState<Source[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

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
          !cancelled && setReposError(errorMessage(err, "Failed to load repositories.")),
      );
    return () => {
      cancelled = true;
    };
  }, []);

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
          !cancelled && setSourcesError(errorMessage(err, "Failed to load sources.")),
      );
    return () => {
      cancelled = true;
    };
  }, [selected]);

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
    setFile(e.target.files?.[0] ?? null);
  };

  const onUpload = async (e: FormEvent) => {
    e.preventDefault();
    if (selected === "" || file === null) return;
    setUploading(true);
    setUploadError(null);
    try {
      const created = await uploadSource(selected, file);
      setSources((prev) => [...(prev ?? []), created]);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    } catch (err) {
      setUploadError(errorMessage(err, "Upload failed."));
    } finally {
      setUploading(false);
    }
  };

  const onDelete = async (id: string) => {
    try {
      await deleteSource(id);
      setSources((prev) => prev?.filter((s) => s.id !== id) ?? prev);
    } catch (err) {
      setSourcesError(errorMessage(err, "Could not delete the source."));
    }
  };

  if (reposError !== null) {
    return <p className="error">{reposError}</p>;
  }
  if (repos === null) {
    return <p>Loading repositories…</p>;
  }
  if (repos.length === 0) {
    return <p>No repositories yet. Create one under Repositories first.</p>;
  }

  return (
    <section className="admin-sources">
      <h1>Sources</h1>

      <label htmlFor="source-repo">Repository</label>
      <select id="source-repo" value={selected} onChange={(e) => setSelected(e.target.value)}>
        {repos.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>

      <form className="source-upload" onSubmit={onUpload}>
        <label htmlFor="source-file">Document</label>
        <input id="source-file" type="file" ref={fileInput} onChange={onFileChange} />
        <button type="submit" disabled={uploading || file === null}>
          Upload
        </button>
        {uploadError !== null && <p className="error">{uploadError}</p>}
      </form>

      {sourcesError !== null && <p className="error">{sourcesError}</p>}
      {sources === null ? (
        <p>Loading sources…</p>
      ) : sources.length === 0 ? (
        <p>No sources yet. Upload a document to get started.</p>
      ) : (
        <ul className="source-list">
          {sources.map((s) => (
            <li key={s.id} className="source-item">
              <span className="source-title">{s.title}</span>
              <span className={`badge status-${s.status}`}>{s.status}</span>
              {s.status === "failed" && s.ingest_error !== null && (
                <span className="source-error">{s.ingest_error}</span>
              )}
              <button type="button" onClick={() => onDelete(s.id)}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
