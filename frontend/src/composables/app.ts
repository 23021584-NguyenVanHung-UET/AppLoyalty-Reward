import { ref, shallowRef } from "vue";
import { apiRequest, isEmbedded } from "../api/client";
import type { Me } from "../api/types";

const me = shallowRef<Me | null>(null);
const toast = ref("");
let timer: ReturnType<typeof setTimeout> | undefined;

export function notify(message: string) {
  toast.value = message;
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    toast.value = "";
  }, 5000);
  if (isEmbedded() && window.shopify?.toast) window.shopify.toast.show(message);
}

export function useApp() {
  return {
    me,
    toast,
    load: async (signal?: AbortSignal) => {
      me.value = await apiRequest<Me>("me", { signal });
      return me.value;
    },
  };
}
