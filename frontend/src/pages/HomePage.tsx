import type { ReactNode } from "react";
import { useAuth } from "../auth/AuthContext";

/** Placeholder landing page — card #36 replaces this with the query experience. */
export function HomePage(): ReactNode {
  const { session } = useAuth();
  return (
    <div className="page">
      <h1>Welcome to ContextVault</h1>
      <p>
        Signed in as <strong>{session?.username}</strong> ({session?.role}).
      </p>
      <p>Ask a knowledge repository and get a cited answer — coming in the query UI.</p>
    </div>
  );
}
