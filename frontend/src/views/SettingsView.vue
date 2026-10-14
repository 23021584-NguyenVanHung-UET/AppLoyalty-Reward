<script setup lang="ts">
import { ref } from "vue";
import { apiRequest } from "../api/client";
import type { Page, ProgramConfig, ProgramConfigHistory } from "../api/types";
import { useResource } from "../composables/resource";
import { useQuery } from "../composables/query";
import { notify, useApp } from "../composables/app";
import { useConfirmAction } from "../composables/confirm";
import { label } from "../lib/format";
import LoadState from "../components/LoadState.vue";
import DataTable from "../components/DataTable.vue";
import ProgramForm from "../components/ProgramForm.vue";
import MoneyText from "../components/MoneyText.vue";
import DateText from "../components/DateText.vue";
import Modal from "../components/Modal.vue";
import JsonViewer from "../components/JsonViewer.vue";
import ErrorBanner from "../components/ErrorBanner.vue";
import ActionDialog from "../components/ActionDialog.vue";
const query = useQuery(),
  app = useApp(),
  confirm = useConfirmAction(),
  formOpen = ref(false);
const effective = useResource((signal) =>
  apiRequest<ProgramConfig | null>("program-config", { signal }),
);
const history = useResource(
  (signal) =>
    apiRequest<Page<ProgramConfigHistory>>("program-config/history", {
      signal,
      query: { page: query.page.value },
    }),
  [query.signature],
);
const selected = ref<ProgramConfig | null>(null);
const show = (value: unknown) =>
  Array.isArray(value)
    ? value.join(", ") || "None"
    : typeof value === "boolean"
      ? value
        ? "Yes"
        : "No"
      : String(value);
const shopRefresh = useResource(() => app.load(), [], false);
async function saved() {
  formOpen.value = false;
  await Promise.all([
    effective.reload(),
    history.reload(),
    shopRefresh.reload(),
  ]);
}
function deleteVersion(row: ProgramConfigHistory) {
  confirm.ask(
    `Delete version ${row.version}?`,
    "This scheduled version will not take effect. The current configuration and existing points stay unchanged. A record of this deletion is retained.",
    async () => {
      await apiRequest<void>(`program-config/${row.id}`, { method: "DELETE" });
      notify(`Version ${row.version} deleted.`);
      if (query.page.value > 1) await query.update({ page: 1 }, false);
      await saved();
    },
    "Confirm delete",
    true,
  );
}
</script>
<template>
  <s-page heading="Program settings" inlineSize="large"
    ><s-button
      slot="primary-action"
      variant="primary"
      :disabled.prop="effective.loading.value || !!effective.error.value"
      @click="formOpen = true"
      >Create new version</s-button
    ><s-section heading="Effective configuration"
      ><p class="muted">
        Settings are versioned and read-only after creation. A new version
        affects future events; earned lots keep their original pending and
        expiry dates.
      </p>
      <LoadState
        :loading="effective.loading.value"
        :error="effective.error.value"
        @retry="effective.reload"
        ><template v-if="effective.data.value"
          ><p>
            Version <strong>{{ effective.data.value.version }}</strong> ·
            Effective
            <DateText :value="effective.data.value.effective_from" /> · Created
            by {{ effective.data.value.created_by }}
          </p>
          <dl class="detail-list">
            <template v-for="(value, key) in effective.data.value" :key="key"
              ><template
                v-if="
                  ![
                    'id',
                    'version',
                    'effective_from',
                    'created_at',
                    'created_by',
                  ].includes(key)
                "
                ><dt>{{ label(key) }}</dt>
                <dd>
                  <MoneyText
                    v-if="key === 'spend_per_point'"
                    :value="String(value)"
                  /><template v-else>{{ show(value) }}</template>
                </dd></template
              ></template
            >
          </dl></template
        >
        <p v-else>
          No configuration is effective yet. Create a version to configure the
          program.
        </p></LoadState
      ></s-section
    >
    <ErrorBanner :error="shopRefresh.error.value"
      ><s-button @click="shopRefresh.reload"
        >Refresh shop context</s-button
      ></ErrorBanner
    >
    <s-section heading="Version history"
      ><p class="muted">
        Only versions scheduled for the future can be deleted. Current and past
        versions are retained for your records.
      </p><LoadState
        :loading="history.loading.value"
        :error="history.error.value"
        @retry="history.reload"
        ><DataTable
          :page="history.data.value"
          :headers="[
            'Version',
            'Effective from',
            'Status',
            'Created by',
            'Created',
            'Actions',
          ]"
          @page="query.update({ page: $event }, false)"
          ><tr v-for="row in history.data.value?.results" :key="row.id">
            <td>{{ row.version }}</td>
            <td><DateText :value="row.effective_from" /></td>
            <td>{{ row.can_delete ? 'Scheduled' : row.id === effective.data.value?.id ? 'Current' : 'Previous' }}</td>
            <td>{{ row.created_by }}</td>
            <td><DateText :value="row.created_at" /></td>
            <td>
              <div class="actions">
                <s-button @click="selected = row">View version</s-button>
                <s-button
                  v-if="row.can_delete"
                  tone="critical"
                  :disabled.prop="confirm.state.busy"
                  @click="deleteVersion(row)"
                  >Delete version</s-button
                >
              </div>
            </td>
          </tr></DataTable
        ></LoadState
      ></s-section
    >
    <ProgramForm
      :open="formOpen"
      :config="effective.data.value"
      @close="formOpen = false"
      @saved="saved"
    /><Modal
      :open="!!selected"
      title="Saved program version"
      @close="selected = null"
      ><JsonViewer :value="selected" /><template #actions
        ><s-button @click="selected = null">Close</s-button></template
      ></Modal
    ><ActionDialog :controller="confirm" /></s-page
  >
</template>
