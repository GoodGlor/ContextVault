import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";

/**
 * Set a new password. Reached either voluntarily or via the forced-change bounce
 * (a temp-password login). Card #35 refines the forced-change messaging.
 */
export function ChangePasswordPage(): ReactNode {
  const { session, changePassword } = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const forced = session?.mustChangePassword ?? false;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await changePassword(currentPassword, newPassword);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-card">
      <h1>Change password</h1>
      {forced && (
        <p className="form-notice" role="status">
          You must set a new password before continuing.
        </p>
      )}
      <form onSubmit={onSubmit}>
        <label>
          Current password
          <input
            name="current_password"
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
          />
        </label>
        <label>
          New password
          <input
            name="new_password"
            type="password"
            autoComplete="new-password"
            minLength={8}
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save password"}
        </button>
      </form>
    </div>
  );
}
