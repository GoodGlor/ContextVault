import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ApiError } from "../api/client";
import {
  createRepository,
  deleteRepository,
  getLlmConfig,
  listAllRepositories,
  listModels,
  setLlmConfig,
  updateRepository,
  LLM_PROVIDERS,
  type AdminRepository,
  type LLMConfig,
  type LLMProvider,
} from "../api/repositories";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** Admin surface for repositories: create them and configure each one's LLM. */
export function AdminRepositoriesPage(): ReactNode {
  const [repos, setRepos] = useState<AdminRepository[] | null>(null);
  const [reposError, setReposError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

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

  const onCreate = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (trimmed === "") return;
    setCreating(true);
    setCreateError(null);
    try {
      const repo = await createRepository({
        name: trimmed,
        description: description.trim() === "" ? null : description.trim(),
      });
      setRepos((prev) => [...(prev ?? []), repo]);
      setName("");
      setDescription("");
    } catch (err) {
      setCreateError(errorMessage(err, "Could not create the repository."));
    } finally {
      setCreating(false);
    }
  };

  /** Replace a repo in the list (after rename / config change). */
  const upsert = (updated: AdminRepository) =>
    setRepos((prev) => prev?.map((r) => (r.id === updated.id ? updated : r)) ?? prev);
  const remove = (id: string) => setRepos((prev) => prev?.filter((r) => r.id !== id) ?? prev);

  if (reposError !== null) {
    return <p className="error">{reposError}</p>;
  }
  if (repos === null) {
    return <p>Loading repositories…</p>;
  }

  return (
    <section className="admin-repos">
      <h1>Repositories</h1>

      <form className="repo-create" onSubmit={onCreate}>
        <h2>New repository</h2>
        <label htmlFor="repo-name">Repository name</label>
        <input id="repo-name" value={name} onChange={(e) => setName(e.target.value)} required />
        <label htmlFor="repo-description">Description</label>
        <input
          id="repo-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <button type="submit" disabled={creating || name.trim() === ""}>
          Create repository
        </button>
        {createError !== null && <p className="error">{createError}</p>}
      </form>

      {repos.length === 0 ? (
        <p>No repositories yet. Create one above to get started.</p>
      ) : (
        <ul className="repo-list">
          {repos.map((repo) => (
            <RepoItem
              key={repo.id}
              repo={repo}
              onChanged={upsert}
              onDeleted={() => remove(repo.id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/** One repository row: configure its LLM, rename it, or delete it. */
function RepoItem({
  repo,
  onChanged,
  onDeleted,
}: {
  repo: AdminRepository;
  onChanged: (updated: AdminRepository) => void;
  onDeleted: () => void;
}): ReactNode {
  const [configuring, setConfiguring] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(repo.name);
  const [description, setDescription] = useState(repo.description ?? "");
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onRename = async (e: FormEvent) => {
    e.preventDefault();
    if (name.trim() === "") return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateRepository(repo.id, {
        name: name.trim(),
        description: description.trim() === "" ? null : description.trim(),
      });
      onChanged(updated);
      setRenaming(false);
    } catch (err) {
      setError(errorMessage(err, "Could not update the repository."));
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (confirmName !== repo.name) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteRepository(repo.id, confirmName);
      onDeleted();
    } catch (err) {
      setError(errorMessage(err, "Could not delete the repository."));
      setDeleting(false);
    }
  };

  return (
    <li className="repo-item">
      <div className="repo-head">
        <span className="repo-name">{repo.name}</span>
        {repo.description !== null && <span className="repo-description">{repo.description}</span>}
        <span className={repo.configured ? "badge configured" : "badge unconfigured"}>
          {repo.configured ? "Configured" : "Not configured"}
        </span>
        <button type="button" onClick={() => setConfiguring((v) => !v)}>
          Configure
        </button>
        <button type="button" onClick={() => setRenaming((v) => !v)}>
          Rename
        </button>
        {!confirmingDelete ? (
          <button type="button" onClick={() => setConfirmingDelete(true)}>
            Delete
          </button>
        ) : (
          <span className="confirm-delete">
            <label htmlFor={`repo-confirm-${repo.id}`}>Confirm name</label>
            <input
              id={`repo-confirm-${repo.id}`}
              value={confirmName}
              onChange={(e) => setConfirmName(e.target.value)}
            />
            <button
              type="button"
              onClick={onDelete}
              disabled={deleting || confirmName !== repo.name}
            >
              Confirm delete
            </button>
          </span>
        )}
      </div>

      {renaming && (
        <form className="repo-rename" onSubmit={onRename}>
          <label htmlFor={`repo-name-${repo.id}`}>Name</label>
          <input
            id={`repo-name-${repo.id}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <label htmlFor={`repo-desc-${repo.id}`}>Description</label>
          <input
            id={`repo-desc-${repo.id}`}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <button type="submit" disabled={saving || name.trim() === ""}>
            Save
          </button>
        </form>
      )}

      {error !== null && <p className="error">{error}</p>}

      {configuring && (
        <RepoConfigPanel
          repository={repo}
          onConfigured={() => onChanged({ ...repo, configured: true })}
        />
      )}
    </li>
  );
}

/** Load and edit one repository's LLM configuration. */
function RepoConfigPanel({
  repository,
  onConfigured,
}: {
  repository: AdminRepository;
  onConfigured: () => void;
}): ReactNode {
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [provider, setProvider] = useState<LLMProvider>(LLM_PROVIDERS[0].value);
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLlmConfig(repository.id)
      .then((cfg) => {
        if (cancelled) return;
        setConfig(cfg);
        if (cfg.provider !== null) setProvider(cfg.provider);
        if (cfg.model !== null) setModel(cfg.model);
      })
      .catch(
        (err: unknown) =>
          !cancelled && setLoadError(errorMessage(err, "Failed to load configuration.")),
      );
    return () => {
      cancelled = true;
    };
  }, [repository.id]);

  const onLoadModels = async () => {
    setLoadingModels(true);
    setModelsError(null);
    try {
      const result = await listModels(repository.id, {
        provider,
        // Send the just-entered key if present; otherwise the backend uses the stored one.
        api_key: apiKey === "" ? undefined : apiKey,
      });
      setModels(result.models);
      if (result.models.length === 0) {
        setModelsError("The provider returned no models.");
      }
    } catch (err) {
      setModelsError(errorMessage(err, "Could not load models."));
    } finally {
      setLoadingModels(false);
    }
  };

  const onSave = async (e: FormEvent) => {
    e.preventDefault();
    if (model.trim() === "" || apiKey === "") return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const updated = await setLlmConfig(repository.id, {
        provider,
        model: model.trim(),
        api_key: apiKey,
      });
      setConfig(updated);
      setApiKey("");
      setSaved(true);
      onConfigured();
    } catch (err) {
      setSaveError(errorMessage(err, "Could not save the configuration."));
    } finally {
      setSaving(false);
    }
  };

  if (loadError !== null) {
    return <p className="error">{loadError}</p>;
  }
  if (config === null) {
    return <p>Loading configuration…</p>;
  }

  const keyId = `key-${repository.id}`;
  const providerId = `provider-${repository.id}`;
  const modelId = `model-${repository.id}`;
  const modelListId = `model-options-${repository.id}`;

  return (
    <form className="repo-config" onSubmit={onSave}>
      {config.api_key_masked !== null && (
        <p className="current-key">Current key: {config.api_key_masked}</p>
      )}
      <label htmlFor={providerId}>Provider</label>
      <select
        id={providerId}
        value={provider}
        onChange={(e) => {
          setProvider(e.target.value as LLMProvider);
          // The old list belongs to the previous provider — clear it.
          setModels([]);
          setModelsError(null);
        }}
      >
        {LLM_PROVIDERS.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>

      <label htmlFor={modelId}>Model</label>
      <input
        id={modelId}
        list={modelListId}
        value={model}
        onChange={(e) => setModel(e.target.value)}
        required
      />
      <datalist id={modelListId}>
        {models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <button type="button" onClick={onLoadModels} disabled={loadingModels}>
        {loadingModels ? "Loading models…" : "Load models"}
      </button>
      {modelsError !== null && <p className="error">{modelsError}</p>}

      <label htmlFor={keyId}>API key</label>
      <input
        id={keyId}
        type="password"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder={config.configured ? "Enter a new key to replace the current one" : ""}
        required
      />

      <button type="submit" disabled={saving || model.trim() === "" || apiKey === ""}>
        Save configuration
      </button>
      {saved && <p className="success">Configuration saved.</p>}
      {saveError !== null && <p className="error">{saveError}</p>}
    </form>
  );
}
