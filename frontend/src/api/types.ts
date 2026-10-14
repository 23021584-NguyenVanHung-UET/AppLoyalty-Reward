export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };
export interface Page<T> {
  count: number;
  page: number;
  pages: number;
  results: T[];
}
export interface ApiFailure {
  code: string;
  message: string;
  details?: JsonValue;
}
export type QueryValue = string | number | boolean | null | undefined;
export type Query = Record<string, QueryValue>;
export type ReasonCode =
  | "INCIDENT_COMPENSATION"
  | "GIFT"
  | "CORRECTION"
  | "COMPLAINT"
  | "OTHER";
export type LotStatus =
  | "PENDING"
  | "AVAILABLE"
  | "EXPIRED"
  | "CONSUMED"
  | "VOIDED";
export type LedgerType =
  | "EARN"
  | "REVOKE"
  | "REDEEM"
  | "REDEEM_REFUND"
  | "EXPIRE"
  | "ADJUST";
export type RedemptionStatus =
  | "PENDING_CODE"
  | "ISSUED"
  | "USED"
  | "EXPIRED"
  | "CANCELLED"
  | "FAILED";
export type WebhookStatus =
  | "RECEIVED"
  | "PROCESSING"
  | "PROCESSED"
  | "FAILED"
  | "SKIPPED";

export interface ProgramFields {
  welcome_trigger?: "SIGNUP" | "FIRST_ORDER";
  birthday_enabled?: boolean;
  birthday_points?: number;
  referral_enabled?: boolean;
  referral_referrer_points?: number;
  referral_referee_points?: number;
  referral_max_per_referrer_per_month?: number;
  referral_min_order_amount?: string | null;
  tiers_enabled?: boolean;
  tier_window_months?: number;
  tier_downgrade_grace_days?: number;
  is_enabled: boolean;
  spend_per_point: string;
  include_tax: boolean;
  include_shipping: boolean;
  excluded_product_ids: number[];
  excluded_collection_ids: number[];
  excluded_channels: string[];
  pending_days: number;
  expiry_days: number;
  expiry_reminder_days: number;
  max_redemptions_per_day: number;
  max_unused_codes_per_reward: number;
  refund_points_on_expired_code: boolean;
  refund_points_on_cancelled_order: boolean;
  allow_negative_balance: boolean;
  negative_balance_alert_threshold: number;
  welcome_points: number;
}
export interface ProgramConfig extends ProgramFields {
  id: number;
  version: number;
  effective_from: string;
  created_by: string;
  created_at: string;
}
export type ProgramInput = ProgramFields & { effective_from?: string };
export interface ProgramConfigHistory extends ProgramConfig {
  can_delete: boolean;
}
export interface Me {
  shop: string;
  currency: string;
  program_config: ProgramConfig | null;
  server_time: string;
  limits: { max_adjust_points: number };
}
export interface Dashboard {
  tier_distribution?: { tier_id: number | null; tier__name: string | null; count: number }[];
  days: number;
  period: Record<
    "earned" | "revoked" | "redeemed" | "expired",
    { count: number; points: number }
  >;
  liability: { available: number; pending: number };
  profiles_with_balance: number;
  profiles_in_debt: number;
  debt_points: number;
  redemptions: Partial<Record<RedemptionStatus, number>>;
  webhook_events: Partial<Record<WebhookStatus, number>>;
}
export interface Customer {
  tier?: { id: number; name: string; rank: number; multiplier: string } | null;
  tier_since?: string | null;
  id: number;
  shopify_customer_id: number;
  email: string;
  first_name: string;
  last_name: string;
  is_anonymized: boolean;
  is_blocked: boolean;
  blocked_reason: string;
  available_balance: number;
  pending_balance: number;
  lifetime_earned: number;
  created_at: string;
}
export interface CustomerDetail extends Customer {
  tier_status?: { enabled: boolean; qualifying_points: number; points_to_next_tier: number | null; next_tier: string | null };
  tier_history?: { id: number; from_name: string | null; to_name: string | null; reason: string; qualifying_points: number; created_at: string }[];
  lots_counts: Partial<Record<LotStatus, number>>;
}
export interface PointsLot {
  id: number;
  source_type: "ORDER" | "ADJUSTMENT" | "WELCOME" | "REFUND_BACK";
  source_order_id: number | null;
  points_total: number;
  points_remaining: number;
  status: LotStatus;
  available_at: string | null;
  expires_at: string | null;
  created_at: string;
}
export interface LedgerEntry {
  id: number;
  type: LedgerType;
  delta: number;
  balance_after: number;
  order_id: number | null;
  reason_code: ReasonCode | "";
  note: string;
  actor: string;
  created_at: string;
  rule_snapshot?: JsonValue;
}
export interface AdjustmentInput {
  delta: number;
  reason_code: ReasonCode;
  note: string;
  client_key: string;
}
export interface AdjustmentResult {
  available_balance: number;
  pending_balance: number;
  lifetime_earned: number;
  transaction_id: number;
  duplicate: boolean;
}
export interface RewardFields {
  name: string;
  description: string;
  points_cost: number;
  discount_type: "FIXED_AMOUNT" | "PERCENTAGE";
  discount_value: string;
  max_discount_amount: string | null;
  min_order_amount: string | null;
  code_valid_days: number;
  stock_limit: number | null;
  per_customer_limit: number | null;
  is_active: boolean;
  active_from: string | null;
  active_to: string | null;
  sort_order: number;
}
export interface Reward extends RewardFields {
  id: number;
  created_at: string;
  updated_at: string;
}
export interface Redemption {
  id: number;
  profile_id: number;
  reward_id: number;
  points_spent: number;
  status: RedemptionStatus;
  code_last4: string | null;
  code_expires_at: string | null;
  created_at: string;
}
export interface RedemptionDetail extends Redemption {
  discount_code: string | null;
  used_order_id: number | null;
  attempts: number;
  updated_at: string;
  last_error: string;
}
export interface WebhookEvent {
  id: number;
  topic: string;
  status: WebhookStatus;
  attempts: number;
  received_at: string;
  last_attempt_at: string | null;
  processed_at: string | null;
  error: string;
}
export interface WebhookDetail extends WebhookEvent {
  payload: JsonValue;
}
export interface IntegrityIssue {
  profile_id: number;
  codes: string[];
}
export interface Integrity {
  scanned: number;
  problems: IntegrityIssue[];
  warnings: IntegrityIssue[];
  truncated: boolean;
  next_after_id: number;
}
export interface AuditEntry {
  id: number;
  actor: string;
  action: string;
  target_type: string;
  target_id: string;
  before: JsonValue;
  after: JsonValue;
  created_at: string;
}
