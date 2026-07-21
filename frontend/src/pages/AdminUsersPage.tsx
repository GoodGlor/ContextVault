import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { ApiError } from "../api/client";
import { createInvitation, type Invitation, type Role } from "../api/invitations";
import { deleteUser, listUsers, resetUserPassword, type AdminUser } from "../api/users";
import { grantAccess, listGrants, revokeAccess, type Grant } from "../api/grants";
import { listAllRepositories, type AdminRepository } from "../api/repositories";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.detail : fallback;
}

function roleLabel(role: Role): string {
  return role === "admin" ? "Admin" : "User";
}

/** Admin surface for accounts and repository access (card #39). */
export function AdminUsersPage(): ReactNode {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [repos, setRepos] = useState<AdminRepository[] | null>(null);
  const [reposError, setReposError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listUsers()
      .then((u) => !cancelled && setUsers(u))
      .catch(
        (err: unknown) => !cancelled && setUsersError(errorMessage(err, "Failed to load users.")),
      );
    listAllRepositories()
      .then((r) => !cancelled && setRepos(r))
      .catch(
        (err: unknown) =>
          !cancelled && setReposError(errorMessage(err, "Failed to load repositories.")),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  if (usersError !== null) return <p className="error">{usersError}</p>;
  if (reposError !== null) return <p className="error">{reposError}</p>;
  if (users === null || repos === null) return <p>Loading…</p>;

  return (
    <div className="admin-users">
      <h1>Users &amp; access</h1>
      <InviteForm />
      <AccountsList
        users={users}
        onDeleted={(id) => setUsers((prev) => prev?.filter((u) => u.id !== id) ?? prev)}
      />
      <GrantsPanel users={users} repos={repos} />
    </div>
  );
}

/** Issue an onboarding invite and reveal its one-time token. */
function InviteForm(): ReactNode {
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Role>("user");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState<Invitation | null>(null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = username.trim();
    if (trimmed === "") return;
    setBusy(true);
    setError(null);
    try {
      const inv = await createInvitation({ username: trimmed, role });
      setInvite(inv);
      setUsername("");
    } catch (err) {
      setError(errorMessage(err, "Could not create the invite."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="Invite a user">
      <h2>Invite a user</h2>
      <form onSubmit={onSubmit}>
        <label htmlFor="invite-username">Username</label>
        <input
          id="invite-username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <label htmlFor="invite-role">Role</label>
        <select id="invite-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="user">User</option>
          <option value="admin">Admin</option>
        </select>
        <button type="submit" disabled={busy || username.trim() === ""}>
          Send invite
        </button>
        {error !== null && <p className="error">{error}</p>}
      </form>
      {invite !== null && (
        <p className="invite-link">
          Invite link for <strong>{invite.username}</strong>:{" "}
          <code>/accept-invite?token={invite.token}</code> (share it once — it isn&apos;t shown
          again).
        </p>
      )}
    </section>
  );
}

/** The account list, each row offering reset-password and (confirmed) delete. */
function AccountsList({
  users,
  onDeleted,
}: {
  users: AdminUser[];
  onDeleted: (id: string) => void;
}): ReactNode {
  return (
    <section aria-label="Accounts">
      <h2>Accounts</h2>
      <ul className="user-list">
        {users.map((u) => (
          <UserRow key={u.id} user={u} onDeleted={() => onDeleted(u.id)} />
        ))}
      </ul>
    </section>
  );
}

function UserRow({ user, onDeleted }: { user: AdminUser; onDeleted: () => void }): ReactNode {
  const [temp, setTemp] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onReset = async () => {
    setResetting(true);
    setError(null);
    try {
      const result = await resetUserPassword(user.id);
      setTemp(result.temporary_password);
    } catch (err) {
      setError(errorMessage(err, "Could not reset the password."));
    } finally {
      setResetting(false);
    }
  };

  const onDelete = async () => {
    if (confirmName !== user.username) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteUser(user.id, confirmName);
      onDeleted();
    } catch (err) {
      setError(errorMessage(err, "Could not delete the user."));
      setDeleting(false);
    }
  };

  return (
    <li className="user-item">
      <span className="user-name">{user.username}</span>
      <span className="user-role">{roleLabel(user.role)}</span>
      {user.must_change_password && <span className="badge">Password change owed</span>}
      <button type="button" onClick={onReset} disabled={resetting}>
        Reset password
      </button>
      {!confirming ? (
        <button type="button" onClick={() => setConfirming(true)}>
          Delete
        </button>
      ) : (
        <span className="confirm-delete">
          <label htmlFor={`confirm-${user.id}`}>Confirm username</label>
          <input
            id={`confirm-${user.id}`}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
          />
          <button
            type="button"
            onClick={onDelete}
            disabled={deleting || confirmName !== user.username}
          >
            Confirm delete
          </button>
        </span>
      )}
      {temp !== null && (
        <p className="temp-password">
          Temporary password: <code>{temp}</code>
        </p>
      )}
      {error !== null && <p className="error">{error}</p>}
    </li>
  );
}

/** Manage per-repository access grants: list, add, revoke. */
function GrantsPanel({
  users,
  repos,
}: {
  users: AdminUser[];
  repos: AdminRepository[];
}): ReactNode {
  const [selected, setSelected] = useState(repos[0]?.id ?? "");
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState(users[0]?.id ?? "");
  const [expires, setExpires] = useState("");
  const [granting, setGranting] = useState(false);

  const usernameOf = (id: string) => users.find((u) => u.id === id)?.username ?? id;

  useEffect(() => {
    if (selected === "") return;
    let cancelled = false;
    setGrants(null);
    setError(null);
    listGrants(selected)
      .then((g) => !cancelled && setGrants(g))
      .catch((err: unknown) => !cancelled && setError(errorMessage(err, "Failed to load grants.")));
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const onGrant = async (e: FormEvent) => {
    e.preventDefault();
    if (selected === "" || target === "") return;
    setGranting(true);
    setError(null);
    try {
      await grantAccess(selected, {
        user_id: target,
        expires_at: expires === "" ? null : new Date(expires).toISOString(),
      });
      const refreshed = await listGrants(selected);
      setGrants(refreshed);
      setExpires("");
    } catch (err) {
      setError(errorMessage(err, "Could not grant access."));
    } finally {
      setGranting(false);
    }
  };

  const onRevoke = async (userId: string) => {
    if (selected === "") return;
    setError(null);
    try {
      await revokeAccess(selected, userId);
      setGrants((prev) => prev?.filter((g) => g.user_id !== userId) ?? prev);
    } catch (err) {
      setError(errorMessage(err, "Could not revoke access."));
    }
  };

  return (
    <section aria-label="Repository access">
      <h2>Repository access</h2>
      <label htmlFor="grant-repo">Repository</label>
      <select id="grant-repo" value={selected} onChange={(e) => setSelected(e.target.value)}>
        {repos.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>

      <form onSubmit={onGrant}>
        <label htmlFor="grant-user">Grant to</label>
        <select id="grant-user" value={target} onChange={(e) => setTarget(e.target.value)}>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.username}
            </option>
          ))}
        </select>
        <label htmlFor="grant-expires">Expires</label>
        <input
          id="grant-expires"
          type="date"
          value={expires}
          onChange={(e) => setExpires(e.target.value)}
        />
        <button type="submit" disabled={granting || target === ""}>
          Grant access
        </button>
      </form>

      {error !== null && <p className="error">{error}</p>}
      {grants === null ? (
        <p>Loading grants…</p>
      ) : grants.length === 0 ? (
        <p>No grants yet.</p>
      ) : (
        <ul className="grant-list">
          {grants.map((g) => (
            <li key={g.id} className="grant-item">
              <span className="grant-user">{usernameOf(g.user_id)}</span>
              <span className="grant-expiry">
                {g.expires_at === null ? "no expiry" : `until ${g.expires_at}`}
              </span>
              <button type="button" onClick={() => onRevoke(g.user_id)}>
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
