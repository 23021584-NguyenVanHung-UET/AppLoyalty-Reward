<script setup lang="ts">
import { ref } from "vue";
import { apiRequest } from "../api/client";
import type { AuditEntry, Integrity, Page } from "../api/types";
import { useResource } from "../composables/resource";
import { useQuery } from "../composables/query";
import { label } from "../lib/format";
import LoadState from "../components/LoadState.vue";
import DataTable from "../components/DataTable.vue";
import Field from "../components/Field.vue";
import DateText from "../components/DateText.vue";
import Modal from "../components/Modal.vue";
import JsonViewer from "../components/JsonViewer.vue";
const query = useQuery(),
  lastRun = ref<string | null>(null);
const health = useResource(
  async (signal) => {
    const result = await apiRequest<Integrity>("integrity", {
      signal,
      query: {
        limit: query.value("limit", "25"),
        after_id: query.value("after_id", "0"),
      },
    });
    lastRun.value = new Date().toISOString();
    return result;
  },
  [],
  false,
);
const audit = useResource(
  (signal) =>
    apiRequest<Page<AuditEntry>>("audit-log", {
      signal,
      query: {
        page: query.page.value,
        action: query.value("action"),
        target_type: query.value("target_type"),
        target_id: query.value("target_id"),
      },
    }),
  [query.signature],
);
const selected = ref<AuditEntry | null>(null);
async function nextBatch() {
  if (!health.data.value) return;
  await query.update({ after_id: health.data.value.next_after_id }, false);
  await health.reload();
}
</script>
<template>
  <s-page heading="Health" inlineSize="large"
    ><s-section heading="Ledger integrity"
      ><p class="muted">
        Read-only checks compare balances, lots and the append-only ledger. Each
        run checks a bounded batch; checks never refresh in a loop.
      </p>
      <div class="toolbar">
        <Field
          :model-value="query.value('limit', '25')"
          @update:model-value="query.update({ limit: $event }, false)"
          label="Profiles per batch"
          help="1–100; the server may enforce a smaller limit."
        /><Field
          :model-value="query.value('after_id', '0')"
          @update:model-value="query.update({ after_id: $event }, false)"
          label="Start after profile ID"
          help="0 starts at the first profile."
        /><s-button
          variant="primary"
          :disabled.prop="health.loading.value"
          :loading.prop="health.loading.value"
          @click="health.reload"
          >Run integrity check</s-button
        >
      </div>
      <p v-if="lastRun" class="muted">
        Last completed: <DateText :value="lastRun" />
      </p>
      <LoadState
        :loading="health.loading.value"
        :error="health.error.value"
        @retry="health.reload"
        ><template v-if="health.data.value"
          ><p>
            Checked {{ health.data.value.scanned }} profiles in this batch.
          </p>
          <s-banner
            v-if="!health.data.value.problems.length"
            heading="No integrity problems in this batch"
            tone="success"
            >Warnings can still identify background work that needs
            attention.</s-banner
          >
          <div class="split">
            <div>
              <s-heading>Problems</s-heading>
              <ul v-if="health.data.value.problems.length">
                <li
                  v-for="issue in health.data.value.problems"
                  :key="issue.profile_id"
                >
                  <RouterLink :to="`/customers/${issue.profile_id}`"
                    >Profile #{{ issue.profile_id }}</RouterLink
                  >
                  <ul>
                    <li v-for="code in issue.codes" :key="code">
                      {{ label(code) }} <code>{{ code }}</code>
                    </li>
                  </ul>
                </li>
              </ul>
              <p v-else class="muted">No problems.</p>
            </div>
            <div>
              <s-heading>Warnings</s-heading>
              <ul v-if="health.data.value.warnings.length">
                <li
                  v-for="issue in health.data.value.warnings"
                  :key="issue.profile_id"
                >
                  <RouterLink :to="`/customers/${issue.profile_id}`"
                    >Profile #{{ issue.profile_id }}</RouterLink
                  >
                  <ul>
                    <li v-for="code in issue.codes" :key="code">
                      {{ label(code) }} <code>{{ code }}</code>
                    </li>
                  </ul>
                </li>
              </ul>
              <p v-else class="muted">No warnings.</p>
            </div>
          </div>
          <s-button
            v-if="health.data.value.truncated"
            :disabled.prop="health.loading.value"
            @click="nextBatch"
            >Check next batch</s-button
          ></template
        >
        <p v-else class="muted">
          Run a check when you need to verify ledger health.
        </p></LoadState
      ></s-section
    ><s-section heading="Audit log"
      ><div class="toolbar">
        <Field
          :model-value="query.value('action')"
          @update:model-value="query.update({ action: $event })"
          label="Action"
          help="Exact action, for example points.adjust"
        /><Field
          :model-value="query.value('target_type')"
          @update:model-value="query.update({ target_type: $event })"
          label="Target type"
          help="For example CustomerProfile or PointsTransaction"
        /><Field
          :model-value="query.value('target_id')"
          @update:model-value="query.update({ target_id: $event })"
          label="Target ID"
        />
      </div>
      <LoadState
        :loading="audit.loading.value"
        :error="audit.error.value"
        @retry="audit.reload"
        ><DataTable
          :page="audit.data.value"
          :headers="['Action', 'Actor', 'Target', 'Created', 'Changes']"
          @page="query.update({ page: $event }, false)"
          ><tr v-for="row in audit.data.value?.results" :key="row.id">
            <td>{{ row.action }}</td>
            <td>{{ row.actor }}</td>
            <td>{{ row.target_type }} #{{ row.target_id }}</td>
            <td><DateText :value="row.created_at" /></td>
            <td><s-button @click="selected = row">View changes</s-button></td>
          </tr></DataTable
        ></LoadState
      ></s-section
    ><Modal
      :open="!!selected"
      title="Audit entry changes"
      @close="selected = null"
      ><template v-if="selected"
        ><s-heading>Before</s-heading
        ><JsonViewer :value="selected.before" /><s-heading>After</s-heading
        ><JsonViewer :value="selected.after" /></template
      ><template #actions
        ><s-button @click="selected = null">Close</s-button></template
      ></Modal
    ></s-page
  >
</template>
