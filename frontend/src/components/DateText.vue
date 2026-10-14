<script setup lang="ts">
import { localDate, utcDate } from "../lib/format";
defineProps<{ value: string | null }>();
</script>
<template>
  <time :datetime="value ?? undefined" :title="utcDate(value)">{{
    localDate(value)
  }}</time>
</template>
