import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { apiRequest, ApiError } from "../api/client";
import CustomersView from "../views/CustomersView.vue";
import CustomerView from "../views/CustomerView.vue";
import RedemptionsView from "../views/RedemptionsView.vue";
import WebhooksView from "../views/WebhooksView.vue";
import { button, customer, deferred, field, page } from "./helpers";
import type { Component } from "vue";

vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  apiRequest: vi.fn(),
}));
const request = vi.mocked(apiRequest);
beforeEach(() => request.mockReset());
async function open(component: Component, path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/:pathMatch(.*)*", component }],
  });
  await router.push(path);
  await router.isReady();
  const wrapper = mount(component, { global: { plugins: [router] } });
  await flushPromises();
  return { wrapper, router };
}
describe("customer list and stale requests", () => {
  it("restores URL filters, paginates, resets page on filter changes and supports back", async () => {
    request.mockResolvedValue(page([customer], 50, 2, 2));
    const { wrapper, router } = await open(
      CustomersView,
      "/customers?search=Test&page=2&blocked=true",
    );
    expect(request.mock.calls[0]?.[1]?.query).toMatchObject({
      page: 2,
      search: "Test",
      blocked: "true",
    });
    await button(wrapper, "Previous").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.page).toBe("1");
    await field(wrapper, "Search customers", "Another");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({
      search: "Another",
      blocked: "true",
    });
    router.back();
    await flushPromises();
    expect(router.currentRoute.value.query.search).toBe("Test");
    expect(router.currentRoute.value.query.page).toBe("1");
  });
  it("has helpful empty and API error states", async () => {
    request
      .mockResolvedValueOnce(page([]))
      .mockRejectedValueOnce(
        new ApiError(503, "SERVICE_BUSY", "Try again later"),
      );
    const { wrapper, router } = await open(CustomersView, "/customers");
    expect(wrapper.text()).toContain("No customers match these filters");
    await router.push("/customers?blocked=true");
    await flushPromises();
    expect(wrapper.text()).toContain("SERVICE_BUSY");
    expect(wrapper.text()).toContain("Try again later");
  });
  it("aborts old parameters and ignores their late response", async () => {
    const stale = deferred<unknown>();
    request.mockReturnValueOnce(stale.promise).mockResolvedValueOnce(page([]));
    const { wrapper, router } = await open(CustomersView, "/customers");
    const signal = request.mock.calls[0]?.[1]?.signal;
    await router.push("/customers?search=Nobody");
    await flushPromises();
    expect(signal?.aborted).toBe(true);
    stale.resolve(page([customer]));
    await flushPromises();
    expect(wrapper.text()).toContain("No customers match these filters");
    expect(wrapper.text()).not.toContain("Test Customer");
    wrapper.unmount();
    expect(request.mock.calls[1]?.[1]?.signal?.aborted).toBe(true);
  });
});
describe("confirmation gates", () => {
  it("does not cancel a redemption until confirmed and keeps API errors open", async () => {
    request.mockImplementation(async (_path, options) =>
      options?.method === "POST"
        ? Promise.reject(new ApiError(409, "ALREADY_USED", "Code already used"))
        : page([
            {
              id: 1,
              profile_id: 1,
              reward_id: 1,
              points_spent: 5,
              status: "ISSUED",
              code_last4: "1234",
              created_at: "2026-10-12T00:00:00Z",
            },
          ]),
    );
    const { wrapper } = await open(RedemptionsView, "/redemptions");
    await button(wrapper, "Cancel").trigger("click");
    expect(
      request.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(0);
    expect(wrapper.text()).toContain("spent points will be refunded");
    await button(wrapper, "Go back").trigger("click");
    expect(
      request.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(0);
    await button(wrapper, "Cancel").trigger("click");
    await button(wrapper, "Cancel redemption").trigger("click");
    await flushPromises();
    expect(request).toHaveBeenCalledWith(
      "redemptions/1/cancel",
      expect.objectContaining({ method: "POST" }),
    );
    expect(wrapper.text()).toContain("ALREADY_USED");
    expect(
      wrapper
        .findAll("dialog")
        .some((dialog) => dialog.attributes("open") !== undefined),
    ).toBe(true);
  });
  it("validates a block reason and requires the second confirmation", async () => {
    request.mockResolvedValue(customer);
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/customers/:id", component: CustomerView }],
    });
    await router.push("/customers/1");
    await router.isReady();
    const wrapper = mount(CustomerView, { global: { plugins: [router] } });
    await flushPromises();
    await button(wrapper, "Block customer").trigger("click");
    await button(wrapper, "Review block").trigger("click");
    expect(wrapper.html()).toContain("Enter a reason");
    await field(wrapper, "Reason for blocking", "Test reason");
    await button(wrapper, "Review block").trigger("click");
    expect(
      request.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(0);
    const confirm = wrapper
      .findAll("s-button")
      .filter((item) => item.text() === "Block customer")
      .at(-1)!;
    await confirm.trigger("click");
    await flushPromises();
    expect(request).toHaveBeenCalledWith(
      "customers/1/block",
      expect.objectContaining({
        method: "POST",
        body: { reason: "Test reason" },
      }),
    );
  });
  it("requires confirmation to retry a failed event and prevents duplicate submits", async () => {
    const pending = deferred<unknown>();
    request.mockImplementation(async (_path, options) =>
      options?.method === "POST"
        ? pending.promise
        : page([
            {
              id: 1,
              topic: "orders/paid",
              status: "FAILED",
              attempts: 3,
              error: "Processing failed",
              received_at: "2026-10-12T00:00:00Z",
            },
          ]),
    );
    const { wrapper } = await open(WebhooksView, "/webhooks");
    await button(wrapper, "Retry").trigger("click");
    expect(
      request.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(0);
    const confirm = button(wrapper, "Retry event");
    await confirm.trigger("click");
    await confirm.trigger("click");
    expect(
      request.mock.calls.filter((call) => call[1]?.method === "POST"),
    ).toHaveLength(1);
    pending.resolve({});
    await flushPromises();
    expect(request).toHaveBeenCalledWith(
      "webhook-events/1/retry",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
