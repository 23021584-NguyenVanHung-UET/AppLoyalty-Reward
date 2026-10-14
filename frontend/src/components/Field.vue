<script setup lang="ts">
withDefaults(
  defineProps<{
    modelValue: string;
    label: string;
    error?: string;
    help?: string;
    multiline?: boolean;
    disabled?: boolean;
    maxLength?: number;
  }>(),
  { error: "", help: "", disabled: false },
);
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const update = (event: Event) =>
  emit(
    "update:modelValue",
    (event.currentTarget as HTMLElement & { value: string }).value,
  );
</script>
<template>
  <s-text-area
    v-if="multiline"
    :label="label"
    :value.prop="modelValue"
    :error="error"
    :details="help"
    :disabled.prop="disabled"
    :maxLength.prop="maxLength"
    @input="update"
    @change="update"
  />
  <s-text-field
    v-else
    :label="label"
    :value.prop="modelValue"
    :error="error"
    :details="help"
    :disabled.prop="disabled"
    @input="update"
    @change="update"
  />
</template>
