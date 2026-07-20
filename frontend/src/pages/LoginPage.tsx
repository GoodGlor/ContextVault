import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";

interface FromState {
  from?: { pathname: string };
}

/** Username + password sign-in. Card #35 layers accept-invite on top of this. */
export function LoginPage(): ReactNode {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { mustChangePassword } = await login(username, password);
      if (mustChangePassword) {
        navigate("/change-password", { replace: true });
        return;
      }
      const dest = (location.state as FromState | null)?.from?.pathname ?? "/";
      navigate(dest, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-card">
      <h1>Sign in</h1>
      <form onSubmit={onSubmit}>
        <label>
          Username
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
