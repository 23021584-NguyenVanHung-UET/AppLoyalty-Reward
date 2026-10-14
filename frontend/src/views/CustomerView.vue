<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { apiRequest, downloadCustomerData, asApiError } from "../api/client";
import type {
  CustomerDetail,
  LedgerEntry,
  Page,
  PointsLot,
} from "../api/types";
import { useQuery } from "../composables/query";
import { useResource } from "../composables/resource";
import { useApp, notify } from "../composables/app";
import { useConfirmAction } from "../composables/confirm";
import { label } from "../lib/format";
import LoadState from "../components/LoadState.vue";
import DataTable from "../components/DataTable.vue";
import SelectField from "../components/SelectField.vue";
import StatusBadge from "../components/StatusBadge.vue";
import PointsText from "../components/PointsText.vue";
import DateText from "../components/DateText.vue";
import AdjustModal from "../components/AdjustModal.vue";
import Modal from "../components/Modal.vue";
import Field from "../components/Field.vue";
import JsonViewer from "../components/JsonViewer.vue";
import ActionDialog from "../components/ActionDialog.vue";
import ErrorBanner from "../components/ErrorBanner.vue";
const query = useQuery(),
  { me } = useApp();
const id = computed(() => String(query.route.params.id));
const tab = computed(() =>
  ["overview", "lots", "ledger"].includes(query.value("tab"))
    ? query.value("tab")
    : "overview",
);
const profile = useResource(
  (signal) => apiRequest<CustomerDetail>(`customers/${id.value}`, { signal }),
  [id],
);
const lots = useResource(
  (signal) =>
    apiRequest<Page<PointsLot>>(`customers/${id.value}/lots`, {
      signal,
      query: { page: query.page.value, status: query.value("status") },
    }),
  [],
  false,
);
const ledger = useResource(
  (signal) =>
    apiRequest<Page<LedgerEntry>>(`customers/${id.value}/ledger`, {
      signal,
      query: { page: query.page.value, type: query.value("type") },
    }),
  [],
  false,
);
watch(
  query.signature,
  () => {
    if (tab.value === "lots") void lots.reload();
    if (tab.value === "ledger") void ledger.reload();
  },
  { immediate: true },
);
const adjustOpen = ref(false),
  blockOpen = ref(false),
  blockReason = ref(""),
  blockError = ref("");
const confirm = useConfirmAction();
const exportBusy=ref(false),privacyError=ref<ReturnType<typeof asApiError>|null>(null);
async function exportData(){
  if(exportBusy.value)return;exportBusy.value=true;privacyError.value=null;
  try{
    const blob=await downloadCustomerData(Number(id.value));
    const url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=`customer-${id.value}-data.json`;document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    notify('Customer data exported. This download is audited.');
  }catch(error){privacyError.value=asApiError(error);}finally{exportBusy.value=false;}
}
function anonymize(){
  confirm.ask('Permanently anonymize customer?',
    'This irreversibly clears personal information and notes. Points and financial history are retained. Pending reward-code issuance must finish first. Future orders will not earn points for this profile.',async()=>{
      await apiRequest(`customers/${id.value}/anonymize`,{method:'POST',body:{confirm:true}});
      notify('Customer anonymized.');await refreshed();
    },'Anonymize customer',true);
}
const snapshotOpen = ref(false),
  selectedEntry = ref<number | null>(null);
const snapshot = useResource(
  async (signal) => {
    const page = await apiRequest<Page<LedgerEntry>>(
      `customers/${id.value}/ledger`,
      {
        signal,
        query: { page: query.page.value, type: query.value("type"), detail: 1 },
      },
    );
    return (
      page.results.find((row) => row.id === selectedEntry.value)
        ?.rule_snapshot ?? null
    );
  },
  [],
  false,
);
watch(id, () => {
  adjustOpen.value = false;
  blockOpen.value = false;
  snapshotOpen.value = false;
  confirm.cancel();
});
function showSnapshot(entry: LedgerEntry) {
  selectedEntry.value = entry.id;
  snapshotOpen.value = true;
  void snapshot.reload();
}
async function refreshed() {
  adjustOpen.value = false;
  await profile.reload();
  if (tab.value === "ledger") await ledger.reload();
  if (tab.value === "lots") await lots.reload();
}
function block() {
  if (!blockReason.value.trim() || blockReason.value.length > 255) {
    blockError.value = "Enter a reason with 1 to 255 characters.";
    return;
  }
  confirm.ask(
    "Block customer?",
    "This customer will no longer be able to redeem points. Their existing balance is kept.",
    async () => {
      await apiRequest(`customers/${id.value}/block`, {
        method: "POST",
        body: { reason: blockReason.value.trim() },
      });
      blockOpen.value = false;
      notify("Customer blocked.");
      await profile.reload();
    },
    "Block customer",
  );
}
function unblock() {
  confirm.ask(
    "Unblock customer?",
    "This customer will be able to redeem again if the program and their balance allow it.",
    async () => {
      await apiRequest(`customers/${id.value}/unblock`, {
        method: "POST",
        body: {},
      });
      notify("Customer unblocked.");
      await profile.reload();
    },
    "Unblock customer",
    false,
  );
}
const lotOptions = [
  { value: "", label: "All statuses" },
  ...["PENDING", "AVAILABLE", "EXPIRED", "CONSUMED", "VOIDED"].map((value) => ({
    value,
    label: label(value),
  })),
];
const ledgerOptions = [
  { value: "", label: "All types" },
  ...["EARN", "REVOKE", "REDEEM", "REDEEM_REFUND", "EXPIRE", "ADJUST"].map(
    (value) => ({ value, label: label(value) }),
  ),
];
</script>
<template>
  <s-page heading="Customer details" inlineSize="large"
    ><RouterLink slot="breadcrumb-actions" to="/customers">Customers</RouterLink
    ><LoadState
      :loading="profile.loading.value"
      :error="profile.error.value"
      @retry="profile.reload"
      ><template v-if="profile.data.value">
        <s-section
          ><s-heading>{{
            [profile.data.value.first_name, profile.data.value.last_name]
              .filter(Boolean)
              .join(" ") || `Customer ${profile.data.value.shopify_customer_id}`
          }}</s-heading>
          <p class="muted">
            Shopify ID {{ profile.data.value.shopify_customer_id }} ·
            {{ profile.data.value.email || "No email provided" }}
          </p>
          <div class="badges">
            <s-badge v-if="profile.data.value.tier">{{profile.data.value.tier.name}} · ×{{profile.data.value.tier.multiplier}}</s-badge>
            <StatusBadge
              v-if="profile.data.value.is_blocked"
              status="BLOCKED"
            /><StatusBadge
              v-if="profile.data.value.is_anonymized"
              status="ANONYMIZED"
            /><StatusBadge
              v-if="profile.data.value.available_balance < 0"
              status="IN_DEBT"
            />
          </div>
          <div class="kpi-grid">
            <article class="kpi">
              <p>Available points</p>
              <strong
                ><PointsText :value="profile.data.value.available_balance"
              /></strong>
            </article>
            <article class="kpi">
              <p>Pending points</p>
              <strong
                ><PointsText :value="profile.data.value.pending_balance"
              /></strong>
            </article>
            <article class="kpi">
              <p>Lifetime earned</p>
              <strong
                ><PointsText :value="profile.data.value.lifetime_earned"
              /></strong>
            </article>
          </div>
          <div class="actions">
            <s-button :disabled.prop="exportBusy||confirm.state.busy" @click="exportData">Export data</s-button>
            <s-button :disabled.prop="profile.data.value.is_anonymized||confirm.state.busy||exportBusy" @click="anonymize">Anonymize customer</s-button>
            <s-button
              :disabled.prop="
                profile.data.value.is_anonymized || confirm.state.busy
              "
              variant="primary"
              @click="adjustOpen = true"
              >Adjust points</s-button
            ><s-button
              v-if="profile.data.value.is_blocked"
              :disabled.prop="confirm.state.busy"
              @click="unblock"
              >Unblock customer</s-button
            ><s-button
              v-else
              :disabled.prop="confirm.state.busy"
              @click="
                blockOpen = true;
                blockReason = '';
                blockError = '';
              "
              >Block customer</s-button
            >
          </div><ErrorBanner :error="privacyError"/></s-section
        >
        <s-section v-if="profile.data.value.tier_status?.enabled" heading="Membership tier">
          <p>Qualifying points: {{profile.data.value.tier_status.qualifying_points}} · Points to next tier: {{profile.data.value.tier_status.points_to_next_tier??'Top tier'}}</p>
          <table><thead><tr><th>From</th><th>To</th><th>Reason</th><th>Points</th><th>Date</th></tr></thead><tbody><tr v-for="row in profile.data.value.tier_history" :key="row.id"><td>{{row.from_name??'None'}}</td><td>{{row.to_name??'None'}}</td><td>{{row.reason}}</td><td>{{row.qualifying_points}}</td><td><DateText :value="row.created_at"/></td></tr></tbody></table>
          <p class="muted">Most recent 20 changes. Earlier changes remain in the tier history API.</p>
        </s-section>
        <s-section
          ><nav class="tabs" aria-label="Customer sections">
            <RouterLink
              v-for="name in ['overview', 'lots', 'ledger']"
              :key="name"
              :class="{ active: tab === name }"
              :to="{ query: { tab: name } }"
              >{{ label(name) }}</RouterLink
            >
          </nav>
          <dl v-if="tab === 'overview'" class="detail-list">
            <dt>Created</dt>
            <dd><DateText :value="profile.data.value.created_at" /></dd>
            <dt>Blocked reason</dt>
            <dd>{{ profile.data.value.blocked_reason || "Not blocked" }}</dd>
            <template
              v-for="(count, status) in profile.data.value.lots_counts"
              :key="status"
              ><dt>{{ label(status) }} lots</dt>
              <dd>{{ count }}</dd></template
            >
          </dl>
          <template v-if="tab === 'lots'"
            ><SelectField
              :model-value="query.value('status')"
              @update:model-value="query.update({ status: $event })"
              label="Lot status"
              :options="lotOptions"
            /><LoadState
              :loading="lots.loading.value"
              :error="lots.error.value"
              @retry="lots.reload"
              ><DataTable
                :page="lots.data.value"
                :headers="[
                  'Source',
                  'Total',
                  'Remaining',
                  'Status',
                  'Available',
                  'Expires',
                  'Source order',
                ]"
                @page="query.update({ page: $event }, false)"
                ><tr v-for="lot in lots.data.value?.results" :key="lot.id">
                  <td>{{ label(lot.source_type) }}</td>
                  <td><PointsText :value="lot.points_total" /></td>
                  <td><PointsText :value="lot.points_remaining" /></td>
                  <td><StatusBadge :status="lot.status" /></td>
                  <td><DateText :value="lot.available_at" /></td>
                  <td><DateText :value="lot.expires_at" /></td>
                  <td>{{ lot.source_order_id ?? "—" }}</td>
                </tr></DataTable
              ></LoadState
            ></template
          >
          <template v-if="tab === 'ledger'"
            ><SelectField
              :model-value="query.value('type')"
              @update:model-value="query.update({ type: $event })"
              label="Transaction type"
              :options="ledgerOptions"
            /><LoadState
              :loading="ledger.loading.value"
              :error="ledger.error.value"
              @retry="ledger.reload"
              ><DataTable
                :page="ledger.data.value"
                :headers="[
                  'Type',
                  'Change',
                  'Balance after',
                  'Order',
                  'Reason / note',
                  'Actor',
                  'Created',
                  'Details',
                ]"
                @page="query.update({ page: $event }, false)"
                ><tr
                  v-for="entry in ledger.data.value?.results"
                  :key="entry.id"
                >
                  <td><StatusBadge :status="entry.type" /></td>
                  <td><PointsText :value="entry.delta" signed /></td>
                  <td><PointsText :value="entry.balance_after" /></td>
                  <td>{{ entry.order_id ?? "—" }}</td>
                  <td>
                    {{ entry.reason_code ? label(entry.reason_code) : "—" }}
                    <p>{{ entry.note }}</p>
                  </td>
                  <td>{{ entry.actor }}</td>
                  <td><DateText :value="entry.created_at" /></td>
                  <td>
                    <s-button @click="showSnapshot(entry)">Details</s-button>
                  </td>
                </tr></DataTable
              ></LoadState
            ></template
          >
        </s-section>
        <AdjustModal
          :open="adjustOpen"
          :profile-id="profile.data.value.id"
          :balance="profile.data.value.available_balance"
          :max="me?.limits.max_adjust_points ?? 100000"
          @close="adjustOpen = false"
          @saved="refreshed"
        /> </template
    ></LoadState>
    <Modal
      :open="blockOpen"
      title="Block customer"
      :busy="confirm.state.busy"
      @close="blockOpen = false"
      ><Field
        v-model="blockReason"
        label="Reason for blocking"
        :error="blockError"
        :disabled="confirm.state.busy"
        help="Required. Maximum 255 characters."
      /><template #actions
        ><s-button
          :disabled.prop="confirm.state.busy"
          @click="blockOpen = false"
          >Close</s-button
        ><s-button
          variant="primary"
          :disabled.prop="confirm.state.busy"
          @click="block"
          >Review block</s-button
        ></template
      ></Modal
    >
    <ActionDialog :controller="confirm" />
    <Modal
      :open="snapshotOpen"
      title="Transaction rule snapshot"
      @close="snapshotOpen = false"
      ><LoadState
        :loading="snapshot.loading.value"
        :error="snapshot.error.value"
        @retry="snapshot.reload"
        ><JsonViewer :value="snapshot.data.value" /></LoadState
      ><template #actions
        ><s-button @click="snapshotOpen = false">Close</s-button></template
      ></Modal
    >
  </s-page>
</template>
