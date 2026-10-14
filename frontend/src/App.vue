<script setup lang="ts">
import { onMounted, ref } from "vue";
import { RouterLink, RouterView, useRouter } from "vue-router";
import { isEmbedded } from "./api/client";
import { useResource } from "./composables/resource";
import { useApp } from "./composables/app";
import ErrorBanner from "./components/ErrorBanner.vue";
const { me, toast, load } = useApp();
const router = useRouter();
const account = useResource((signal) => load(signal));
const embedded = isEmbedded(),
  appNav = ref(false);
const links = [
  ["/", "Dashboard"],
  ["/customers", "Customers"],
  ["/rewards", "Rewards"],
  ["/earn-rules", "Earning rules"],
  ["/campaigns", "Campaigns"],
  ["/tiers", "Tiers"],
  ["/redemptions", "Redemptions"],
  ["/settings", "Program settings"],
  ["/webhooks", "Webhook events"],
  ["/health", "Health"],
  ["/reports", "Reports"],
  ["/imports", "Import history"],
  ["/referrals", "Referrals"],
  ["/notifications", "Notifications"],
  ["/notification-log", "Notification log"],
] as const;
const href = (path: string) => router.resolve(path).href;
onMounted(() => {
  if (embedded) {
    appNav.value = !!customElements.get("s-app-nav");
    void customElements.whenDefined("s-app-nav").then(() => {
      appNav.value = true;
    });
  }
});
</script>
<template>
  <header class="app-brand">
    <strong>Loyalty &amp; Rewards</strong
    ><span v-if="me" class="muted"
      >{{ me.shop }} · {{ me.currency }} · Program v{{
        me.program_config?.version ?? "—"
      }}</span
    >
  </header>
  <s-app-nav v-if="appNav"
    ><s-link :href="href('/')" rel="home">Dashboard</s-link
    ><s-link
      v-for="[path, title] in links.slice(1)"
      :key="path"
      :href="href(path)"
      >{{ title }}</s-link
    ></s-app-nav
  >
  <nav v-else class="app-nav" aria-label="Main navigation">
    <RouterLink
      v-for="[path, title] in links"
      :key="path"
      :to="path"
      :exact-active-class="'active'"
      >{{ title }}</RouterLink
    >
  </nav>
  <div class="global-notices">
    <s-banner v-if="!embedded" heading="Open from Shopify admin" tone="info"
      >This interface uses your Shopify admin session. Open the installed app
      from your store's admin. Local development can use a temporary
      development-only token.</s-banner
    ><ErrorBanner :error="account.error.value"
      ><s-button @click="account.reload"
        >Retry connection</s-button
      ></ErrorBanner
    >
  </div>
  <main><RouterView /></main>
  <div v-if="toast" class="toast" role="status">{{ toast }}</div>
</template>
