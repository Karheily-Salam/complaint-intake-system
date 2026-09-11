/**
 * Staff API key handling for the support dashboard.
 *
 * The key is never compiled into the bundle - a secret shipped in JavaScript
 * is not a secret. The agent types it into the dashboard and it is held in
 * sessionStorage, so it lives for that browser tab and disappears when the tab
 * closes.
 *
 * This is honest about what it is: the backend's existing shared-key scheme,
 * entered at runtime rather than embedded. It is proportionate for a
 * single-operator internal tool, and the trade-off is that a cross-site
 * scripting flaw on this origin could read the key - which is why the page
 * loads no third-party script and the server sends a strict CSP. A
 * multi-operator deployment would want real sessions with httpOnly cookies
 * instead, which is a backend change, not a frontend one.
 */

const STORAGE_KEY = "cis.staffApiKey";

export function getStaffKey(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    // Private modes and blocked site data both throw here.
    return null;
  }
}

export function setStaffKey(key: string): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, key);
  } catch {
    /* non-persistent session is still usable for this page load */
  }
}

export function clearStaffKey(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Why a staff request failed, so the UI can say something specific. */
export type StaffAuthState = "ok" | "unauthorized" | "not-configured" | "error";

export class StaffRequestError extends Error {
  constructor(
    readonly state: StaffAuthState,
    message: string,
  ) {
    super(message);
    this.name = "StaffRequestError";
  }
}

export function classifyStatus(status: number): StaffAuthState {
  if (status === 401 || status === 403) return "unauthorized";
  // The server fails closed with 503 when STAFF_API_KEY is unset, which is a
  // deployment state rather than a bad key - worth telling the agent apart.
  if (status === 503) return "not-configured";
  return status >= 200 && status < 300 ? "ok" : "error";
}
