import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
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
          !cancelled && setReposError(errorMessage(err, t("repositories.failedToLoad"))),
      );
    return () => {
      cancelled = true;
    };
  }, [t]);

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
      setCreateError(errorMessage(err, t("repositories.couldNotCreate")));
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
    return <p>{t("repositories.loadingRepositories")}</p>;
  }

  return (
    <section className="admin-repos">
      <h1>{t("repositories.title")}</h1>

      <form className="repo-create" onSubmit={onCreate}>
        <h2>{t("repositories.newRepository")}</h2>
        <label htmlFor="repo-name">{t("repositories.repositoryName")}</label>
        <input id="repo-name" value={name} onChange={(e) => setName(e.target.value)} required />
        <label htmlFor="repo-description">{t("repositories.description")}</label>
        <input
          id="repo-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <button type="submit" disabled={creating || name.trim() === ""}>
          {t("repositories.createRepository")}
        </button>
        {createError !== null && <p className="error">{createError}</p>}
      </form>

      {repos.length === 0 ? (
        <p>{t("repositories.emptyState")}</p>
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
  const { t } = useTranslation();
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
      setError(errorMessage(err, t("repositories.couldNotUpdate")));
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
      setError(errorMessage(err, t("repositories.couldNotDelete")));
      setDeleting(false);
    }
  };

  return (
    <li className="repo-item">
      <div className="repo-head">
        <span className="repo-name">{repo.name}</span>
        {repo.description !== null && <span className="repo-description">{repo.description}</span>}
        <span className={repo.configured ? "badge configured" : "badge unconfigured"}>
          {repo.configured ? t("repositories.configured") : t("repositories.notConfigured")}
        </span>
        <button type="button" onClick={() => setConfiguring((v) => !v)}>
          {t("repositories.configure")}
        </button>
        <button type="button" onClick={() => setRenaming((v) => !v)}>
          {t("repositories.rename")}
        </button>
        {!confirmingDelete ? (
          <button type="button" onClick={() => setConfirmingDelete(true)}>
            {t("repositories.delete")}
          </button>
        ) : (
          <span className="confirm-delete">
            <label htmlFor={`repo-confirm-${repo.id}`}>{t("repositories.confirmName")}</label>
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
              {t("repositories.confirmDelete")}
            </button>
          </span>
        )}
      </div>

      {renaming && (
        <form className="repo-rename" onSubmit={onRename}>
          <label htmlFor={`repo-name-${repo.id}`}>{t("repositories.name")}</label>
          <input
            id={`repo-name-${repo.id}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <label htmlFor={`repo-desc-${repo.id}`}>{t("repositories.description")}</label>
          <input
            id={`repo-desc-${repo.id}`}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <button type="submit" disabled={saving || name.trim() === ""}>
            {t("repositories.save")}
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
  const { t } = useTranslation();
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [provider, setProvider] = useState<LLMProvider>(LLM_PROVIDERS[0].value);
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [replacingKey, setReplacingKey] = useState(false);
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
          !cancelled && setLoadError(errorMessage(err, t("repositories.failedToLoadConfig"))),
      );
    return () => {
      cancelled = true;
    };
  }, [repository.id, t]);

  // When the selected provider already has a relevant stored key, fetch its models
  // automatically so the dropdown is populated with the current model preselected —
  // no need to re-enter the key just to change the model. Re-runs if the provider is
  // switched back to the configured one.
  useEffect(() => {
    if (config === null || !config.configured || provider !== config.provider) return;
    let cancelled = false;
    setLoadingModels(true);
    setModelsError(null);
    listModels(repository.id, { provider })
      .then((res) => {
        if (cancelled) return;
        setModels(res.models);
        if (res.models.length === 0) setModelsError(t("repositories.noModels"));
      })
      .catch(
        (err: unknown) =>
          !cancelled && setModelsError(errorMessage(err, t("repositories.couldNotLoadModels"))),
      )
      .finally(() => !cancelled && setLoadingModels(false));
    return () => {
      cancelled = true;
    };
  }, [config, provider, repository.id, t]);

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
        setModelsError(t("repositories.noModels"));
      }
    } catch (err) {
      setModelsError(errorMessage(err, t("repositories.couldNotLoadModels")));
    } finally {
      setLoadingModels(false);
    }
  };

  if (loadError !== null) {
    return <p className="error">{loadError}</p>;
  }
  if (config === null) {
    return <p>{t("repositories.loadingConfiguration")}</p>;
  }

  // The stored key belongs to the configured provider. It is "relevant" only while
  // that same provider is selected — switch providers and a new key is required.
  const keyIsRelevant = config.configured && provider === config.provider;
  const keyRequired = !keyIsRelevant;
  // Show the key input when a key is required (no relevant stored one), or when the
  // admin explicitly chooses to replace the existing one.
  const showKeyInput = keyRequired || replacingKey;
  // We can fetch models with the stored key (relevant) or a just-entered one.
  const canLoad = keyIsRelevant || apiKey.trim() !== "";
  // The single model field's options: the fetched models, plus the current model so
  // it always shows even before the list loads (or if it's since been retired).
  const modelOptions = model && !models.includes(model) ? [model, ...models] : models;

  const onSave = async (e: FormEvent) => {
    e.preventDefault();
    if (model.trim() === "" || (keyRequired && apiKey.trim() === "")) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const updated = await setLlmConfig(repository.id, {
        provider,
        model: model.trim(),
        // Only send a key when one was entered; otherwise the stored key is kept.
        ...(apiKey.trim() !== "" ? { api_key: apiKey } : {}),
      });
      setConfig(updated);
      setApiKey("");
      setReplacingKey(false);
      setSaved(true);
      onConfigured();
    } catch (err) {
      setSaveError(errorMessage(err, t("repositories.couldNotSave")));
    } finally {
      setSaving(false);
    }
  };

  const keyId = `key-${repository.id}`;
  const providerId = `provider-${repository.id}`;
  const modelSelectId = `model-${repository.id}`;

  return (
    <form className="repo-config" onSubmit={onSave}>
      {/* The stored key only applies to its own provider; don't advertise it once a
          different provider is selected (a new key is needed then). */}
      {keyIsRelevant && config.api_key_masked !== null && (
        <p className="current-key">
          {t("repositories.currentKey", { value: config.api_key_masked })}
        </p>
      )}
      <label htmlFor={providerId}>{t("repositories.provider")}</label>
      <select
        id={providerId}
        value={provider}
        onChange={(e) => {
          const next = e.target.value as LLMProvider;
          setProvider(next);
          // The old list belongs to the previous provider — clear it. Keep the model
          // only when returning to the configured provider; otherwise a new one is picked.
          setModels([]);
          setModelsError(null);
          setReplacingKey(false);
          setApiKey("");
          setModel(config.provider === next ? (config.model ?? "") : "");
        }}
      >
        {LLM_PROVIDERS.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>

      {/* The model is a single field: a dropdown that shows the current model and
          the loaded alternatives. It appears once there's at least one option. */}
      {modelOptions.length > 0 && (
        <>
          <label htmlFor={modelSelectId}>{t("repositories.model")}</label>
          <select
            id={modelSelectId}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            required
          >
            {!modelOptions.includes(model) && (
              <option value="">{t("repositories.chooseModelPlaceholder")}</option>
            )}
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </>
      )}
      <button type="button" onClick={onLoadModels} disabled={loadingModels || !canLoad}>
        {loadingModels ? t("repositories.loadingModels") : t("repositories.loadModels")}
      </button>
      {modelsError !== null && <p className="error">{modelsError}</p>}

      {/* The key is only asked for when there's no relevant stored one. An already-keyed
          provider offers an explicit "Replace key" instead of forcing re-entry. */}
      {showKeyInput ? (
        <>
          <label htmlFor={keyId}>{t("repositories.apiKey")}</label>
          <input
            id={keyId}
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={keyIsRelevant ? t("repositories.keyPlaceholder") : ""}
            required={keyRequired}
          />
          {replacingKey && (
            <button
              type="button"
              onClick={() => {
                setReplacingKey(false);
                setApiKey("");
              }}
            >
              {t("repositories.cancelReplaceKey")}
            </button>
          )}
        </>
      ) : (
        <button type="button" onClick={() => setReplacingKey(true)}>
          {t("repositories.replaceKey")}
        </button>
      )}

      <button
        type="submit"
        disabled={saving || model.trim() === "" || (keyRequired && apiKey.trim() === "")}
      >
        {t("repositories.saveConfiguration")}
      </button>
      {saved && <p className="success">{t("repositories.configurationSaved")}</p>}
      {saveError !== null && <p className="error">{saveError}</p>}
    </form>
  );
}
