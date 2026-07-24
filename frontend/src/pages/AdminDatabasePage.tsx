import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import { listAllRepositories, type AdminRepository } from "../api/repositories";
import {
  deleteDatabase,
  getDatabase,
  introspect,
  patchSchema,
  putDatabase,
  type DatabaseConnection,
  type DatabaseType,
  type ExposedTable,
} from "../api/database";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

/** One editable column row in the allow-list editor: whether it's included, and
 *  the description the report LLM will see for it. */
interface EditableColumn {
  name: string;
  description: string;
  checked: boolean;
}

/** One editable table row, holding its own included columns. */
interface EditableTable {
  table: string;
  description: string;
  checked: boolean;
  columns: EditableColumn[];
}

/** Seed the editor from a fresh introspect result, carrying over any
 *  descriptions/inclusion already saved in the current allow-list so
 *  re-introspecting doesn't discard prior curation. */
function toEditable(introspected: ExposedTable[], saved: ExposedTable[]): EditableTable[] {
  return introspected.map((table) => {
    const savedTable = saved.find((s) => s.table === table.table);
    return {
      table: table.table,
      description: savedTable?.description ?? table.description,
      checked: savedTable !== undefined,
      columns: table.columns.map((col) => {
        const savedCol = savedTable?.columns.find((c) => c.name === col.name);
        return {
          name: col.name,
          description: savedCol?.description ?? col.description,
          checked: savedCol !== undefined,
        };
      }),
    };
  });
}

/** Only the checked tables/columns — this is what gets saved as the allow-list. */
function toExposedSchema(tables: EditableTable[]): ExposedTable[] {
  return tables
    .filter((t) => t.checked)
    .map((t) => ({
      table: t.table,
      description: t.description,
      columns: t.columns
        .filter((c) => c.checked)
        .map((c) => ({ name: c.name, description: c.description })),
    }));
}

/** Admin surface for a repository's reporting-database connection: connect,
 *  introspect its live schema, curate an allow-list, or disconnect. */
export function AdminDatabasePage(): ReactNode {
  const { t } = useTranslation();
  const [repos, setRepos] = useState<AdminRepository[] | null>(null);
  const [reposError, setReposError] = useState<string | null>(null);
  const [selected, setSelected] = useState("");

  // `null` = no connection yet (show the setup form); `undefined` = still loading.
  const [connection, setConnection] = useState<DatabaseConnection | null | undefined>(undefined);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  // Setup-form fields.
  const [dbType, setDbType] = useState<DatabaseType>("postgres");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [database, setDatabase] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  // Allow-list editor (populated once "Introspect" has been run).
  const [schema, setSchema] = useState<EditableTable[] | null>(null);
  const [introspecting, setIntrospecting] = useState(false);
  const [introspectError, setIntrospectError] = useState<string | null>(null);
  const [savingSchema, setSavingSchema] = useState(false);
  const [schemaSaved, setSchemaSaved] = useState(false);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  const [deleteError, setDeleteError] = useState<string | null>(null);

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
          !cancelled && setReposError(errorMessage(err, t("adminDatabase.errorLoadRepos"))),
      );
    return () => {
      cancelled = true;
    };
  }, [t]);

  // (Re)load the selected repository's connection.
  useEffect(() => {
    if (selected === "") return;
    let cancelled = false;
    setConnection(undefined);
    setConnectionError(null);
    setSchema(null);
    setSchemaSaved(false);
    getDatabase(selected)
      .then((conn) => !cancelled && setConnection(conn))
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setConnection(null);
        } else {
          setConnectionError(errorMessage(err, t("adminDatabase.errorLoadConnection")));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selected, t]);

  const onConnect = async (e: FormEvent) => {
    e.preventDefault();
    if (selected === "" || host.trim() === "" || port.trim() === "") return;
    setConnecting(true);
    setConnectError(null);
    try {
      const conn = await putDatabase(selected, {
        db_type: dbType,
        host: host.trim(),
        port: Number(port),
        database: database.trim(),
        username: username.trim(),
        password,
      });
      setConnection(conn);
      setPassword("");
    } catch (err) {
      setConnectError(errorMessage(err, t("adminDatabase.errorConnect")));
    } finally {
      setConnecting(false);
    }
  };

  const onIntrospect = async () => {
    if (selected === "" || connection == null) return;
    setIntrospecting(true);
    setIntrospectError(null);
    setSchemaSaved(false);
    try {
      const result = await introspect(selected);
      setSchema(toEditable(result.schema, connection.exposed_schema));
    } catch (err) {
      setIntrospectError(errorMessage(err, t("adminDatabase.errorIntrospect")));
    } finally {
      setIntrospecting(false);
    }
  };

  const onSaveSchema = async () => {
    if (selected === "" || schema === null) return;
    setSavingSchema(true);
    setSchemaError(null);
    setSchemaSaved(false);
    try {
      const updated = await patchSchema(selected, toExposedSchema(schema));
      setConnection(updated);
      setSchemaSaved(true);
    } catch (err) {
      setSchemaError(errorMessage(err, t("adminDatabase.errorSaveSchema")));
    } finally {
      setSavingSchema(false);
    }
  };

  const onDelete = async () => {
    if (selected === "") return;
    if (!window.confirm(t("adminDatabase.confirmDelete"))) return;
    setDeleteError(null);
    try {
      await deleteDatabase(selected);
      setConnection(null);
      setSchema(null);
    } catch (err) {
      setDeleteError(errorMessage(err, t("adminDatabase.errorDelete")));
    }
  };

  const setTableChecked = (index: number, checked: boolean) => {
    setSchema((prev) =>
      prev === null ? prev : prev.map((t, i) => (i === index ? { ...t, checked } : t)),
    );
  };
  const setTableDescription = (index: number, description: string) => {
    setSchema((prev) =>
      prev === null ? prev : prev.map((t, i) => (i === index ? { ...t, description } : t)),
    );
  };
  const setColumnChecked = (tableIndex: number, columnIndex: number, checked: boolean) => {
    setSchema((prev) =>
      prev === null
        ? prev
        : prev.map((t, i) =>
            i === tableIndex
              ? {
                  ...t,
                  columns: t.columns.map((c, j) => (j === columnIndex ? { ...c, checked } : c)),
                }
              : t,
          ),
    );
  };
  const setColumnDescription = (tableIndex: number, columnIndex: number, description: string) => {
    setSchema((prev) =>
      prev === null
        ? prev
        : prev.map((t, i) =>
            i === tableIndex
              ? {
                  ...t,
                  columns: t.columns.map((c, j) => (j === columnIndex ? { ...c, description } : c)),
                }
              : t,
          ),
    );
  };

  if (reposError !== null) {
    return <p className="error">{reposError}</p>;
  }
  if (repos === null) {
    return <p>{t("adminDatabase.loadingRepos")}</p>;
  }
  if (repos.length === 0) {
    return <p>{t("adminDatabase.noRepos")}</p>;
  }

  return (
    <section className="admin-database">
      <h1>{t("adminDatabase.title")}</h1>

      <label htmlFor="database-repo">{t("adminDatabase.repositoryLabel")}</label>
      <select id="database-repo" value={selected} onChange={(e) => setSelected(e.target.value)}>
        {repos.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>

      {connectionError !== null && <p className="error">{connectionError}</p>}

      {connection === undefined ? (
        <p>{t("adminDatabase.loadingConnection")}</p>
      ) : connection === null ? (
        <form className="database-setup" onSubmit={onConnect}>
          <p>{t("adminDatabase.setupIntro")}</p>
          <label htmlFor="db-type">{t("adminDatabase.dbTypeLabel")}</label>
          <select
            id="db-type"
            value={dbType}
            onChange={(e) => setDbType(e.target.value as DatabaseType)}
          >
            <option value="postgres">{t("adminDatabase.dbTypePostgres")}</option>
            <option value="mysql">{t("adminDatabase.dbTypeMysql")}</option>
          </select>

          <label htmlFor="db-host">{t("adminDatabase.hostLabel")}</label>
          <input id="db-host" value={host} onChange={(e) => setHost(e.target.value)} required />

          <label htmlFor="db-port">{t("adminDatabase.portLabel")}</label>
          <input
            id="db-port"
            type="number"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            required
          />

          <label htmlFor="db-database">{t("adminDatabase.databaseLabel")}</label>
          <input
            id="db-database"
            value={database}
            onChange={(e) => setDatabase(e.target.value)}
            required
          />

          <label htmlFor="db-username">{t("adminDatabase.usernameLabel")}</label>
          <input
            id="db-username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />

          <label htmlFor="db-password">{t("adminDatabase.passwordLabel")}</label>
          <input
            id="db-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          <button type="submit" disabled={connecting || host.trim() === "" || port.trim() === ""}>
            {connecting ? t("adminDatabase.connecting") : t("adminDatabase.connectButton")}
          </button>
          {connectError !== null && <p className="error">{connectError}</p>}
        </form>
      ) : (
        <div className="database-connected">
          <h2>{t("adminDatabase.connectedTitle")}</h2>
          <ul className="database-summary">
            <li>{t("adminDatabase.summaryHost", { host: connection.host })}</li>
            <li>{t("adminDatabase.summaryDatabase", { database: connection.database })}</li>
            <li>{t("adminDatabase.summaryUsername", { username: connection.username })}</li>
          </ul>

          <button type="button" onClick={onIntrospect} disabled={introspecting}>
            {introspecting ? t("adminDatabase.introspecting") : t("adminDatabase.introspectButton")}
          </button>
          {introspectError !== null && <p className="error">{introspectError}</p>}

          {schema !== null && (
            <div className="database-schema-editor">
              <h3>{t("adminDatabase.schemaTitle")}</h3>
              {schema.length === 0 ? (
                <p>{t("adminDatabase.noTablesFound")}</p>
              ) : (
                <ul className="schema-table-list">
                  {schema.map((table, tableIndex) => (
                    <li key={table.table} className="schema-table">
                      <label>
                        <input
                          type="checkbox"
                          aria-label={t("adminDatabase.includeTableLabel", { table: table.table })}
                          checked={table.checked}
                          onChange={(e) => setTableChecked(tableIndex, e.target.checked)}
                        />
                        {table.table}
                      </label>
                      <input
                        placeholder={t("adminDatabase.tableDescriptionPlaceholder")}
                        value={table.description}
                        onChange={(e) => setTableDescription(tableIndex, e.target.value)}
                      />
                      <ul className="schema-column-list">
                        {table.columns.map((col, columnIndex) => (
                          <li key={col.name} className="schema-column">
                            <label>
                              <input
                                type="checkbox"
                                aria-label={t("adminDatabase.includeColumnLabel", {
                                  name: col.name,
                                })}
                                checked={col.checked}
                                onChange={(e) =>
                                  setColumnChecked(tableIndex, columnIndex, e.target.checked)
                                }
                              />
                              {col.name}
                            </label>
                            <input
                              placeholder={t("adminDatabase.columnDescriptionPlaceholder")}
                              value={col.description}
                              onChange={(e) =>
                                setColumnDescription(tableIndex, columnIndex, e.target.value)
                              }
                            />
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              )}
              <button type="button" onClick={onSaveSchema} disabled={savingSchema}>
                {savingSchema
                  ? t("adminDatabase.savingSchema")
                  : t("adminDatabase.saveSchemaButton")}
              </button>
              {schemaSaved && <p className="success">{t("adminDatabase.schemaSaved")}</p>}
              {schemaError !== null && <p className="error">{schemaError}</p>}
            </div>
          )}

          <button type="button" onClick={onDelete}>
            {t("adminDatabase.deleteButton")}
          </button>
          {deleteError !== null && <p className="error">{deleteError}</p>}
        </div>
      )}
    </section>
  );
}
