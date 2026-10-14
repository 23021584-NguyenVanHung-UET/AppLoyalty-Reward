<script setup lang="ts">
import { computed, ref, shallowRef, watch } from "vue";
import { apiRequest, asApiError, type ApiError } from "../api/client";
import type {
  AdjustmentInput,
  AdjustmentResult,
  ReasonCode,
} from "../api/types";
import {
  adjustmentHelp,
  reasons,
  validateAdjustment,
  type Errors,
} from "../lib/validation";
import { points } from "../lib/format";
import { notify } from "../composables/app";
import Modal from "./Modal.vue";
import Field from "./Field.vue";
import SelectField from "./SelectField.vue";
import ErrorBanner from "./ErrorBanner.vue";

const props = defineProps<{
  open: boolean;
  profileId: number;
  balance: number;
  max: number;
}>();
const emit = defineEmits<{ close: []; saved: [] }>();
const delta = ref(""),
  note = ref(""),
  reason = ref<ReasonCode>("CORRECTION");
const key = ref(""),
  busy = ref(false),
  review = ref(false);
const errors = ref<Errors>({}),
  error = shallowRef<ApiError | null>(null);
const frozen = shallowRef<AdjustmentInput | null>(null);
watch(
  () => props.open,
  (open) => {
    if (!open) return;
    delta.value = "";
    note.value = "";
    reason.value = "CORRECTION";
    key.value = crypto.randomUUID();
    busy.value = false;
    review.value = false;
    errors.value = {};
    error.value = null;
    frozen.value = null;
  },
  { immediate: true },
);
const preview = computed(() =>
  Number.isSafeInteger(Number(delta.value)) && delta.value !== ""
    ? `Balance will change from ${points(props.balance)} to ${points(props.balance + Number(delta.value))} (estimate only).`
    : "Enter a whole number to preview the estimated balance.",
);
function prepare() {
  errors.value = validateAdjustment(delta.value, note.value, props.max);
  if (!Object.keys(errors.value).length) review.value = true;
}
async function save() {
  if (busy.value || !review.value) return;
  busy.value = true;
  error.value = null;
  frozen.value ??= {
    delta: Number(delta.value),
    reason_code: reason.value,
    note: note.value.trim(),
    client_key: key.value,
  };
  try {
    const result = await apiRequest<AdjustmentResult>(
      `customers/${props.profileId}/adjust`,
      { method: "POST", body: frozen.value },
    );
    notify(
      result.duplicate
        ? "This adjustment was already applied."
        : "Points adjusted.",
    );
    emit("saved");
  } catch (failure) {
    error.value = asApiError(failure);
    review.value = false;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <Modal
    :open="open"
    :title="review ? 'Confirm points adjustment' : 'Adjust points'"
    :busy="busy"
    @close="emit('close')"
  >
    <ErrorBanner :error="error" />
    <p v-if="error && adjustmentHelp[error.code]" role="alert">
      {{ adjustmentHelp[error.code] }}
    </p>
    <div class="form-grid">
      <Field
        v-model="delta"
        label="Points change"
        help="Use a positive number to add points or a negative number to deduct available points."
        :error="errors.delta"
        :disabled="busy || review || !!frozen"
      />
      <SelectField
        :model-value="reason"
        @update:model-value="reason = $event as ReasonCode"
        label="Reason"
        :options="reasons"
        :disabled="busy || review || !!frozen"
      />
      <Field
        v-model="note"
        label="Note"
        multiline
        :max-length="500"
        :help="`${note.trim().length}/500 characters · required, at least 5 characters`"
        :error="errors.note"
        :disabled="busy || review || !!frozen"
      />
    </div>
    <p class="estimate">{{ preview }}</p>
    <p v-if="review">
      This adds an immutable ledger entry. Confirm the amount and reason before
      applying it.
    </p>
    <p v-if="frozen && error" class="muted">
      Retry sends the same adjustment. To change it, close and reopen this
      dialog.
    </p>
    <template #actions
      ><s-button v-if="review" :disabled.prop="busy" @click="review = false"
        >Edit details</s-button
      ><s-button :disabled.prop="busy" @click="emit('close')">Close</s-button
      ><s-button
        v-if="!review"
        variant="primary"
        :disabled.prop="busy"
        @click="prepare"
        >Review adjustment</s-button
      ><s-button
        v-else
        variant="primary"
        :disabled.prop="busy"
        :loading.prop="busy"
        @click="save"
        >Confirm adjustment</s-button
      ></template
    >
  </Modal>
</template>
