<script setup lang="ts">
import type { Page } from "../api/types";
defineProps<{
  headers: string[];
  page: Page<unknown> | null;
  empty?: string;
  busy?: boolean;
}>();
defineEmits<{ page: [value: number] }>();
</script>
<template>
  <template v-if="page?.results.length">
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th v-for="header in headers" :key="header" scope="col">
              {{ header }}
            </th>
          </tr>
        </thead>
        <tbody>
          <slot />
        </tbody>
      </table>
    </div>
    <div class="pagination" aria-label="Pagination">
      <p class="muted">
        {{ page.count.toLocaleString("en-US") }} results · Page
        {{ page.page }} of {{ page.pages }}
      </p>
      <div class="actions">
        <s-button
          :disabled.prop="page.page <= 1 || !!busy"
          @click="$emit('page', page.page - 1)"
          >Previous</s-button
        ><s-button
          :disabled.prop="page.page >= page.pages || !!busy"
          @click="$emit('page', page.page + 1)"
          >Next</s-button
        >
      </div>
    </div>
  </template>
  <div v-else class="empty-state">
    <s-heading>No results yet</s-heading>
    <p>
      {{
        empty || "Try changing your filters or check back after new activity."
      }}
    </p>
  </div>
</template>
