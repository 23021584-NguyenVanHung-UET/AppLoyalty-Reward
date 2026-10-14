<script setup lang="ts">
defineProps<{
  modelValue: string;
  label: string;
  options: { value: string; label: string }[];
  error?: string;
  disabled?: boolean;
  help?: string;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
</script>
<template>
  <s-select
    :label="label"
    :value.prop="modelValue"
    :error="error || ''"
    :details="help || ''"
    :disabled.prop="!!disabled"
    @change="
      emit(
        'update:modelValue',
        ($event.currentTarget as HTMLElement & { value: string }).value,
      )
    "
    ><s-option
      v-for="option in options"
      :key="option.value"
      :value="option.value"
      >{{ option.label }}</s-option
    ></s-select
  >
</template>
