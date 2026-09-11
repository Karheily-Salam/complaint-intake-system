import type { ComplaintSchema, FieldGroup } from "@/types/api";

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

/** Date without the time, for scanning a list of tickets. */
export function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString();
}

// -------------------------------------------------------------- ticket fields

export interface CollectedFieldView {
  key: string;
  label: string;
  value: string;
}

// The ticket detail shows one flat table rather than sections, but the order
// still matters for scanning: who the customer is, then what the transaction
// was, then the long prose last so it cannot push the identifiers off-screen.
// That ordering is what the schema registry's `group` is used for here.
const GROUP_RANK: Record<FieldGroup, number> = {
  customer: 0,
  transaction: 1,
  details: 2,
  issue: 3,
};

/**
 * A ticket's collected values, labelled and ordered for the detail table.
 *
 * Labels and order come from the backend schema registry, so a new complaint
 * type needs no frontend change. A value whose key is not in the schema still
 * appears rather than being dropped: hiding data an agent may need is worse
 * than an imperfect label.
 *
 * `exclude` is presentation-only. It exists so the detail table can leave out
 * the long problem description, which is prose rather than a scannable value;
 * the field itself is untouched in the API, the workflow and the conversation
 * history, where the customer's original message still carries it.
 */
export function collectedFields(
  schemas: ComplaintSchema[],
  complaintType: string,
  values: Record<string, string>,
  exclude: readonly string[] = [],
): CollectedFieldView[] {
  const hidden = new Set(exclude);
  const specs = schemas.find((s) => s.type === complaintType)?.common_fields ?? [];
  const labelFor = buildLabelLookup(schemas);
  const ranked: (CollectedFieldView & { rank: number; order: number })[] = [];

  specs.forEach((spec, index) => {
    const value = values[spec.key];
    if (!value || hidden.has(spec.key)) return;
    ranked.push({
      key: spec.key,
      label: spec.label,
      value,
      rank: GROUP_RANK[spec.group] ?? GROUP_RANK.details,
      order: index,
    });
  });

  const placed = new Set(ranked.map((f) => f.key));
  Object.entries(values).forEach(([key, value], index) => {
    if (placed.has(key) || !value || hidden.has(key)) return;
    ranked.push({
      key,
      label: labelFor(key),
      value,
      rank: GROUP_RANK.details,
      order: specs.length + index,
    });
  });

  return ranked
    .sort((a, b) => a.rank - b.rank || a.order - b.order)
    .map(({ key, label, value }) => ({ key, label, value }));
}

/** The subject the reply composer shows. The server applies the same rule. */
export function replySubject(conversationSubject: string | null | undefined): string {
  const base = (conversationSubject ?? "").trim() || "Your complaint";
  return /^re:/i.test(base) ? base : `Re: ${base}`;
}
