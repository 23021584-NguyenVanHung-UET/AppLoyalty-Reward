<script setup lang="ts">
import { apiRequest } from "../api/client";
import type { Customer, Page } from "../api/types";
import { useQuery } from "../composables/query";
import { useResource } from "../composables/resource";
import Field from "../components/Field.vue";
import SelectField from "../components/SelectField.vue";
import LoadState from "../components/LoadState.vue";
import DataTable from "../components/DataTable.vue";
import StatusBadge from "../components/StatusBadge.vue";
import PointsText from "../components/PointsText.vue";
import DateText from "../components/DateText.vue";
const query = useQuery();
const customers = useResource(
  (signal) =>
    apiRequest<Page<Customer>>("customers", {
      signal,
      query: {
        page: query.page.value,
        search: query.value("search"),
        has_balance: query.value("has_balance"),
        in_debt: query.value("in_debt"),
        blocked: query.value("blocked"),
        ordering: query.value("ordering", "-created"),
      },
    }),
  [query.signature],
);
const booleans = [
  { value: "", label: "All" },
  { value: "true", label: "Yes" },
  { value: "false", label: "No" },
];
const ordering = [
  { value: "-created", label: "Newest first" },
  { value: "created", label: "Oldest first" },
  { value: "-balance", label: "Available: high to low" },
  { value: "balance", label: "Available: low to high" },
  { value: "-lifetime", label: "Lifetime: high to low" },
  { value: "lifetime", label: "Lifetime: low to high" },
];
function name(customer: Customer) {
  return (
    [customer.first_name, customer.last_name].filter(Boolean).join(" ") ||
    customer.email ||
    `Customer ${customer.shopify_customer_id}`
  );
}
</script>
<template>
  <s-page heading="Customers" inlineSize="large"
    ><s-section>
      <div class="toolbar">
        <Field
          :model-value="query.value('search')"
          @update:model-value="query.update({ search: $event })"
          label="Search customers"
          help="Name, email or Shopify customer ID"
        /><SelectField
          :model-value="query.value('has_balance')"
          @update:model-value="query.update({ has_balance: $event })"
          label="Has a balance"
          :options="booleans"
        /><SelectField
          :model-value="query.value('in_debt')"
          @update:model-value="query.update({ in_debt: $event })"
          label="In debt"
          :options="booleans"
        /><SelectField
          :model-value="query.value('blocked')"
          @update:model-value="query.update({ blocked: $event })"
          label="Blocked"
          :options="booleans"
        /><SelectField
          :model-value="query.value('ordering', '-created')"
          @update:model-value="query.update({ ordering: $event })"
          label="Sort by"
          :options="ordering"
        />
      </div>
      <LoadState
        :loading="customers.loading.value"
        :error="customers.error.value"
        @retry="customers.reload"
        ><DataTable
          :headers="[
            'Customer',
            'Shopify ID',
            'Available',
            'Pending',
            'Lifetime earned',
            'Status',
            'Created',
          ]"
          :page="customers.data.value"
          empty="No customers match these filters. New customers appear after their Shopify events are processed."
          @page="query.update({ page: $event }, false)"
        >
          <tr
            v-for="customer in customers.data.value?.results"
            :key="customer.id"
            class="clickable"
            @click="$router.push(`/customers/${customer.id}`)"
          >
            <td>
              <RouterLink :to="`/customers/${customer.id}`" @click.stop>{{
                name(customer)
              }}</RouterLink>
              <div v-if="customer.email" class="muted">
                {{ customer.email }}
              </div>
            </td>
            <td>{{ customer.shopify_customer_id }}</td>
            <td><PointsText :value="customer.available_balance" /></td>
            <td><PointsText :value="customer.pending_balance" /></td>
            <td><PointsText :value="customer.lifetime_earned" /></td>
            <td>
              <div class="badges">
                <s-badge v-if="customer.tier">{{customer.tier.name}}</s-badge>
                <StatusBadge
                  v-if="customer.is_blocked"
                  status="BLOCKED"
                /><StatusBadge
                  v-if="customer.available_balance < 0"
                  status="IN_DEBT"
                /><StatusBadge
                  v-if="customer.is_anonymized"
                  status="ANONYMIZED"
                /><StatusBadge
                  v-if="
                    !customer.is_blocked &&
                    !customer.is_anonymized &&
                    customer.available_balance >= 0
                  "
                  status="ACTIVE"
                />
              </div>
            </td>
            <td><DateText :value="customer.created_at" /></td>
          </tr> </DataTable
      ></LoadState> </s-section
  ></s-page>
</template>
