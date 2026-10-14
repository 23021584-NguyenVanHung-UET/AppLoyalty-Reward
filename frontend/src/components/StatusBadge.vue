<script setup lang="ts">
import { computed } from "vue";
import { label } from "../lib/format";
const props = defineProps<{ status: string }>();
const tone = computed(() =>
  ["FAILED", "BLOCKED", "IN_DEBT", "REVOKE"].includes(props.status)
    ? "critical"
    : ["ISSUED", "PROCESSED", "AVAILABLE", "ACTIVE", "EARN"].includes(
          props.status,
        )
      ? "success"
      : [
            "PENDING",
            "PENDING_CODE",
            "PROCESSING",
            "RECEIVED",
            "SKIPPED",
          ].includes(props.status)
        ? "warning"
        : "neutral",
);
</script>
<template>
  <s-badge :tone="tone">{{ label(status) }}</s-badge>
</template>
