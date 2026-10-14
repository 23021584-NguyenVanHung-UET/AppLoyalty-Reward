<script setup lang="ts">
import { computed, watch } from "vue";
import { apiRequest } from "../api/client";
import type { Dashboard, Integrity } from "../api/types";
import { useQuery } from "../composables/query";
import { useResource } from "../composables/resource";
import { useApp } from "../composables/app";
import { points } from "../lib/format";
import LoadState from "../components/LoadState.vue";
import SelectField from "../components/SelectField.vue";
import StatusBadge from "../components/StatusBadge.vue";
const query = useQuery();
const days = computed(() =>
  ["7", "30", "90"].includes(query.value("days")) ? query.value("days") : "30",
);
const dashboard = useResource(
  (signal) =>
    apiRequest<Dashboard>("dashboard", { signal, query: { days: days.value } }),
  [days],
);
const health = useResource(
  (signal) =>
    apiRequest<Integrity>("integrity", { signal, query: { limit: 25 } }),
  [],
  false,
);
const { me } = useApp();
let checked = false;
watch(dashboard.data, (value) => {
  if (value && !checked) {
    checked = true;
    void health.reload();
  }
});
const cards = computed(() => {
  const data = dashboard.data.value;
  if (!data) return [];
  return [
    ["Points earned", data.period.earned.points],
    ["Points revoked", data.period.revoked.points],
    ["Points redeemed", data.period.redeemed.points],
    ["Points expired", data.period.expired.points],
    ["Outstanding available points", data.liability.available],
    ["Outstanding pending points", data.liability.pending],
    ["Members with a balance", data.profiles_with_balance],
    ["Profiles in debt", data.profiles_in_debt],
  ] as const;
});
</script>
<template>
  <s-page heading="Dashboard" inlineSize="large">
    <s-section v-if="me?.program_config?.tiers_enabled" heading="Tier distribution"><div class="kpi-grid"><article v-for="row in dashboard.data.value?.tier_distribution" :key="String(row.tier_id)" class="kpi"><p>{{row.tier__name??'No tier yet'}}</p><strong>{{row.count}}</strong></article></div></s-section>
    <s-section
      ><div class="toolbar">
        <div>
          <s-heading>Your loyalty program at a glance</s-heading>
          <p class="muted">
            Current program:
            {{
              me?.program_config
                ? `version ${me.program_config.version}`
                : "not configured"
            }}. Points, balances and activity from your ledger.
          </p>
        </div>
        <SelectField
          :model-value="days"
          @update:model-value="query.update({ days: $event })"
          label="Activity period"
          :options="[
            { value: '7', label: 'Last 7 days' },
            { value: '30', label: 'Last 30 days' },
            { value: '90', label: 'Last 90 days' },
          ]"
        />
      </div>
      <LoadState
        :loading="dashboard.loading.value"
        :error="dashboard.error.value"
        @retry="dashboard.reload"
        ><template v-if="dashboard.data.value">
          <s-banner
            v-if="(dashboard.data.value.webhook_events.FAILED ?? 0) > 0"
            heading="Some webhook events need attention"
            tone="warning"
            ><RouterLink to="/webhooks?status=FAILED"
              >Review failed webhook events</RouterLink
            ></s-banner
          >
          <s-banner
            v-if="health.data.value?.problems.length"
            heading="Ledger problems found"
            tone="critical"
            ><RouterLink to="/health"
              >Review the health check</RouterLink
            ></s-banner
          >
          <p v-if="health.loading.value" class="muted">
            Checking a sample of ledger profiles in the background…
          </p>
          <p v-if="health.error.value" class="muted">
            The background health check could not finish. Run a check from
            Health.
          </p>
          <p v-if="health.data.value?.truncated" class="muted">
            Health check sampled {{ health.data.value.scanned }} profiles. Use
            Health to check additional profiles.
          </p>
          <div class="kpi-grid">
            <article v-for="[title, value] in cards" :key="title" class="kpi">
              <p>{{ title }}</p>
              <strong>{{ points(value) }}</strong>
            </article>
          </div>
        </template></LoadState
      >
    </s-section>
    <div v-if="dashboard.data.value" class="split">
      <s-section heading="Redemptions by status"
        ><table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Count</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(count, status) in dashboard.data.value.redemptions"
              :key="status"
            >
              <td><StatusBadge :status="status" /></td>
              <td>{{ count?.toLocaleString("en-US") }}</td>
            </tr>
          </tbody>
        </table>
        <p
          v-if="!Object.keys(dashboard.data.value.redemptions).length"
          class="muted"
        >
          No redemptions yet.
        </p></s-section
      >
      <s-section heading="Webhook events by status"
        ><table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Count</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(count, status) in dashboard.data.value.webhook_events"
              :key="status"
            >
              <td><StatusBadge :status="status" /></td>
              <td>{{ count?.toLocaleString("en-US") }}</td>
            </tr>
          </tbody>
        </table>
        <p
          v-if="!Object.keys(dashboard.data.value.webhook_events).length"
          class="muted"
        >
          No events received yet.
        </p></s-section
      >
    </div>
  </s-page>
</template>
