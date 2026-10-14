<script setup lang="ts">
import type { ApiError } from "../api/client";
import ErrorBanner from "./ErrorBanner.vue";
defineProps<{ loading: boolean; error: ApiError | null }>();
defineEmits<{ retry: [] }>();
</script>
<template>
  <div v-if="loading" class="loading" role="status">
    <s-spinner accessibilityLabel="Loading" size="base" /><span>Loading…</span>
  </div>
  <ErrorBanner v-else-if="error" :error="error"
    ><s-button @click="$emit('retry')">Retry</s-button></ErrorBanner
  ><slot v-else />
</template>
