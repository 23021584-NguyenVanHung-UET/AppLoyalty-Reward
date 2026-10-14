<script setup lang="ts">
defineProps<{ value: unknown }>();
</script>
<template>
  <pre class="json-viewer" tabindex="0" aria-label="Read-only JSON">{{
    JSON.stringify(value, null, 2)
  }}</pre>
</template>
