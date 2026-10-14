import type { VueWrapper } from "@vue/test-utils";
import type { CustomerDetail, Page } from "../api/types";

export const customer: CustomerDetail = {
  id: 1,
  shopify_customer_id: 123,
  email: "",
  first_name: "Test",
  last_name: "Customer",
  is_anonymized: false,
  is_blocked: false,
  blocked_reason: "",
  available_balance: 10,
  pending_balance: 2,
  lifetime_earned: 12,
  created_at: "2026-10-12T07:31:00Z",
  lots_counts: { AVAILABLE: 1, PENDING: 1 },
};
export function page<T>(
  results: T[],
  count = results.length,
  current = 1,
  pages = 1,
): Page<T> {
  return { results, count, page: current, pages };
}
export function button(wrapper: VueWrapper, text: string) {
  const found = wrapper
    .findAll("s-button")
    .find((item) => item.text() === text);
  if (!found) throw new Error(`Missing button: ${text}`);
  return found;
}
export async function field(wrapper: VueWrapper, label: string, value: string) {
  const found = wrapper
    .findAll("s-text-field,s-text-area,s-select")
    .find((item) => item.attributes("label") === label);
  if (!found) throw new Error(`Missing field: ${label}`);
  (found.element as HTMLElement & { value: string }).value = value;
  await found.trigger(
    found.element.tagName === "S-SELECT" ? "change" : "input",
  );
}
export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
