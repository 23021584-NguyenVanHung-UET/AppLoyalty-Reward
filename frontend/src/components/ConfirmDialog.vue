<script setup lang="ts">
import Modal from "./Modal.vue";
withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    message: string;
    busy?: boolean;
    confirmLabel?: string;
    danger?: boolean;
  }>(),
  { confirmLabel: "Confirm", danger: true },
);
defineEmits<{ confirm: []; cancel: [] }>();
</script>
<template>
  <Modal :open="open" :title="title" :busy="busy" @close="$emit('cancel')"
    ><p>{{ message }}</p>
    <slot /><template #actions
      ><s-button :disabled.prop="!!busy" @click="$emit('cancel')"
        >Go back</s-button
      ><s-button
        :tone="danger ? 'critical' : 'auto'"
        variant="primary"
        :loading.prop="!!busy"
        :disabled.prop="!!busy"
        @click="!busy && $emit('confirm')"
        >{{ confirmLabel }}</s-button
      ></template
    ></Modal
  >
</template>
