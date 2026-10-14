import { createRouter, createWebHistory } from "vue-router";

export function createAppRouter(
  base = import.meta.env.DEV ? "/app-static/" : "/app/",
) {
  const router = createRouter({
    history: createWebHistory(base),
    routes: [
      { path: "/", component: () => import("./views/DashboardView.vue") },
      {
        path: "/customers",
        component: () => import("./views/CustomersView.vue"),
      },
      {
        path: "/customers/:id",
        component: () => import("./views/CustomerView.vue"),
      },
      { path: "/rewards", component: () => import("./views/RewardsView.vue") },
      ...["earn-rules", "campaigns", "tiers"].map(kind => ({ path: `/${kind}`, component: () => import("./views/EarningView.vue"), props: { kind } })),
      {
        path: "/redemptions",
        component: () => import("./views/RedemptionsView.vue"),
      },
      { path: "/settings", component: () => import("./views/SettingsView.vue") },
      { path: "/webhooks", component: () => import("./views/WebhooksView.vue") },
      { path: "/health", component: () => import("./views/HealthView.vue") },
      { path: "/reports", component: () => import("./views/ReportsView.vue") },
      { path: "/imports", component: () => import("./views/ImportsView.vue") },
      { path: "/referrals", component: () => import("./views/ReferralsView.vue") },
      { path: "/notifications", component: () => import("./views/NotificationsView.vue") },
      { path: "/notification-log", component: () => import("./views/NotificationsView.vue"), props: {logOnly:true} },
      {
        path: "/:pathMatch(.*)*",
        component: () => import("./views/NotFoundView.vue"),
      },
    ],
    scrollBehavior: () => ({ top: 0 }),
  });

  // Keep previously copied STEP 9 hash URLs working, including their filters
  // and Shopify's existing launch context. New links use ordinary pathnames.
  router.beforeEach((to) => {
    if (
      to.path === "/" &&
      to.hash.startsWith("#/") &&
      !to.hash.startsWith("#//")
    ) {
      const legacy = router.resolve(to.hash.slice(1));
      return {
        path: legacy.path,
        query: { ...to.query, ...legacy.query },
        hash: legacy.hash,
        replace: true,
      };
    }
  });
  return router;
}
