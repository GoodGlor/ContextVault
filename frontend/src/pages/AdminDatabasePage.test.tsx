import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AdminDatabasePage } from "./AdminDatabasePage";
import type { AdminRepository } from "../api/repositories";
import type { DatabaseConnection, ExposedTable } from "../api/database";

function json(body: unknown, status = 200): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const REPOS: AdminRepository[] = [
  { id: "r-1", name: "Handbook", description: null, configured: true },
];

function connection(overrides: Partial<DatabaseConnection> = {}): DatabaseConnection {
  return {
    id: "conn-1",
    db_type: "postgres",
    host: "db.internal",
    port: 5432,
    database: "reporting",
    username: "readonly",
    exposed_schema: [],
    ...overrides,
  };
}

const SCHEMA: ExposedTable[] = [
  {
    table: "orders",
    description: "",
    columns: [
      { name: "id", description: "" },
      { name: "total", description: "" },
    ],
  },
  {
    table: "customers",
    description: "",
    columns: [{ name: "email", description: "" }],
  },
];

describe("AdminDatabasePage", () => {
  const fetchMock = vi.fn();
  beforeEach(() => vi.stubGlobal("fetch", fetchMock));
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  /** `conn` is `null` for "no connection yet" (GET → 404). */
  function mock(opts: {
    repos?: AdminRepository[];
    conn?: DatabaseConnection | null;
    putResult?: DatabaseConnection;
    introspectSchema?: ExposedTable[];
    patchResult?: DatabaseConnection;
  }) {
    const repos = opts.repos ?? REPOS;
    const conn = opts.conn === undefined ? null : opts.conn;
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url.endsWith("/admin/repositories")) return Promise.resolve(json(repos));
      if (/\/repositories\/[^/]+\/database\/introspect$/.test(url) && method === "POST") {
        return Promise.resolve(json({ schema: opts.introspectSchema ?? [] }));
      }
      if (/\/repositories\/[^/]+\/database\/schema$/.test(url) && method === "PATCH") {
        return Promise.resolve(json(opts.patchResult ?? connection()));
      }
      if (/\/repositories\/[^/]+\/database$/.test(url)) {
        if (method === "GET") {
          return conn === null
            ? Promise.resolve(json({ detail: "Database connection not found" }, 404))
            : Promise.resolve(json(conn));
        }
        if (method === "PUT") {
          return Promise.resolve(json(opts.putResult ?? connection()));
        }
        if (method === "DELETE") {
          return Promise.resolve(json(null, 204));
        }
      }
      throw new Error(`unexpected fetch ${method} ${url}`);
    });
  }

  it("renders the connection setup form when no connection exists", async () => {
    mock({ conn: null });
    render(<AdminDatabasePage />);
    expect(await screen.findByLabelText("Host")).toBeInTheDocument();
    expect(screen.getByLabelText("Port")).toBeInTheDocument();
    expect(screen.getByLabelText("Database")).toBeInTheDocument();
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("submits the setup form and calls putDatabase with typed values", async () => {
    mock({ conn: null, putResult: connection() });
    render(<AdminDatabasePage />);
    await screen.findByLabelText("Host");

    await userEvent.selectOptions(screen.getByLabelText("Database type"), "postgres");
    await userEvent.type(screen.getByLabelText("Host"), "db.internal");
    const portInput = screen.getByLabelText("Port");
    await userEvent.clear(portInput);
    await userEvent.type(portInput, "5432");
    await userEvent.type(screen.getByLabelText("Database"), "reporting");
    await userEvent.type(screen.getByLabelText("Username"), "readonly");
    await userEvent.type(screen.getByLabelText("Password"), "s3cret");
    await userEvent.click(screen.getByRole("button", { name: "Connect" }));

    const put = fetchMock.mock.calls.find(
      (c) => (c[1]?.method ?? "GET") === "PUT" && String(c[0]).endsWith("/database"),
    );
    expect(String(put?.[0])).toContain("/repositories/r-1/database");
    const body = JSON.parse(String(put?.[1]?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      db_type: "postgres",
      host: "db.internal",
      port: 5432,
      database: "reporting",
      username: "readonly",
      password: "s3cret",
    });
    expect(typeof body.port).toBe("number");

    // After a successful connect, the connected summary shows.
    expect(await screen.findByRole("button", { name: "Introspect" })).toBeInTheDocument();
  });

  it("shows a masked connected summary — never the password", async () => {
    mock({ conn: connection() });
    render(<AdminDatabasePage />);
    expect(await screen.findByText(/db\.internal/)).toBeInTheDocument();
    expect(screen.getByText(/reporting/)).toBeInTheDocument();
    expect(screen.getByText(/readonly/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Introspect" })).toBeInTheDocument();
    expect(screen.queryByText(/s3cret/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("renders tables with description inputs and checkboxes after introspecting", async () => {
    mock({ conn: connection(), introspectSchema: SCHEMA });
    render(<AdminDatabasePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Introspect" }));

    expect(await screen.findByText("orders")).toBeInTheDocument();
    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.getByLabelText("Include table orders")).toBeInTheDocument();
    expect(screen.getByLabelText("Include column id")).toBeInTheDocument();
    expect(screen.getByLabelText("Include column total")).toBeInTheDocument();
    expect(screen.getByLabelText("Include column email")).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText("Table description")).toHaveLength(2);
    expect(screen.getAllByPlaceholderText("Column description")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Save allow-list" })).toBeInTheDocument();
  });

  it("saves the allow-list with only checked tables/columns", async () => {
    mock({ conn: connection(), introspectSchema: SCHEMA });
    render(<AdminDatabasePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Introspect" }));
    await screen.findByText("orders");

    // Include "orders" and only its "total" column with a description; leave
    // "customers" unchecked entirely.
    await userEvent.click(screen.getByLabelText("Include table orders"));
    await userEvent.click(screen.getByLabelText("Include column total"));
    const ordersRow = screen.getByText("orders").closest("li") as HTMLElement;
    await userEvent.type(
      within(ordersRow).getByPlaceholderText("Table description"),
      "Customer orders",
    );
    const totalColumn = screen.getByLabelText("Include column total").closest("li") as HTMLElement;
    await userEvent.type(
      within(totalColumn).getByPlaceholderText("Column description"),
      "Order total",
    );

    await userEvent.click(screen.getByRole("button", { name: "Save allow-list" }));

    const patch = fetchMock.mock.calls.find((c) => (c[1]?.method ?? "GET") === "PATCH");
    const body = JSON.parse(String(patch?.[1]?.body)) as { exposed_schema: ExposedTable[] };
    expect(body.exposed_schema).toEqual([
      {
        table: "orders",
        description: "Customer orders",
        columns: [{ name: "total", description: "Order total" }],
      },
    ]);
  });

  it("deletes the connection after confirming", async () => {
    mock({ conn: connection() });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<AdminDatabasePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete connection" }));

    expect(window.confirm).toHaveBeenCalled();
    const del = fetchMock.mock.calls.find((c) => (c[1]?.method ?? "GET") === "DELETE");
    expect(String(del?.[0])).toContain("/repositories/r-1/database");
    // Back to the setup form once deleted.
    expect(await screen.findByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("does not delete when the confirmation is declined", async () => {
    mock({ conn: connection() });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AdminDatabasePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Delete connection" }));

    const del = fetchMock.mock.calls.find((c) => (c[1]?.method ?? "GET") === "DELETE");
    expect(del).toBeUndefined();
  });
});
