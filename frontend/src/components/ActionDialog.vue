<script setup lang="ts">
import type { useConfirmAction } from "../composables/confirm";
import ConfirmDialog from "./ConfirmDialog.vue";
import ErrorBanner from "./ErrorBanner.vue";
defineProps<{ controller: ReturnType<typeof useConfirmAction> }>();
</script>
<template>
  <ConfirmDialog
    :open="controller.state.open"
    :title="controller.state.title"
    :message="controller.state.message"
    :confirm-label="controller.state.label"
    :danger="controller.state.danger"
    :busy="controller.state.busy"
    @confirm="controller.execute"
    @cancel="controller.cancel"
    ><ErrorBanner :error="controller.state.error"
  /></ConfirmDialog>
</template>
