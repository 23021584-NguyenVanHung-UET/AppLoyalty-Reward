<script setup lang="ts">
import { ref } from "vue";
import { apiRequest } from "../api/client";
import type { Page, Reward } from "../api/types";
import { useResource } from "../composables/resource";
import { useQuery } from "../composables/query";
import { useConfirmAction } from "../composables/confirm";
import { notify } from "../composables/app";
import LoadState from "../components/LoadState.vue";
import DataTable from "../components/DataTable.vue";
import RewardForm from "../components/RewardForm.vue";
import ActionDialog from "../components/ActionDialog.vue";
import MoneyText from "../components/MoneyText.vue";
import PointsText from "../components/PointsText.vue";
import DateText from "../components/DateText.vue";
import StatusBadge from "../components/StatusBadge.vue";
const query = useQuery(),
  confirm = useConfirmAction();
const rewards = useResource(
  (signal) =>
    apiRequest<Page<Reward>>("rewards", {
      signal,
      query: { page: query.page.value },
    }),
  [query.signature],
);
const formOpen = ref(false),
  selected = ref<Reward | null>(null);
function edit(reward: Reward | null) {
  selected.value = reward;
  formOpen.value = true;
}
async function saved() {
  formOpen.value = false;
  await rewards.reload();
}
function toggle(reward: Reward) {
  const active = !reward.is_active;
  confirm.ask(
    active ? "Activate reward?" : "Deactivate reward?",
    active
      ? "Customers will be able to redeem this reward during its active window."
      : "New redemptions will stop. Existing discount codes will keep their current terms.",
    async () => {
      await apiRequest(
        `rewards/${reward.id}/${active ? "activate" : "deactivate"}`,
        { method: "POST", body: {} },
      );
      notify(active ? "Reward activated." : "Reward deactivated.");
      await rewards.reload();
    },
    active ? "Activate" : "Deactivate",
    !active,
  );
}
</script>
<template>
  <s-page heading="Rewards" inlineSize="large"
    ><s-button slot="primary-action" variant="primary" @click="edit(null)"
      >Create reward</s-button
    ><s-section
      ><p class="muted">
        Set the rewards customers can exchange their points for. Listed in
        display order.
      </p>
      <LoadState
        :loading="rewards.loading.value"
        :error="rewards.error.value"
        @retry="rewards.reload"
        ><DataTable
          :headers="[
            'Reward',
            'Points',
            'Discount',
            'Status',
            'Active from',
            'Active until',
            'Actions',
          ]"
          :page="rewards.data.value"
          empty="Create your first reward to give customers a reason to earn points."
          @page="query.update({ page: $event }, false)"
          ><tr v-for="reward in rewards.data.value?.results" :key="reward.id">
            <td>
              <strong>{{ reward.name }}</strong>
              <p class="muted">{{ reward.description }}</p>
            </td>
            <td><PointsText :value="reward.points_cost" /></td>
            <td>
              <MoneyText
                v-if="reward.discount_type === 'FIXED_AMOUNT'"
                :value="reward.discount_value"
              /><span v-else>{{ reward.discount_value }}%</span>
            </td>
            <td>
              <StatusBadge :status="reward.is_active ? 'ACTIVE' : 'INACTIVE'" />
            </td>
            <td><DateText :value="reward.active_from" /></td>
            <td><DateText :value="reward.active_to" /></td>
            <td>
              <div class="actions">
                <s-button
                  :disabled.prop="confirm.state.busy"
                  @click="edit(reward)"
                  >Edit</s-button
                ><s-button
                  :disabled.prop="confirm.state.busy"
                  @click="toggle(reward)"
                  >{{ reward.is_active ? "Deactivate" : "Activate" }}</s-button
                >
              </div>
            </td>
          </tr></DataTable
        ></LoadState
      ></s-section
    ><RewardForm
      :open="formOpen"
      :reward="selected"
      @close="formOpen = false"
      @saved="saved" /><ActionDialog :controller="confirm"
  /></s-page>
</template>
