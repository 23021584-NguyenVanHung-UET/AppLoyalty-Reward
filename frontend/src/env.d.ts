/// <reference types="vite/client" />
import type { ShopifyGlobal } from "@shopify/app-bridge-types";

declare global {
  interface Window {
    shopify?: ShopifyGlobal;
  }
}

export {};
