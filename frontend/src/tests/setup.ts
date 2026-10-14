import { afterEach, vi } from "vitest";
import { config, enableAutoUnmount } from "@vue/test-utils";

config.global.config.compilerOptions = {
  isCustomElement: (tag) => tag.startsWith("s-"),
};
enableAutoUnmount(afterEach);
// Do not load Shopify's CDN or make network calls in unit tests.
for (const tag of [
  "s-text-field",
  "s-text-area",
  "s-select",
  "s-checkbox",
  "s-button",
]) {
  customElements.define(
    tag,
    class extends HTMLElement {
      value = "";
      checked = false;
      disabled = false;
      loading = false;
    },
  );
}
HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
HTMLDialogElement.prototype.close = function () {
  this.open = false;
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
