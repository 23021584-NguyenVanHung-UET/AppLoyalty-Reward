<script setup lang="ts">
import { points } from "../lib/format";
defineProps<{ value: number; signed?: boolean }>();
</script>
<template>
  <span
    class="numeric"
    :class="signed ? (value < 0 ? 'negative' : 'positive') : ''"
    >{{ points(value, signed) }}</span
  >
</template>
