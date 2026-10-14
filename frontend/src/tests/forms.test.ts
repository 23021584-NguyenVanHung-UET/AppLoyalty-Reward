import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { apiRequest, ApiError } from "../api/client";
import AdjustModal from "../components/AdjustModal.vue";
import RewardForm from "../components/RewardForm.vue";
import ProgramForm from "../components/ProgramForm.vue";
import { programDraft, programPayload } from "../lib/validation";
import { button, deferred, field } from "./helpers";

vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  apiRequest: vi.fn(),
}));
vi.mock("../composables/app", () => ({ notify: vi.fn() }));
const request = vi.mocked(apiRequest);
beforeEach(() => {
  request.mockReset();
});
describe("adjustment modal", () => {
  const create = () =>
    mount(AdjustModal, {
      props: { open: true, profileId: 1, balance: 10, max: 100 },
    });
  it("validates before allowing confirmation and shows an estimate", async () => {
    const wrapper = create();
    await field(wrapper, "Points change", "0");
    await field(wrapper, "Note", "abc");
    await button(wrapper, "Review adjustment").trigger("click");
    expect(wrapper.html()).toContain("Enter a non-zero whole number");
    expect(wrapper.html()).toContain("5 to 500 characters");
    expect(request).not.toHaveBeenCalled();
    await field(wrapper, "Points change", "-3");
    await field(wrapper, "Note", "Valid note");
    expect(wrapper.text()).toContain("from 10 to 7 (estimate only)");
    await button(wrapper, "Review adjustment").trigger("click");
    expect(wrapper.text()).toContain("Confirm points adjustment");
    expect(request).not.toHaveBeenCalled();
  });
  it("keeps one key and frozen payload across failure/retry; reopening creates another", async () => {
    const wrapper = create();
    request
      .mockRejectedValueOnce(
        new ApiError(422, "INSUFFICIENT_POINTS", "Insufficient balance"),
      )
      .mockResolvedValue({ duplicate: false });
    await field(wrapper, "Points change", "-20");
    await field(wrapper, "Note", "Valid note");
    await button(wrapper, "Review adjustment").trigger("click");
    await button(wrapper, "Confirm adjustment").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("No partial deduction was made");
    expect(wrapper.get("dialog").attributes()).toHaveProperty("open");
    await button(wrapper, "Review adjustment").trigger("click");
    await button(wrapper, "Confirm adjustment").trigger("click");
    await flushPromises();
    const first = request.mock.calls[0]?.[1]?.body;
    expect(request.mock.calls[1]?.[1]?.body).toEqual(first);
    expect(first).toMatchObject({ delta: -20, client_key: expect.any(String) });
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await field(wrapper, "Points change", "5");
    await field(wrapper, "Note", "Other note");
    await button(wrapper, "Review adjustment").trigger("click");
    await button(wrapper, "Confirm adjustment").trigger("click");
    await flushPromises();
    expect(
      (request.mock.calls[2]?.[1]?.body as { client_key: string }).client_key,
    ).not.toBe((first as { client_key: string }).client_key);
  });
  it("blocks double clicks while a request is unresolved", async () => {
    const pending = deferred<unknown>();
    request.mockReturnValue(pending.promise);
    const wrapper = create();
    await field(wrapper, "Points change", "5");
    await field(wrapper, "Note", "Valid note");
    await button(wrapper, "Review adjustment").trigger("click");
    const confirm = button(wrapper, "Confirm adjustment");
    await confirm.trigger("click");
    await confirm.trigger("click");
    expect(request).toHaveBeenCalledTimes(1);
    expect(
      (confirm.element as HTMLElement & { disabled: boolean }).disabled,
    ).toBe(true);
    pending.resolve({ duplicate: false });
    await flushPromises();
    expect(wrapper.emitted("saved")).toHaveLength(1);
  });
});
describe("version and reward forms", () => {
  it("shows a program diff before submitting and retains a 409 error", async () => {
    const config = {
      ...programPayload(programDraft(null)),
      id: 1,
      version: 1,
      created_by: "test",
      created_at: "2026-10-12T00:00:00Z",
      effective_from: "2026-10-12T00:00:00Z",
    };
    const wrapper = mount(ProgramForm, { props: { open: true, config } });
    await field(wrapper, "Spend required for one point", "20.00");
    await button(wrapper, "Review changes").trigger("click");
    expect(wrapper.text()).toContain("What will change");
    expect(wrapper.text()).toContain("10.00");
    expect(wrapper.text()).toContain("20.00");
    expect(request).not.toHaveBeenCalled();
    request.mockRejectedValue(
      new ApiError(409, "CONFLICT", "Please review the current version."),
    );
    await button(wrapper, "Confirm new version").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("CONFLICT");
    expect(wrapper.get("dialog").attributes()).toHaveProperty("open");
  });
  it("prevents percentage >100 and surfaces an API unsupported-cap error inline", async () => {
    const wrapper = mount(RewardForm, { props: { open: true, reward: null } });
    await field(wrapper, "Reward name", "Test reward");
    await field(wrapper, "Points required", "5");
    await field(wrapper, "Discount type", "PERCENTAGE");
    await field(wrapper, "Discount value", "101");
    await button(wrapper, "Review reward").trigger("click");
    expect(wrapper.html()).toContain("Percentage must not exceed 100");
    expect(request).not.toHaveBeenCalled();
    await field(wrapper, "Discount value", "10");
    await button(wrapper, "Review reward").trigger("click");
    expect(request).not.toHaveBeenCalled();
    request.mockRejectedValue(
      new ApiError(422, "UNSUPPORTED", "Unsupported percentage cap"),
    );
    await button(wrapper, "Confirm save").trigger("click");
    await flushPromises();
    const cap = wrapper
      .findAll("s-text-field")
      .find((item) => item.attributes("label") === "Maximum discount amount");
    expect(cap?.attributes("error")).toBe("Unsupported percentage cap");
  });
});
