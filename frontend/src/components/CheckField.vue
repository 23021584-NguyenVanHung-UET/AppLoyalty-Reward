<script setup lang="ts">
defineProps<{
  modelValue: boolean;
  label: string;
  help?: string;
  disabled?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();
</script>
<template>
  <div>
    <s-checkbox
      :label="label"
      :checked.prop="modelValue"
      :disabled.prop="!!disabled"
      @change="
        emit(
          'update:modelValue',
          ($event.currentTarget as HTMLElement & { checked: boolean }).checked,
        )
      "
    />
    <p v-if="help" class="muted field-help">{{ help }}</p>
  </div>
</template>
