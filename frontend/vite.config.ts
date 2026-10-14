import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  base: "/app-static/",
  plugins: [
    vue({
      template: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("s-") },
      },
    }),
  ],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: {
    manifest: true,
    sourcemap: false,
    rollupOptions: { input: "src/main.ts" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    clearMocks: true,
    restoreMocks: true,
  },
});
