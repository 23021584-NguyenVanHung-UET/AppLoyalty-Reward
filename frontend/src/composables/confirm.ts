import { reactive } from "vue";
import { asApiError, type ApiError } from "../api/client";

export function useConfirmAction() {
  const state = reactive({
    open: false,
    title: "",
    message: "",
    label: "Confirm",
    danger: true,
    busy: false,
    error: null as ApiError | null,
  });
  let task: (() => Promise<void>) | undefined;
  function ask(
    title: string,
    message: string,
    action: () => Promise<void>,
    label = "Confirm",
    danger = true,
  ) {
    if (state.busy) return;
    Object.assign(state, {
      open: true,
      title,
      message,
      label,
      danger,
      error: null,
    });
    task = action;
  }
  async function execute() {
    if (state.busy || !state.open || !task) return;
    state.busy = true;
    state.error = null;
    try {
      await task();
      state.open = false;
    } catch (error) {
      state.error = asApiError(error);
    } finally {
      state.busy = false;
    }
  }
  function cancel() {
    if (!state.busy) {
      state.open = false;
      task = undefined;
    }
  }
  return { state, ask, execute, cancel };
}
