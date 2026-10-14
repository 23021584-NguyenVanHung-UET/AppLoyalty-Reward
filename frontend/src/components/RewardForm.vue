<script setup lang="ts">
import { ref, shallowRef, watch } from "vue";
import { apiRequest, asApiError, type ApiError } from "../api/client";
import type { Reward } from "../api/types";
import {
  rewardDraft,
  rewardPayload,
  validateReward,
  type Errors,
} from "../lib/validation";
import { notify } from "../composables/app";
import Modal from "./Modal.vue";
import Field from "./Field.vue";
import SelectField from "./SelectField.vue";
import CheckField from "./CheckField.vue";
import ErrorBanner from "./ErrorBanner.vue";
const props = defineProps<{ open: boolean; reward: Reward | null }>();
const emit = defineEmits<{ close: []; saved: [] }>();
const draft = ref(rewardDraft()),
  errors = ref<Errors>({}),
  review = ref(false),
  busy = ref(false);
const error = shallowRef<ApiError | null>(null);
watch(
  () => props.open,
  (open) => {
    if (open) {
      draft.value = rewardDraft(props.reward ?? undefined);
      errors.value = {};
      error.value = null;
      review.value = false;
    }
  },
  { immediate: true },
);
function prepare() {
  errors.value = validateReward(draft.value);
  if (!Object.keys(errors.value).length) review.value = true;
}
async function save() {
  if (busy.value || !review.value) return;
  errors.value = validateReward(draft.value);
  if (Object.keys(errors.value).length) {
    review.value = false;
    return;
  }
  busy.value = true;
  error.value = null;
  try {
    await apiRequest<Reward>(
      props.reward ? `rewards/${props.reward.id}` : "rewards",
      {
        method: props.reward ? "PATCH" : "POST",
        body: rewardPayload(draft.value),
      },
    );
    notify(props.reward ? "Reward updated." : "Reward created.");
    emit("saved");
  } catch (failure) {
    error.value = asApiError(failure);
    if (error.value.code === "UNSUPPORTED")
      errors.value.max_discount_amount = error.value.message;
    review.value = false;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <Modal
    :open="open"
    :title="
      review
        ? 'Confirm reward settings'
        : reward
          ? 'Edit reward'
          : 'Create reward'
    "
    :busy="busy"
    @close="emit('close')"
    ><ErrorBanner :error="error" />
    <p class="page-note">
      Changes apply to future redemptions. Previously issued rewards keep their
      original terms.
    </p>
    <div class="form-stack">
      <Field
        v-model="draft.name"
        label="Reward name"
        :error="errors.name"
        :disabled="busy || review"
      />
      <Field
        v-model="draft.description"
        label="Description"
        multiline
        :error="errors.description"
        :disabled="busy || review"
      />
      <div class="form-grid">
        <Field
          v-model="draft.points_cost"
          label="Points required"
          :error="errors.points_cost"
          :disabled="busy || review"
        /><SelectField
          :model-value="draft.discount_type"
          @update:model-value="
            draft.discount_type = $event as 'FIXED_AMOUNT' | 'PERCENTAGE'
          "
          label="Discount type"
          :options="[
            { value: 'FIXED_AMOUNT', label: 'Fixed amount' },
            { value: 'PERCENTAGE', label: 'Percentage' },
          ]"
          :disabled="busy || review"
        /><Field
          v-model="draft.discount_value"
          label="Discount value"
          :help="
            draft.discount_type === 'PERCENTAGE'
              ? 'Percentage from 0.01 to 100.'
              : 'Amount in shop currency, up to 2 decimal places.'
          "
          :error="errors.discount_value"
          :disabled="busy || review"
        /><Field
          v-model="draft.max_discount_amount"
          label="Maximum discount amount"
          help="Optional for fixed amount. Capped percentage rewards are not supported."
          :error="errors.max_discount_amount"
          :disabled="busy || review"
        /><Field
          v-model="draft.min_order_amount"
          label="Minimum order amount"
          help="Shop currency. Leave blank for no minimum."
          :error="errors.min_order_amount"
          :disabled="busy || review"
        /><Field
          v-model="draft.code_valid_days"
          label="Code validity (days)"
          :error="errors.code_valid_days"
          :disabled="busy || review"
        /><Field
          v-model="draft.stock_limit"
          label="Total redemption limit"
          help="Leave blank for no limit; 0 means unavailable."
          :error="errors.stock_limit"
          :disabled="busy || review"
        /><Field
          v-model="draft.per_customer_limit"
          label="Redemptions per customer"
          help="Leave blank for no limit."
          :error="errors.per_customer_limit"
          :disabled="busy || review"
        /><Field
          v-model="draft.sort_order"
          label="Display order"
          help="Lower numbers appear first."
          :error="errors.sort_order"
          :disabled="busy || review"
        />
      </div>
      <div class="form-grid">
        <label
          >Active from (local time)<input
            v-model="draft.active_from"
            type="datetime-local"
            :disabled="busy || review"
          /><span class="field-error">{{ errors.active_from }}</span></label
        ><label
          >Active until (local time)<input
            v-model="draft.active_to"
            type="datetime-local"
            :disabled="busy || review"
          /><span class="field-error">{{ errors.active_to }}</span></label
        >
      </div>
      <CheckField
        v-model="draft.is_active"
        label="Reward is active"
        help="Customers can redeem it only during its active window and when limits allow it."
        :disabled="busy || review"
      />
      <p v-if="review">
        Save these reward settings? This will not modify existing redemptions.
      </p>
    </div>
    <template #actions
      ><s-button :disabled.prop="busy" @click="emit('close')">Close</s-button
      ><s-button v-if="review" :disabled.prop="busy" @click="review = false"
        >Edit details</s-button
      ><s-button
        v-if="!review"
        variant="primary"
        :disabled.prop="busy"
        @click="prepare"
        >Review reward</s-button
      ><s-button
        v-else
        variant="primary"
        :disabled.prop="busy"
        :loading.prop="busy"
        @click="save"
        >Confirm save</s-button
      ></template
    ></Modal
  >
</template>
