/**
 * Complaint-type grouping shared by the staff dashboard and the public demo.
 *
 * The two screens differ in where their data comes from - the dashboard asks
 * the server for one page per group, the demo filters a small bounded list it
 * already holds - but they must group, order and label tickets identically.
 * That common part lives here.
 */

import type { ComplaintSchema, TicketSummary } from "@/types/api";

/**
 * The order the groups are shown in. Presentation only.
 *
 * Deliberately an explicit list rather than anything derived from the schema
 * registry: the reading order an agent triages in is a UI decision, and tying
 * it to a backend property (alphabetical order, or the open_schema flag) means
 * an unrelated backend edit can silently rearrange the dashboard. A complaint
 * type not named here keeps its registry order and appears after these three.
 */
export const GROUP_ORDER = ["deposit", "withdrawal", "other"];

/**
 * Plural, scannable headings. Which groups *exist* still comes from the schema
 * registry; an unlisted type falls back to its own schema label, so a new
 * complaint type appears without a frontend change.
 */
const GROUP_TITLES: Record<string, string> = {
  deposit: "Deposits",
  withdrawal: "Withdrawals",
  other: "Other",
};

export function groupTitle(schema: ComplaintSchema): string {
  return GROUP_TITLES[schema.type] ?? schema.label;
}

/** Schemas in display order: the named types first, anything else after. */
export function orderSchemas(schemas: ComplaintSchema[]): ComplaintSchema[] {
  const rank = (type: string) => {
    const index = GROUP_ORDER.indexOf(type);
    return index === -1 ? GROUP_ORDER.length : index;
  };
  // Array.prototype.sort is stable, so unlisted types keep their registry order.
  return [...schemas].sort((a, b) => rank(a.type) - rank(b.type));
}

/** What the filter controls currently ask for. */
export interface TicketQueryState {
  status: string;
  q: string;
}

/** One group's worth of rows, however the caller obtained them. */
export interface TicketGroupData {
  items: TicketSummary[];
  total: number;
  page: number;
}

/**
 * Group an already-fetched list client-side.
 *
 * Only for the demo, whose entire dataset is synthetic, small and returned by
 * a single bounded request. The staff dashboard must never do this: real
 * ticket data is filtered and paginated by the server so the browser only ever
 * receives the page it asked for.
 *
 * The filter mirrors the backend's own: exact status match, and a search that
 * matches the reference or the customer email, case-insensitively.
 */
export function buildLocalGroups(
  tickets: TicketSummary[],
  types: string[],
  filter: TicketQueryState,
  pageSize: number,
  pages: Record<string, number>,
): Record<string, TicketGroupData> {
  const needle = filter.q.trim().toLowerCase();
  const matches = (ticket: TicketSummary) => {
    if (filter.status && ticket.status !== filter.status) return false;
    if (!needle) return true;
    return (
      ticket.reference.toLowerCase().includes(needle) ||
      ticket.customer.email.toLowerCase().includes(needle)
    );
  };

  const groups: Record<string, TicketGroupData> = {};
  for (const type of types) {
    const all = tickets.filter((t) => t.type === type && matches(t));
    const pageCount = Math.max(1, Math.ceil(all.length / pageSize));
    // Clamp: a filter change can leave the group on a page that no longer exists.
    const page = Math.min(Math.max(1, pages[type] ?? 1), pageCount);
    groups[type] = {
      items: all.slice((page - 1) * pageSize, page * pageSize),
      total: all.length,
      page,
    };
  }
  return groups;
}
