import { api } from "./client";

// Mirrors the database-connection schemas in src/contextvault/api/database.py.
// One connection per repository; the password is write-only — no route ever
// returns it, so `DatabaseConnection` deliberately has no password field.

export type DatabaseType = "postgres" | "mysql";

export interface ExposedColumn {
  name: string;
  description: string;
}

export interface ExposedTable {
  table: string;
  description: string;
  columns: ExposedColumn[];
}

export interface DatabaseConnection {
  id: string;
  db_type: DatabaseType;
  host: string;
  port: number;
  database: string;
  username: string;
  exposed_schema: ExposedTable[];
}

/** Payload for `putDatabase`. `password` omitted/empty keeps the currently stored
 *  one (required only when creating a connection for the first time);
 *  `exposed_schema` omitted keeps the currently stored allow-list. */
export interface DatabaseConnectionPayload {
  db_type: DatabaseType;
  host: string;
  port: number;
  database: string;
  username: string;
  password?: string;
  exposed_schema?: ExposedTable[] | null;
}

/** Read a repository's stored connection — never the password. 404 if none exists. */
export function getDatabase(repositoryId: string): Promise<DatabaseConnection> {
  return api.get<DatabaseConnection>(`/repositories/${repositoryId}/database`);
}

/** Live-test then store a repository's reporting-database connection (upsert). */
export function putDatabase(
  repositoryId: string,
  payload: DatabaseConnectionPayload,
): Promise<DatabaseConnection> {
  return api.put<DatabaseConnection>(`/repositories/${repositoryId}/database`, payload);
}

/** Save the admin's edited exposed-schema allow-list without re-testing the connection. */
export function patchSchema(
  repositoryId: string,
  exposedSchema: ExposedTable[],
): Promise<DatabaseConnection> {
  return api.patch<DatabaseConnection>(`/repositories/${repositoryId}/database/schema`, {
    exposed_schema: exposedSchema,
  });
}

/** Remove a repository's connection; its generated reports/schedules cascade with it. */
export function deleteDatabase(repositoryId: string): Promise<void> {
  return api.del<void>(`/repositories/${repositoryId}/database`);
}

export interface IntrospectResult {
  schema: ExposedTable[];
}

/** Live-read tables/columns from the stored connection, for the admin to annotate
 *  and save as the exposed-schema allow-list (`patchSchema`). */
export function introspect(repositoryId: string): Promise<IntrospectResult> {
  return api.post<IntrospectResult>(`/repositories/${repositoryId}/database/introspect`);
}
