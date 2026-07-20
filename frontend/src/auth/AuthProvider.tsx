import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { configureApi } from "../api/client";
import * as authApi from "../api/auth";
import { decodeToken } from "./jwt";
import { AuthContext, type Session } from "./AuthContext";

const STORAGE_KEY = "contextvault.session";

function loadSession(): Session | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === null) return null;
  try {
    const parsed = JSON.parse(raw) as Session;
    if (typeof parsed.token !== "string" || decodeToken(parsed.token) === null) return null;
    return parsed;
  } catch {
    return null;
  }
}

function sessionFromToken(token: string, username: string, mustChangePassword: boolean): Session {
  const claims = decodeToken(token);
  if (claims === null) throw new Error("Received a malformed token from the server");
  return { token, userId: claims.sub, role: claims.role, username, mustChangePassword };
}

export function AuthProvider({ children }: { children: ReactNode }): ReactNode {
  const [session, setSession] = useState<Session | null>(loadSession);

  // Keep a ref in sync so the API client's token getter always reads the latest
  // value without re-running the configure effect on every session change.
  const sessionRef = useRef(session);
  sessionRef.current = session;

  const persist = useCallback((next: Session | null) => {
    setSession(next);
    if (next === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }, []);

  const logout = useCallback(() => persist(null), [persist]);

  // Wire the API client once: it reads the token from the ref and clears the
  // session on any 401 (an expired or revoked token bounces the user to /login).
  useEffect(() => {
    configureApi({
      getToken: () => sessionRef.current?.token ?? null,
      onUnauthorized: () => persist(null),
    });
  }, [persist]);

  const login = useCallback(
    async (username: string, password: string) => {
      const res = await authApi.login(username, password);
      persist(sessionFromToken(res.access_token, username, res.must_change_password));
      return { mustChangePassword: res.must_change_password };
    },
    [persist],
  );

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      const res = await authApi.changePassword(currentPassword, newPassword);
      const username = sessionRef.current?.username ?? "";
      persist(sessionFromToken(res.access_token, username, res.must_change_password));
    },
    [persist],
  );

  const value = useMemo(
    () => ({ session, login, changePassword, logout }),
    [session, login, changePassword, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
