<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";
const props = defineProps<{ open: boolean; title: string; busy?: boolean }>();
const emit = defineEmits<{ close: [] }>();
const element = ref<HTMLDialogElement | null>(null);
async function sync() {
  await nextTick();
  if (props.open && !element.value?.open) element.value?.showModal();
  else if (!props.open && element.value?.open) element.value?.close();
}
watch(() => props.open, sync);
onMounted(sync);
</script>
<template>
  <dialog
    ref="element"
    class="modal"
    :aria-label="title"
    @cancel.prevent="!busy && emit('close')"
  >
    <div class="modal-heading">
      <h2>{{ title }}</h2>
      <button
        type="button"
        aria-label="Close dialog"
        :disabled="busy"
        @click="emit('close')"
      >
        ×
      </button>
    </div>
    <div class="modal-body"><slot /></div>
    <footer class="modal-footer"><slot name="actions" /></footer>
  </dialog>
</template>
