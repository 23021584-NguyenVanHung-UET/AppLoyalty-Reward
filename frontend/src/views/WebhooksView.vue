<script setup lang="ts">
import { computed, watch } from "vue";
import { apiRequest } from "../api/client";
import type { Page, WebhookEvent, WebhookDetail } from "../api/types";
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
import DateText from "../components/DateText.vue";
import Modal from "../components/Modal.vue";
import JsonViewer from "../components/JsonViewer.vue";
import ActionDialog from "../components/ActionDialog.vue";
const query = useQuery(),
  confirm = useConfirmAction();
const rows = useResource(
  (signal) =>
    apiRequest<Page<WebhookEvent>>("webhook-events", {
      signal,
      query: {
        page: query.page.value,
        status: query.value("status"),
        topic: query.value("topic"),
      },
    }),
  [query.signature],
);
const detailId = computed(() =>
  /^\d+$/.test(query.value("detail")) ? query.value("detail") : "",
);
const detail = useResource(
  (signal) =>
    apiRequest<WebhookDetail>(`webhook-events/${detailId.value}`, { signal }),
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
  ...["RECEIVED", "PROCESSING", "PROCESSED", "FAILED", "SKIPPED"].map(
    (value) => ({ value, label: label(value) }),
  ),
];
function retry(row: WebhookEvent) {
  confirm.ask(
    "Retry webhook event?",
    "This resets the event’s attempts and status. The background worker will process it again; it is not immediately executed by this screen.",
    async () => {
      await apiRequest(`webhook-events/${row.id}/retry`, {
        method: "POST",
        body: {},
      });
      notify("Webhook reset for processing.");
      await rows.reload();
      if (detailId.value) await detail.reload();
    },
    "Retry event",
    false,
  );
}
</script>
<template>
  <s-page heading="Webhook events" inlineSize="large"
    ><s-section
      ><div class="toolbar">
        <SelectField
          :model-value="query.value('status')"
          @update:model-value="query.update({ status: $event })"
          label="Status"
          :options="statuses"
        /><Field
          :model-value="query.value('topic')"
          @update:model-value="query.update({ topic: $event })"
          label="Topic"
          help="Exact topic, for example orders/paid or customers/create"
        />
      </div>
      <LoadState
        :loading="rows.loading.value"
        :error="rows.error.value"
        @retry="rows.reload"
        ><DataTable
          :page="rows.data.value"
          :headers="[
            'Event',
            'Topic',
            'Status',
            'Attempts',
            'Latest issue',
            'Received',
            'Actions',
          ]"
          empty="No events match these filters. Check that your Shopify webhooks point to this backend."
          @page="query.update({ page: $event }, false)"
          ><tr v-for="row in rows.data.value?.results" :key="row.id">
            <td>
              <s-button @click="query.update({ detail: row.id }, false)"
                >#{{ row.id }}</s-button
              >
            </td>
            <td>{{ row.topic }}</td>
            <td><StatusBadge :status="row.status" /></td>
            <td>{{ row.attempts }}</td>
            <td>{{ row.error || "—" }}</td>
            <td><DateText :value="row.received_at" /></td>
            <td>
              <s-button
                v-if="['FAILED', 'SKIPPED'].includes(row.status)"
                :disabled.prop="confirm.state.busy"
                @click="retry(row)"
                >Retry</s-button
              >
            </td>
          </tr></DataTable
        ></LoadState
      ></s-section
    ><Modal
      :open="!!detailId"
      title="Webhook event details"
      :busy="confirm.state.busy"
      @close="query.update({ detail: undefined }, false)"
      ><LoadState
        :loading="detail.loading.value"
        :error="detail.error.value"
        @retry="detail.reload"
        ><template v-if="detail.data.value"
          ><StatusBadge :status="detail.data.value.status" />
          <dl class="detail-list">
            <dt>Topic</dt>
            <dd>{{ detail.data.value.topic }}</dd>
            <dt>Attempts</dt>
            <dd>{{ detail.data.value.attempts }}</dd>
            <dt>Received</dt>
            <dd><DateText :value="detail.data.value.received_at" /></dd>
            <dt>Last attempt</dt>
            <dd><DateText :value="detail.data.value.last_attempt_at" /></dd>
            <dt>Processed</dt>
            <dd><DateText :value="detail.data.value.processed_at" /></dd>
            <dt>Latest issue</dt>
            <dd>{{ detail.data.value.error || "None recorded" }}</dd>
          </dl>
          <s-heading>Masked payload</s-heading
          ><JsonViewer
            :value="detail.data.value.payload" /></template></LoadState
      ><template #actions
        ><s-button
          :disabled.prop="confirm.state.busy"
          @click="query.update({ detail: undefined }, false)"
          >Close</s-button
        ><s-button
          v-if="
            detail.data.value &&
            ['FAILED', 'SKIPPED'].includes(detail.data.value.status)
          "
          :disabled.prop="confirm.state.busy"
          @click="retry(detail.data.value)"
          >Retry event</s-button
        ></template
      ></Modal
    ><ActionDialog :controller="confirm"
  /></s-page>
</template>
