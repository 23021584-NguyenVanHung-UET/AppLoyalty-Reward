import { computed } from "vue";
import { useRoute, useRouter, type LocationQueryRaw } from "vue-router";
import type { Query } from "../api/types";

export function useQuery() {
  const route = useRoute();
  const router = useRouter();
  const value = (name: string, fallback = ""): string => {
    const item = route.query[name];
    return typeof item === "string" ? item : fallback;
  };
  const page = computed(() =>
    Math.max(1, Number.parseInt(value("page", "1"), 10) || 1),
  );
  async function update(changes: Query, resetPage = true) {
    const next: LocationQueryRaw = {
      ...route.query,
      ...(resetPage ? { page: undefined } : {}),
    };
    for (const [key, item] of Object.entries(changes))
      next[key] =
        item === "" || item === null || item === undefined
          ? undefined
          : String(item);
    await router.push({ path: route.path, query: next });
  }
  const signature = computed(() => route.fullPath);
  return { value, page, update, signature, route };
}
