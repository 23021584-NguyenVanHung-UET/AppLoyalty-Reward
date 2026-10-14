<script setup lang="ts">
import { computed, ref, shallowRef, watch } from "vue";
import { apiRequest, asApiError, type ApiError } from "../api/client";
import type { ProgramConfig } from "../api/types";
import {
  programBooleanFields,
  programNumberFields,
  programDraft,
  programPayload,
  programDiff,
  validateProgram,
  type Errors,
} from "../lib/validation";
import { notify } from "../composables/app";
import Modal from "./Modal.vue";
import Field from "./Field.vue";
import CheckField from "./CheckField.vue";
import SelectField from "./SelectField.vue";
import ErrorBanner from "./ErrorBanner.vue";
const props = defineProps<{ open: boolean; config: ProgramConfig | null }>();
const emit = defineEmits<{ close: []; saved: [] }>();
const draft = ref(programDraft(null)),
  errors = ref<Errors>({}),
  review = ref(false),
  busy = ref(false);
const error = shallowRef<ApiError | null>(null);
watch(
  () => props.open,
  (open) => {
    if (open) {
      draft.value = programDraft(props.config);
      errors.value = {};
      review.value = false;
      error.value = null;
    }
  },
  { immediate: true },
);
const diff = computed(() =>
  programDiff(props.config, programPayload(draft.value)),
);
function prepare() {
  errors.value = validateProgram(draft.value);
  if (!Object.keys(errors.value).length) review.value = true;
}
async function save() {
  if (busy.value || !review.value) return;
  errors.value = validateProgram(draft.value);
  if (Object.keys(errors.value).length) {
    review.value = false;
    return;
  }
  busy.value = true;
  error.value = null;
  try {
    await apiRequest<ProgramConfig>("program-config", {
      method: "POST",
      body: programPayload(draft.value),
    });
    notify("A new program version was created.");
    emit("saved");
  } catch (failure) {
    error.value = asApiError(failure);
    review.value = false;
  } finally {
    busy.value = false;
  }
}
const show = (value: unknown) =>
  Array.isArray(value)
    ? value.join(", ") || "None"
    : typeof value === "boolean"
      ? value
        ? "Yes"
        : "No"
      : String(value);
</script>
<template>
  <Modal
    :open="open"
    :title="
      review ? 'Confirm new program version' : 'Create new program version'
    "
    :busy="busy"
    @close="emit('close')"
    ><ErrorBanner :error="error" /><s-banner
      heading="Versioned settings"
      tone="info"
      >Existing points and redemptions keep their saved rules. Pending and
      expiry periods are stored per lot when points are earned.</s-banner
    >
    <div v-if="!review" class="form-stack">
      <SelectField :model-value="draft.welcome_trigger" label="Welcome trigger" :options="[{value:'FIRST_ORDER',label:'First earned order'},{value:'SIGNUP',label:'Verified signup within 24 hours'}]" :disabled="busy" :error="errors.welcome_trigger" @update:model-value="draft.welcome_trigger=$event as 'SIGNUP'|'FIRST_ORDER'"/>
      <Field v-model="draft.referral_min_order_amount" label="Referral minimum eligible order amount" help="Shop currency, after discounts and earning exclusions, before multipliers. Leave blank for no minimum." :error="errors.referral_min_order_amount" :disabled="busy"/>
      <Field
        v-model="draft.spend_per_point"
        label="Spend required for one point"
        help="Eligible spending in shop currency, after discounts and configured exclusions. Greater than zero; up to 2 decimal places."
        :error="errors.spend_per_point"
        :disabled="busy"
      />
      <div class="form-grid">
        <Field
          v-for="field in programNumberFields"
          :key="field.key"
          v-model="draft[field.key]"
          :label="field.label"
          :help="field.help"
          :error="errors[field.key]"
          :disabled="busy"
        />
      </div>
      <CheckField
        v-for="field in programBooleanFields"
        :key="field.key"
        v-model="draft[field.key]"
        :label="field.label"
        :help="field.help"
        :disabled="busy"
      />
      <Field
        v-model="draft.excluded_product_ids"
        label="Excluded product IDs"
        multiline
        help="Positive Shopify product IDs separated by commas. Leave blank to exclude none."
        :error="errors.excluded_product_ids"
        :disabled="busy"
      />
      <Field
        v-model="draft.excluded_collection_ids"
        label="Excluded collection IDs"
        multiline
        help="Positive Shopify collection IDs separated by commas. Leave blank to exclude none."
        :error="errors.excluded_collection_ids"
        :disabled="busy"
      />
      <Field
        v-model="draft.excluded_channels"
        label="Excluded sales channels"
        help="Channel names separated by commas. Leave blank to exclude none."
        :error="errors.excluded_channels"
        :disabled="busy"
      />
      <label
        >Effective from (local time)<input
          v-model="draft.effective_from"
          type="datetime-local"
          :disabled="busy"
        /><span class="field-help"
          >Leave blank to take effect when saved. Existing versions are never
          edited.</span
        ><span v-if="errors.effective_from" class="field-error">{{
          errors.effective_from
        }}</span></label
      >
    </div>
    <div v-else>
      <s-heading>What will change</s-heading>
      <p>
        Effective from:
        {{
          draft.effective_from
            ? new Date(draft.effective_from).toLocaleString("en-US")
            : "When saved (server time)"
        }}
      </p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Setting</th>
              <th>Current</th>
              <th>New version</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="change in diff" :key="change.field">
              <td>{{ change.field }}</td>
              <td>{{ show(change.before) }}</td>
              <td>{{ show(change.after) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!diff.length">
        Values are unchanged. This still creates a new, immutable version.
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
        >Review changes</s-button
      ><s-button
        v-else
        variant="primary"
        :disabled.prop="busy"
        :loading.prop="busy"
        @click="save"
        >Confirm new version</s-button
      ></template
    ></Modal
  >
</template>
