import type { ComplaintSchema } from "@/types/api";

/**
 * Human-readable names for complaint fields.
 *
 * The labels come from the backend's YAML schema registry (via /schemas) - the
 * same source the conversation engine uses - so the UI cannot drift from the
 * business configuration, and adding a complaint type needs no frontend change.
 * The fallbacks below only cover keys that are not part of any schema.
 */
const FALLBACK_LABELS: Record<string, string> = {
  user_id: "User ID",
  account_email: "Account email",
  withdrawal_transaction_id: "Withdrawal transaction ID",
  source_wallet_or_account: "Source wallet/account",
  transaction_date: "Transaction date",
  deposit_method: "Deposit method",
  problem_description: "Problem description",
};

export type LabelLookup = (key: string) => string;

/** Turn `some_field_name` into `Some field name` as a last resort. */
function humanise(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function buildLabelLookup(schemas: ComplaintSchema[]): LabelLookup {
  const fromSchemas: Record<string, string> = {};
  for (const schema of schemas) {
    for (const field of schema.common_fields) {
      fromSchemas[field.key] = field.label;
    }
  }
  return (key) => fromSchemas[key] ?? FALLBACK_LABELS[key] ?? humanise(key);
}

/** Complaint type -> its display label, again from the schema registry. */
export function buildTypeLookup(schemas: ComplaintSchema[]): LabelLookup {
  const byType: Record<string, string> = {};
  for (const schema of schemas) byType[schema.type] = schema.label;
  return (type) => byType[type] ?? humanise(type);
}

const STATUS_LABELS: Record<string, string> = {
  open: "Open",
  collecting_info: "Collecting information",
  validating: "Validating",
  completed: "Completed",
  abandoned: "Abandoned",
  new: "New",
  in_progress: "In progress",
  resolved: "Resolved",
  closed: "Closed",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? humanise(status);
}

export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}
