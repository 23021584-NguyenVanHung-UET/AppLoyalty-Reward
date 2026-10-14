import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { apiRequest, ApiError } from "../api/client";
import type { ProgramConfigHistory } from "../api/types";
import { programDraft, programPayload } from "../lib/validation";
import SettingsView from "../views/SettingsView.vue";
import { button, page } from "./helpers";

vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  apiRequest: vi.fn(),
  isEmbedded: () => false,
}));
const request = vi.mocked(apiRequest);
const current: ProgramConfigHistory = {
  ...programPayload(programDraft(null)),
  id: 2, version: 2, effective_from: "2026-10-12T07:28:00Z",
  created_by: "test", created_at: "2026-10-12T07:28:00Z", can_delete: false,
};
const scheduled: ProgramConfigHistory = {
  ...current, id: 3, version: 3, effective_from: "2026-10-17T02:00:00Z", can_delete: true,
};
let removed = false;
beforeEach(() => {
  removed = false;
  request.mockReset().mockImplementation(async (path, options) => {
    if (options?.method === "DELETE") { removed = true; return undefined; }
    if (path === "program-config") return current;
    if (path === "program-config/history") return page(removed ? [current] : [scheduled, current]);
    if (path === "me") return { shop: "test.myshopify.com", currency: "USD", program_config: current, limits: { max_adjust_points: 100000 } };
    throw new Error(`Unexpected request: ${path}`);
  });
});
async function open(path = "/settings") {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/settings", component: SettingsView }] });
  await router.push(path);
  await router.isReady();
  const wrapper = mount(SettingsView, { global: { plugins: [router] } });
  await flushPromises();
  return { wrapper, router };
}
describe("deleting scheduled program versions", () => {
  it("offers deletion only for scheduled rows and requires confirmation before refreshing", async () => {
    const { wrapper } = await open();
    expect(wrapper.findAll("tbody tr").filter(row => row.text().includes("Delete version"))).toHaveLength(1);
    await button(wrapper, "Delete version").trigger("click");
    expect(request.mock.calls.some(([, options]) => options?.method === "DELETE")).toBe(false);
    await button(wrapper, "Go back").trigger("click");
    expect(removed).toBe(false);
    await button(wrapper, "Delete version").trigger("click");
    await button(wrapper, "Confirm delete").trigger("click");
    await flushPromises();
    expect(request).toHaveBeenCalledWith("program-config/3", { method: "DELETE" });
    expect(wrapper.findAll("tbody tr")).toHaveLength(1);
    expect(wrapper.text()).toContain("USD");
    expect(wrapper.findAll("s-button").some(item => item.text() === "Delete version")).toBe(false);
    wrapper.unmount();
  });
  it("keeps the dialog and row when the version became effective before confirmation", async () => {
    const { wrapper } = await open();
    request.mockRejectedValueOnce(new ApiError(409, "CONFIG_ALREADY_EFFECTIVE", "This version is already effective."));
    await button(wrapper, "Delete version").trigger("click");
    await button(wrapper, "Confirm delete").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("CONFIG_ALREADY_EFFECTIVE");
    expect(wrapper.find('dialog[aria-label="Delete version 3?"]').attributes("open")).toBeDefined();
    expect(wrapper.findAll("tbody tr")).toHaveLength(2);
    wrapper.unmount();
  });
  it("returns to page one after deleting so an empty last page does not cause a 404", async () => {
    const { wrapper, router } = await open("/settings?page=2");
    await button(wrapper, "Delete version").trigger("click");
    await button(wrapper, "Confirm delete").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.page).toBe("1");
    expect(request).toHaveBeenCalledWith("program-config/history", expect.objectContaining({ query: { page: 1 } }));
    wrapper.unmount();
  });
});
