<script setup lang="ts">
import { computed } from "vue";
import { useApp } from "../composables/app";
import { money } from "../lib/format";
defineProps<{ value: string | null }>();
const { me } = useApp();
const currency = computed(() => me.value?.currency ?? "USD");
</script>
<template>
  <span class="numeric">{{ money(value, currency) }}</span>
</template>
