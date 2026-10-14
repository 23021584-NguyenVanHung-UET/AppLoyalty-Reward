import { onScopeDispose, ref, shallowRef, watch, type WatchSource } from "vue";
import { asApiError, type ApiError } from "../api/client";

export function useResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  sources: WatchSource[] = [],
  immediate = true,
) {
  const data = shallowRef<T | null>(null);
  const loading = ref(false);
  const error = shallowRef<ApiError | null>(null);
  let controller: AbortController | undefined;
  let generation = 0;
  let disposed = false;
  async function reload() {
    if (disposed) return;
    controller?.abort();
    const current = ++generation;
    const request = (controller = new AbortController());
    loading.value = true;
    data.value = null;
    error.value = null;
    try {
      const value = await loader(request.signal);
      if (current === generation) data.value = value;
    } catch (failure) {
      if (!request.signal.aborted && current === generation)
        error.value = asApiError(failure);
    } finally {
      if (current === generation) loading.value = false;
    }
  }
  watch(
    sources,
    () => {
      void reload();
    },
    { immediate },
  );
  onScopeDispose(() => {
    disposed = true;
    generation++;
    controller?.abort();
  });
  return { data, loading, error, reload };
}
