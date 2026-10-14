import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import type { Router } from "vue-router";
import App from "../App.vue";
import { createAppRouter } from "../router";
import { apiRequest } from "../api/client";
import { customer, field, page } from "./helpers";

vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  apiRequest: vi.fn(),
  isEmbedded: () => true,
}));
const request = vi.mocked(apiRequest);
const routers: Router[] = [];
customElements.define("s-app-nav", class extends HTMLElement {});

beforeEach(() => {
  window.history.replaceState(null, "", "/app/");
  vi.stubGlobal("scrollTo", vi.fn());
  request.mockReset();
  request.mockImplementation(async (path) => {
    if (path === "me") return { shop: "test.myshopify.com", currency: "USD", program_config: null, limits: { max_adjust_points: 100000 } };
    if (path === "customers") return page([customer]);
    if (path === "dashboard") return {
      period: Object.fromEntries(["earned", "revoked", "redeemed", "expired"].map(key => [key, { count: 0, points: 0 }])),
      liability: { available: 0, pending: 0 }, profiles_with_balance: 0, profiles_in_debt: 0,
      redemptions: {}, webhook_events: {},
    };
    if (path === "integrity") return { problems: [], warnings: [], truncated: false, scanned: 0 };
    throw new Error(`Unexpected request: ${path}`);
  });
});
afterEach(() => {
  routers.splice(0).forEach(router => router.options.history.destroy());
});
async function open(url = "/app/", base = "/app/") {
  window.history.replaceState(null, "", url);
  const router = createAppRouter(base); routers.push(router);
  const wrapper = mount(App, { global: { plugins: [router] } });
  await router.isReady(); await flushPromises();
  return { router, wrapper };
}
describe("Shopify sidebar and Vue history routing", () => {
  it("publishes distinct pathname links without hashes or launch query parameters", async () => {
    const { wrapper } = await open("/app/?host=launch-context&embedded=1");
    const links = wrapper.findAll("s-app-nav s-link");
    expect(links.map(link => link.attributes("href"))).toEqual([
      "/app/", "/app/customers", "/app/rewards", "/app/earn-rules", "/app/campaigns", "/app/tiers", "/app/redemptions", "/app/settings", "/app/webhooks", "/app/health", "/app/reports", "/app/imports", "/app/referrals", "/app/notifications", "/app/notification-log",
    ]);
    expect(links[0]?.attributes("rel")).toBe("home");
  });
  it("renders the real customer search screen on a direct pathname load", async () => {
    const { router, wrapper } = await open("/app/customers?search=Test&page=2");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Test Customer"));
    expect(router.currentRoute.value.path).toBe("/customers");
    expect(wrapper.find('s-text-field[label="Search customers"]').exists()).toBe(true);
    expect(wrapper.text()).not.toContain("Your loyalty program at a glance");
    expect(request).toHaveBeenCalledWith("customers", expect.objectContaining({ query: expect.objectContaining({ search: "Test", page: 2 }) }));
  });
  it("updates RouterView when browser history moves to a sidebar destination", async () => {
    const { router, wrapper } = await open();
    await vi.waitFor(() => expect(wrapper.text()).toContain("Your loyalty program at a glance"));
    const href = wrapper.findAll("s-app-nav s-link").find(link => link.text() === "Customers")!.attributes("href")!;
    // Model a browser/App Bridge history transition; no Shopify CDN is loaded.
    window.history.pushState(window.history.state, "", href);
    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
    await vi.waitFor(() => expect(wrapper.text()).toContain("Test Customer"));
    expect(router.currentRoute.value.path).toBe("/customers");
    expect(wrapper.text()).not.toContain("Your loyalty program at a glance");
  });
  it("keeps filters in pathname queries and restores the screen on browser back", async () => {
    const { router, wrapper } = await open("/app/customers?search=Test&page=2");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Test Customer"));
    await field(wrapper, "Search customers", "Another"); await flushPromises();
    expect(window.location.pathname).toBe("/app/customers");
    expect(new URLSearchParams(window.location.search).get("search")).toBe("Another");
    expect(new URLSearchParams(window.location.search).has("page")).toBe(false);
    await router.push("/"); await flushPromises(); router.back();
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe("/customers"));
    expect(router.currentRoute.value.query.search).toBe("Another");
    expect(wrapper.find('s-text-field[label="Search customers"]').exists()).toBe(true);
  });
  it("migrates old hash links while retaining filters and existing launch context", async () => {
    const { router, wrapper } = await open("/app/?host=launch-context#/customers?search=Test&page=2");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Test Customer"));
    expect(window.location.pathname).toBe("/app/customers");
    expect(window.location.hash).toBe("");
    expect(router.currentRoute.value.query).toEqual({ host: "launch-context", search: "Test", page: "2" });
    expect(wrapper.find('s-app-nav s-link[href="/app/customers"]').exists()).toBe(true);
  });
  it("uses the Vite pathname base during local development", async () => {
    const { wrapper, router } = await open("/app-static/customers", "/app-static/");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Test Customer"));
    expect(router.currentRoute.value.path).toBe("/customers");
    expect(wrapper.find('s-app-nav s-link[href="/app-static/rewards"]').exists()).toBe(true);
  });
});
