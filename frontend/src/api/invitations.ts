import { api } from "./client";

// Mirrors AcceptedUserResponse in src/contextvault/api/invitations.py.
export interface AcceptedUser {
  id: string;
  username: string;
  role: "admin" | "user";
}

/** Redeem an invite token by choosing a password; creates the account (public). */
export function acceptInvitation(token: string, password: string): Promise<AcceptedUser> {
  return api.post<AcceptedUser>("/invitations/accept", { token, password });
}
