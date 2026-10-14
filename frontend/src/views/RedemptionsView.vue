<script setup lang="ts">
import { computed, watch } from "vue";
import { apiRequest } from "../api/client";
import type { Page, Redemption, RedemptionDetail } from "../api/types";
import { useResource } from "../composables/resource";
import { useQuery } from "../composables/query";
import { useConfirmAction } from "../composables/confirm";
import { notify } from "../composables/app";
import { label } from "../lib/format";
import LoadState from "../components/LoadState.vue";
import DataTable from "../components/DataTable.vue";
import Field from "../components/Field.vue";
import SelectField from "../components/SelectField.vue";
import StatusBadge from "../components/StatusBadge.vue";
import PointsText from "../components/PointsText.vue";
import DateText from "../components/DateText.vue";
import Modal from "../components/Modal.vue";
import ActionDialog from "../components/ActionDialog.vue";
const query = useQuery(),
  confirm = useConfirmAction();
const rows = useResource(
  (signal) =>
    apiRequest<Page<Redemption>>("redemptions", {
      signal,
      query: {
        page: query.page.value,
        search: query.value("search"),
        status: query.value("status"),
      },
    }),
  [query.signature],
);
const detailId = computed(() =>
  /^\d+$/.test(query.value("detail")) ? query.value("detail") : "",
);
const detail = useResource(
  (signal) =>
    apiRequest<RedemptionDetail>(`redemptions/${detailId.value}`, { signal }),
  [],
  false,
);
watch(
  detailId,
  (value) => {
    if (value) void detail.reload();
  },
  { immediate: true },
);
const statuses = [
  { value: "", label: "All statuses" },
  ...["PENDING_CODE", "ISSUED", "USED", "EXPIRED", "CANCELLED", "FAILED"].map(
    (value) => ({ value, label: label(value) }),
  ),
];
function cancel(row: Redemption) {
  confirm.ask(
    "Cancel redemption?",
    "The Shopify discount code will be deactivated and the customer’s spent points will be refunded. A used code cannot be cancelled.",
    async () => {
      await apiRequest(`redemptions/${row.id}/cancel`, {
        method: "POST",
        body: {},
      });
      notify("Redemption cancelled and points refunded.");
      await rows.reload();
      if (detailId.value) await detail.reload();
    },
    "Cancel redemption",
  );
}
</script>
<template>
  <s-page heading="Redemptions" inlineSize="large"
    ><s-section
      ><div class="toolbar">
        <Field
          :model-value="query.value('search')"
          @update:model-value="query.update({ search: $event })"
          label="Search redemptions"
          help="Discount code, customer name, email or Shopify ID"
        /><SelectField
          :model-value="query.value('status')"
          @update:model-value="query.update({ status: $event })"
          label="Status"
          :options="statuses"
        />
      </div>
      <LoadState
        :loading="rows.loading.value"
        :error="rows.error.value"
        @retry="rows.reload"
        ><DataTable
          :page="rows.data.value"
          :headers="[
            'Redemption',
            'Customer',
            'Reward',
            'Points',
            'Status',
            'Code',
            'Created',
            'Actions',
          ]"
          empty="No redemptions match these filters. Redemptions appear when customers exchange points for rewards."
          @page="query.update({ page: $event }, false)"
          ><tr v-for="row in rows.data.value?.results" :key="row.id">
            <td>
              <s-button @click="query.update({ detail: row.id }, false)"
                >#{{ row.id }}</s-button
              >
            </td>
            <td>
              <RouterLink :to="`/customers/${row.profile_id}`"
                >Customer #{{ row.profile_id }}</RouterLink
              >
            </td>
            <td>#{{ row.reward_id }}</td>
            <td><PointsText :value="row.points_spent" /></td>
            <td><StatusBadge :status="row.status" /></td>
            <td>
              {{ row.code_last4 ? `•••• ${row.code_last4}` : "Not issued" }}
            </td>
            <td><DateText :value="row.created_at" /></td>
            <td>
              <s-button
                v-if="row.status === 'ISSUED'"
                :disabled.prop="confirm.state.busy"
                @click="cancel(row)"
                >Cancel</s-button
              >
            </td>
          </tr></DataTable
        ></LoadState
      ></s-section
    >
    <Modal
      :open="!!detailId"
      title="Redemption details"
      :busy="confirm.state.busy"
      @close="query.update({ detail: undefined }, false)"
      ><LoadState
        :loading="detail.loading.value"
        :error="detail.error.value"
        @retry="detail.reload"
        ><template v-if="detail.data.value"
          ><StatusBadge :status="detail.data.value.status" />
          <p v-if="detail.data.value.discount_code" class="code-detail">
            {{ detail.data.value.discount_code }}
          </p>
          <dl class="detail-list">
            <dt>Points spent</dt>
            <dd><PointsText :value="detail.data.value.points_spent" /></dd>
            <dt>Created</dt>
            <dd><DateText :value="detail.data.value.created_at" /></dd>
            <dt>Last updated</dt>
            <dd><DateText :value="detail.data.value.updated_at" /></dd>
            <dt>Code expires</dt>
            <dd><DateText :value="detail.data.value.code_expires_at" /></dd>
            <dt>Used on order</dt>
            <dd>{{ detail.data.value.used_order_id ?? "Not used" }}</dd>
            <dt>Issuance attempts</dt>
            <dd>{{ detail.data.value.attempts }}</dd>
            <dt>Latest issue</dt>
            <dd>{{ detail.data.value.last_error || "None recorded" }}</dd>
          </dl></template
        ></LoadState
      ><template #actions
        ><s-button
          :disabled.prop="confirm.state.busy"
          @click="query.update({ detail: undefined }, false)"
          >Close</s-button
        ><s-button
          v-if="detail.data.value?.status === 'ISSUED'"
          :disabled.prop="confirm.state.busy"
          tone="critical"
          @click="cancel(detail.data.value)"
          >Cancel redemption</s-button
        ></template
      ></Modal
    ><ActionDialog :controller="confirm"
  /></s-page>
</template>
