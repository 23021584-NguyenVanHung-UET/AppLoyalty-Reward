import type {
  ProgramConfig,
  ProgramFields,
  ProgramInput,
  ReasonCode,
  Reward,
  RewardFields,
} from "../api/types";
import { label, toLocalInput } from "./format";

export type Errors = Record<string, string>;
export function integer(value: string, min: number, max: number): boolean {
  return (
    /^-?\d+$/.test(value) &&
    Number.isSafeInteger(Number(value)) &&
    Number(value) >= min &&
    Number(value) <= max
  );
}
function decimal(value: string, positive = false): boolean {
  return (
    /^\d{1,16}(\.\d{1,2})?$/.test(value) && (!positive || /[1-9]/.test(value))
  );
}
function hundredths(value: string): bigint {
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole + fraction.padEnd(2, "0"));
}
export function validateAdjustment(
  delta: string,
  note: string,
  max: number,
): Errors {
  const errors: Errors = {};
  if (!integer(delta, -max, max) || Number(delta) === 0)
    errors.delta = `Enter a non-zero whole number between -${max.toLocaleString("en-US")} and ${max.toLocaleString("en-US")}.`;
  if (note.trim().length < 5 || note.trim().length > 500)
    errors.note = "Enter a note with 5 to 500 characters.";
  return errors;
}
export const reasons: { value: ReasonCode; label: string }[] = [
  "INCIDENT_COMPENSATION",
  "GIFT",
  "CORRECTION",
  "COMPLAINT",
  "OTHER",
].map((value) => ({ value: value as ReasonCode, label: label(value) }));
export const adjustmentHelp: Record<string, string> = {
  INSUFFICIENT_POINTS:
    "The customer does not have enough available points. No partial deduction was made.",
  KEY_REUSED:
    "This request has already been used with different details. Close the dialog and start a new adjustment.",
  CUSTOMER_ANONYMIZED: "Anonymized customers cannot receive adjustments.",
  INVALID_DELTA:
    "This adjustment exceeds the shop limit or is not a valid whole number.",
};

export interface RewardDraft {
  name: string;
  description: string;
  points_cost: string;
  discount_type: "FIXED_AMOUNT" | "PERCENTAGE";
  discount_value: string;
  max_discount_amount: string;
  min_order_amount: string;
  code_valid_days: string;
  stock_limit: string;
  per_customer_limit: string;
  is_active: boolean;
  active_from: string;
  active_to: string;
  sort_order: string;
}
export function rewardDraft(reward?: Reward): RewardDraft {
  return {
    name: reward?.name ?? "",
    description: reward?.description ?? "",
    points_cost: String(reward?.points_cost ?? ""),
    discount_type: reward?.discount_type ?? "FIXED_AMOUNT",
    discount_value: reward?.discount_value ?? "",
    max_discount_amount: reward?.max_discount_amount ?? "",
    min_order_amount: reward?.min_order_amount ?? "",
    code_valid_days: String(reward?.code_valid_days ?? 30),
    stock_limit: reward?.stock_limit == null ? "" : String(reward.stock_limit),
    per_customer_limit:
      reward?.per_customer_limit == null
        ? ""
        : String(reward.per_customer_limit),
    is_active: reward?.is_active ?? true,
    active_from: toLocalInput(reward?.active_from ?? null),
    active_to: toLocalInput(reward?.active_to ?? null),
    sort_order: String(reward?.sort_order ?? 0),
  };
}
export function validateReward(draft: RewardDraft): Errors {
  const errors: Errors = {};
  if (!draft.name.trim() || draft.name.trim().length > 150)
    errors.name = "Enter a name with 1 to 150 characters.";
  if (draft.description.length > 5000)
    errors.description = "Use at most 5,000 characters.";
  if (!integer(draft.points_cost, 1, 2147483647))
    errors.points_cost = "Enter a positive whole number.";
  if (!decimal(draft.discount_value, true))
    errors.discount_value =
      "Enter a positive decimal with at most 2 decimal places.";
  if (draft.discount_type === "PERCENTAGE") {
    if (
      decimal(draft.discount_value) &&
      hundredths(draft.discount_value) > 10000n
    )
      errors.discount_value = "Percentage must not exceed 100.";
    if (draft.max_discount_amount)
      errors.max_discount_amount =
        "Capped percentage rewards are not supported.";
  }
  if (draft.max_discount_amount && !decimal(draft.max_discount_amount, true))
    errors.max_discount_amount = "Enter a positive decimal or leave blank.";
  if (draft.min_order_amount && !decimal(draft.min_order_amount))
    errors.min_order_amount = "Enter a non-negative decimal or leave blank.";
  if (!integer(draft.code_valid_days, 1, 3650))
    errors.code_valid_days = "Enter 1 to 3,650 days.";
  for (const key of ["stock_limit", "per_customer_limit"] as const)
    if (draft[key] && !integer(draft[key], 0, 2147483647))
      errors[key] =
        "Enter a non-negative whole number or leave blank for no limit.";
  if (!integer(draft.sort_order, -2147483648, 2147483647))
    errors.sort_order = "Enter a whole number.";
  for (const key of ["active_from", "active_to"] as const)
    if (draft[key] && Number.isNaN(new Date(draft[key]).valueOf()))
      errors[key] = "Enter a valid local date and time.";
  if (
    draft.active_from &&
    draft.active_to &&
    new Date(draft.active_from) >= new Date(draft.active_to)
  )
    errors.active_to = "End time must be after the start time.";
  return errors;
}
export function rewardPayload(draft: RewardDraft): RewardFields {
  return {
    ...draft,
    name: draft.name.trim(),
    points_cost: Number(draft.points_cost),
    code_valid_days: Number(draft.code_valid_days),
    stock_limit: draft.stock_limit === "" ? null : Number(draft.stock_limit),
    per_customer_limit:
      draft.per_customer_limit === "" ? null : Number(draft.per_customer_limit),
    max_discount_amount: draft.max_discount_amount || null,
    min_order_amount: draft.min_order_amount || null,
    active_from: draft.active_from
      ? new Date(draft.active_from).toISOString()
      : null,
    active_to: draft.active_to ? new Date(draft.active_to).toISOString() : null,
    sort_order: Number(draft.sort_order),
  };
}

export const programBooleanFields = [
  {key:"birthday_enabled",label:"Enable birthday points",help:"For members with an earned order. Birthday is stored as month/day only, with a 12-month edit lock."},
  {key:"referral_enabled",label:"Enable referrals",help:"Reward new customers and their referrers after order points become available. Pending referrals keep their saved entitlements."},
  {key: "tiers_enabled", label: "Enable membership tiers", help: "Apply the current membership tier multiplier to future processed orders."},
  {
    key: "is_enabled",
    label: "Enable loyalty program",
    help: "Allow future orders and redemptions to participate in the program.",
  },
  {
    key: "include_tax",
    label: "Include tax",
    help: "Include tax in the eligible order amount used to earn points.",
  },
  {
    key: "include_shipping",
    label: "Include shipping",
    help: "Include shipping charges in the eligible order amount.",
  },
  {
    key: "allow_negative_balance",
    label: "Allow negative balances",
    help: "Allow debt when revoking or manually deducting more points than the customer has. Customers in debt cannot redeem.",
  },
  {
    key: "refund_points_on_expired_code",
    label: "Refund expired codes",
    help: "Return spent points after an unused code expires and is closed by the background job.",
  },
  {
    key: "refund_points_on_cancelled_order",
    label: "Refund cancelled orders",
    help: "Return redeemed points according to the existing cancelled-order policy.",
  },
] as const;
export const programNumberFields = [
  {key:"birthday_points",label:"Birthday points",help:"Annual birthday bonus, available immediately. 0 grants no points.",min:0,max:1000000},
  {key:"referral_referrer_points",label:"Referral points for referrer",help:"Bonus for the person sharing the link.",min:0,max:1000000},
  {key:"referral_referee_points",label:"Referral points for new customer",help:"Bonus for the new customer completing their first eligible order.",min:0,max:1000000},
  {key:"referral_max_per_referrer_per_month",label:"Referrals per referrer / month",help:"Pending and rewarded referrals reserve a slot in the attribution month (shop timezone). 0 prevents referrals.",min:0,max:1000000},
  {key: "tier_window_months", label: "Tier qualification window (months)", help: "Rolling calendar months of net order points. 0 means lifetime.", min: 0, max: 120},
  {key: "tier_downgrade_grace_days", label: "Tier downgrade grace (days)", help: "Daily downgrades wait from the most recent qualifying drop below the current tier threshold.", min: 0, max: 3650},
  {
    key: "pending_days",
    label: "Pending period",
    help: "Days before earned points become available. 0 makes them available immediately.",
    min: 0,
    max: 365,
  },
  {
    key: "expiry_days",
    label: "Point expiry",
    help: "Days from earning until expiry, including the pending period. 0 means no expiry.",
    min: 0,
    max: 3650,
  },
  {
    key: "expiry_reminder_days",
    label: "Expiry reminder lead time",
    help: "Days before expiry for reminders. Enable the email type in Notifications.",
    min: 0,
    max: 3650,
  },
  {
    key: "max_redemptions_per_day",
    label: "Redemptions per 24 hours",
    help: "Maximum redemptions per customer in a rolling 24-hour period. 0 prevents redemptions.",
    min: 0,
    max: 1000000,
  },
  {
    key: "max_unused_codes_per_reward",
    label: "Unused codes per reward",
    help: "Maximum issued, unused codes per customer for each reward. 0 prevents issuing another code.",
    min: 0,
    max: 1000000,
  },
  {
    key: "welcome_points",
    label: "Welcome points",
    help: "Configured welcome-point amount. Changing this value does not award points to existing customers.",
    min: 0,
    max: 1000000,
  },
  {
    key: "negative_balance_alert_threshold",
    label: "Debt alert threshold",
    help: "Signed points at or below zero. Stored for debt monitoring; this screen does not send notifications.",
    min: -1000000000000,
    max: 0,
  },
] as const;
export interface ProgramDraft {
  welcome_trigger: "SIGNUP" | "FIRST_ORDER";
  birthday_enabled: boolean;
  birthday_points: string;
  referral_enabled: boolean;
  referral_referrer_points: string;
  referral_referee_points: string;
  referral_max_per_referrer_per_month: string;
  referral_min_order_amount: string;
  tiers_enabled: boolean;
  tier_window_months: string;
  tier_downgrade_grace_days: string;
  is_enabled: boolean;
  include_tax: boolean;
  include_shipping: boolean;
  allow_negative_balance: boolean;
  refund_points_on_expired_code: boolean;
  refund_points_on_cancelled_order: boolean;
  spend_per_point: string;
  pending_days: string;
  expiry_days: string;
  expiry_reminder_days: string;
  max_redemptions_per_day: string;
  max_unused_codes_per_reward: string;
  welcome_points: string;
  negative_balance_alert_threshold: string;
  excluded_product_ids: string;
  excluded_collection_ids: string;
  excluded_channels: string;
  effective_from: string;
}
export function programDraft(config: ProgramConfig | null): ProgramDraft {
  return {
    welcome_trigger:config?.welcome_trigger??"FIRST_ORDER",
    birthday_enabled:config?.birthday_enabled??false,
    birthday_points:String(config?.birthday_points??0),
    referral_enabled:config?.referral_enabled??false,
    referral_referrer_points:String(config?.referral_referrer_points??0),
    referral_referee_points:String(config?.referral_referee_points??0),
    referral_max_per_referrer_per_month:String(config?.referral_max_per_referrer_per_month??10),
    referral_min_order_amount:config?.referral_min_order_amount??"",
    tiers_enabled: config?.tiers_enabled ?? false,
    tier_window_months: String(config?.tier_window_months ?? 0),
    tier_downgrade_grace_days: String(config?.tier_downgrade_grace_days ?? 30),
    is_enabled: config?.is_enabled ?? true,
    spend_per_point: config?.spend_per_point ?? "10.00",
    include_tax: config?.include_tax ?? false,
    include_shipping: config?.include_shipping ?? false,
    allow_negative_balance: config?.allow_negative_balance ?? true,
    refund_points_on_expired_code:
      config?.refund_points_on_expired_code ?? true,
    refund_points_on_cancelled_order:
      config?.refund_points_on_cancelled_order ?? true,
    pending_days: String(config?.pending_days ?? 7),
    expiry_days: String(config?.expiry_days ?? 30),
    expiry_reminder_days: String(config?.expiry_reminder_days ?? 5),
    max_redemptions_per_day: String(config?.max_redemptions_per_day ?? 3),
    max_unused_codes_per_reward: String(
      config?.max_unused_codes_per_reward ?? 1,
    ),
    welcome_points: String(config?.welcome_points ?? 0),
    negative_balance_alert_threshold: String(
      config?.negative_balance_alert_threshold ?? -500,
    ),
    excluded_product_ids: config?.excluded_product_ids.join(", ") ?? "",
    excluded_collection_ids: config?.excluded_collection_ids.join(", ") ?? "",
    excluded_channels: config?.excluded_channels.join(", ") ?? "",
    effective_from: "",
  };
}
const split = (text: string) =>
  text
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
export function validateProgram(draft: ProgramDraft, now = Date.now()): Errors {
  const errors: Errors = {};
  if(!["SIGNUP","FIRST_ORDER"].includes(draft.welcome_trigger))errors.welcome_trigger="Choose a welcome trigger.";
  if(draft.referral_min_order_amount&&!decimal(draft.referral_min_order_amount))errors.referral_min_order_amount="Enter a non-negative amount with at most 2 decimals, or leave blank.";
  if (!decimal(draft.spend_per_point, true))
    errors.spend_per_point =
      "Enter a positive amount with at most 2 decimal places.";
  for (const field of programNumberFields)
    if (!integer(draft[field.key], field.min, field.max))
      errors[field.key] =
        `Enter a whole number from ${field.min.toLocaleString("en-US")} to ${field.max.toLocaleString("en-US")}.`;
  if (
    Number(draft.expiry_days) > 0 &&
    Number(draft.expiry_reminder_days) > Number(draft.expiry_days)
  )
    errors.expiry_reminder_days =
      "Reminder lead time cannot exceed the point expiry period.";
  for (const key of [
    "excluded_product_ids",
    "excluded_collection_ids",
  ] as const) {
    const ids = split(draft[key]);
    if (
      ids.length > 1000 ||
      ids.some((id) => !integer(id, 1, Number.MAX_SAFE_INTEGER))
    )
      errors[key] = "Enter up to 1,000 positive IDs separated by commas.";
  }
  const channels = split(draft.excluded_channels);
  if (channels.length > 100 || channels.some((channel) => channel.length > 100))
    errors.excluded_channels =
      "Use up to 100 channel names, at most 100 characters each.";
  if (
    draft.effective_from &&
    (Number.isNaN(new Date(draft.effective_from).valueOf()) ||
      new Date(draft.effective_from).valueOf() < now - 60000)
  )
    errors.effective_from =
      "Choose a time no more than 60 seconds in the past, or leave blank for now.";
  return errors;
}
export function programPayload(draft: ProgramDraft): ProgramInput {
  const data: ProgramInput = {
    welcome_trigger:draft.welcome_trigger,
    birthday_enabled:draft.birthday_enabled,
    birthday_points:Number(draft.birthday_points),
    referral_enabled:draft.referral_enabled,
    referral_referrer_points:Number(draft.referral_referrer_points),
    referral_referee_points:Number(draft.referral_referee_points),
    referral_max_per_referrer_per_month:Number(draft.referral_max_per_referrer_per_month),
    referral_min_order_amount:draft.referral_min_order_amount||null,
    tiers_enabled: draft.tiers_enabled,
    tier_window_months: Number(draft.tier_window_months),
    tier_downgrade_grace_days: Number(draft.tier_downgrade_grace_days),
    is_enabled: draft.is_enabled,
    spend_per_point: draft.spend_per_point,
    include_tax: draft.include_tax,
    include_shipping: draft.include_shipping,
    allow_negative_balance: draft.allow_negative_balance,
    refund_points_on_expired_code: draft.refund_points_on_expired_code,
    refund_points_on_cancelled_order: draft.refund_points_on_cancelled_order,
    pending_days: Number(draft.pending_days),
    expiry_days: Number(draft.expiry_days),
    expiry_reminder_days: Number(draft.expiry_reminder_days),
    max_redemptions_per_day: Number(draft.max_redemptions_per_day),
    max_unused_codes_per_reward: Number(draft.max_unused_codes_per_reward),
    welcome_points: Number(draft.welcome_points),
    negative_balance_alert_threshold: Number(
      draft.negative_balance_alert_threshold,
    ),
    excluded_product_ids: split(draft.excluded_product_ids).map(Number),
    excluded_collection_ids: split(draft.excluded_collection_ids).map(Number),
    excluded_channels: split(draft.excluded_channels),
  };
  if (draft.effective_from)
    data.effective_from = new Date(draft.effective_from).toISOString();
  return data;
}
export function programDiff(before: ProgramConfig | null, after: ProgramInput) {
  return Object.entries(after)
    .filter(
      ([key, value]) =>
        JSON.stringify(before?.[key as keyof ProgramFields]) !==
        JSON.stringify(value),
    )
    .map(([key, value]) => ({
      field: label(key),
      before: before?.[key as keyof ProgramFields] ?? "Not configured",
      after: value,
    }));
}
