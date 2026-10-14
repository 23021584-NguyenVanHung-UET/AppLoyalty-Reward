import { describe, expect, it } from "vitest";
import { money, points, localDate, utcDate, toLocalInput } from "../lib/format";
import {
  adjustmentHelp,
  programDiff,
  programDraft,
  programPayload,
  rewardDraft,
  rewardPayload,
  validateAdjustment,
  validateProgram,
  validateReward,
} from "../lib/validation";
import type { ProgramConfig } from "../api/types";

describe("formatting", () => {
  it("formats exact decimal strings beyond binary-float precision", () => {
    expect(money("9999999999999999.99", "USD")).toBe(
      "USD\u00a09,999,999,999,999,999.99",
    );
    expect(money("10.01", "USD")).toBe("USD\u00a010.01");
    expect(money(null, "USD")).toBe("Not set");
  });
  it("formats signed integer points and local dates with a UTC companion", () => {
    expect(points(1234567)).toBe("1,234,567");
    expect(points(-12, true)).toBe("-12");
    expect(points(12, true)).toBe("+12");
    const value = "2026-10-12T07:31:00Z";
    expect(localDate(value)).toBe(
      new Intl.DateTimeFormat("en-US", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value)),
    );
    expect(utcDate(value)).toBe("2026-10-12 07:31:00.000 UTC");
    expect(new Date(toLocalInput(value)).getTime()).toBe(
      new Date(value).getTime(),
    );
    expect(localDate(null)).toBe("—");
    expect(utcDate("invalid")).toBe("Invalid date");
  });
});
describe("form validation and payloads", () => {
  it.each(["0", "1.5", "NaN", "100001", "-100001"])(
    "rejects adjustment %s",
    (delta) => {
      expect(
        validateAdjustment(delta, "Valid note", 100000).delta,
      ).toBeTruthy();
    },
  );
  it("requires a trimmed adjustment note of 5..500 characters and maps action errors", () => {
    expect(validateAdjustment("-3", "   ", 100).note).toBeTruthy();
    expect(validateAdjustment("3", "x".repeat(501), 100).note).toBeTruthy();
    expect(validateAdjustment("-3", "Valid", 100)).toEqual({});
    for (const code of [
      "INSUFFICIENT_POINTS",
      "KEY_REUSED",
      "CUSTOMER_ANONYMIZED",
      "INVALID_DELTA",
    ])
      expect(adjustmentHelp[code]).toBeTruthy();
  });
  it("checks reward percentages, limits, window and preserves decimal strings", () => {
    const draft = {
      ...rewardDraft(),
      name: "Test reward",
      points_cost: "5",
      discount_value: "100.01",
      discount_type: "PERCENTAGE" as const,
    };
    expect(validateReward(draft).discount_value).toMatch(/100/);
    draft.discount_value = "100";
    expect(validateReward(draft)).toEqual({});
    draft.max_discount_amount = "20";
    expect(validateReward(draft).max_discount_amount).toMatch(/not supported/);
    draft.max_discount_amount = "";
    draft.active_from = "2026-10-13T10:00";
    draft.active_to = "2026-10-12T10:00";
    expect(validateReward(draft).active_to).toBeTruthy();
    draft.active_to = "";
    draft.points_cost = "0";
    expect(validateReward(draft).points_cost).toBeTruthy();
    draft.points_cost = "5";
    draft.discount_value = "10.01";
    expect(rewardPayload(draft).discount_value).toBe("10.01");
    expect(rewardPayload(draft).stock_limit).toBeNull();
  });
  it("validates program ranges, exclusion lists and effective time", () => {
    const draft = programDraft(null);
    expect(validateProgram(draft)).toEqual({});
    Object.assign(draft, {
      spend_per_point: "0",
      pending_days: "366",
      expiry_days: "30",
      expiry_reminder_days: "31",
      excluded_product_ids: "1,abc",
      negative_balance_alert_threshold: "1",
      effective_from: "2020-01-01T00:00",
    });
    expect(Object.keys(validateProgram(draft))).toEqual(
      expect.arrayContaining([
        "spend_per_point",
        "pending_days",
        "expiry_reminder_days",
        "excluded_product_ids",
        "negative_balance_alert_threshold",
        "effective_from",
      ]),
    );
  });
  it("shows only changed values in the version diff and defaults effective time to the server", () => {
    const fields = programPayload(programDraft(null));
    const current: ProgramConfig = {
      ...fields,
      id: 1,
      version: 1,
      created_by: "test",
      created_at: "2026-10-12T00:00:00Z",
      effective_from: "2026-10-12T00:00:00Z",
    };
    const draft = programDraft(current);
    draft.spend_per_point = "20.00";
    draft.pending_days = "10";
    const payload = programPayload(draft);
    expect(payload).not.toHaveProperty("effective_from");
    expect(programDiff(current, payload)).toEqual([
      { field: "Spend per point", before: "10.00", after: "20.00" },
      { field: "Pending days", before: 7, after: 10 },
    ]);
  });
});
