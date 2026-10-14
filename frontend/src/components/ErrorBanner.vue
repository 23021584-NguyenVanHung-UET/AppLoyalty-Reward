<script setup lang="ts">
import type { ApiError } from "../api/client";
defineProps<{ error: ApiError | null }>();
</script>
<template>
  <s-banner
    v-if="error"
    heading="Unable to complete this request"
    tone="critical"
    ><p>{{ error.message }}</p>
    <p>
      <strong>{{ error.code }}</strong
      ><span v-if="error.status === 429"> — Wait a moment before retrying.</span
      ><span v-if="error.status >= 500">
        — The service may be temporarily unavailable.</span
      >
    </p>
    <slot
  /></s-banner>
</template>
