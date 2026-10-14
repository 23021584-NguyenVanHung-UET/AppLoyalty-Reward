<script setup lang="ts">
import {apiRequest} from '../api/client';
import type {Page} from '../api/types';
import {useQuery} from '../composables/query';
import {useResource} from '../composables/resource';
import SelectField from '../components/SelectField.vue';
import LoadState from '../components/LoadState.vue';
import DataTable from '../components/DataTable.vue';
import DateText from '../components/DateText.vue';
type Run={id:number;status:string;options:{since:string;until:string;activate_now:boolean;expiry_mode:string};counts:{scanned?:number;eligible?:number;duplicates?:number;points?:number;customers?:number;skipped?:Record<string,number>};last_order_id:number;last_error:string;created_at:string;finished_at:string|null};
const query=useQuery();
const runs=useResource(signal=>apiRequest<Page<Run>>('import-runs',{signal,query:{page:query.page.value,status:query.value('status')}}),[query.signature]);
</script>
<template>
  <s-page heading="Import history" inlineSize="large"><s-section>
    <p>Historical imports start from the backend command line. This screen shows saved runs; dry runs do not create history or points.</p>
    <SelectField :model-value="query.value('status')" label="Status" :options="[{value:'',label:'All'},...['RUNNING','COMPLETED','FAILED','LIMITED'].map(value=>({value,label:value}))]" @update:model-value="query.update({status:$event})"/>
    <s-button :disabled.prop="runs.loading.value" @click="runs.reload">Refresh</s-button>
    <LoadState :loading="runs.loading.value" :error="runs.error.value" @retry="runs.reload"><DataTable :page="runs.data.value" :headers="['Run','Status','Period (UTC)','Counts','Last order','Created','Issue']" @page="query.update({page:$event},false)">
      <tr v-for="run in runs.data.value?.results" :key="run.id"><td>#{{run.id}}</td><td>{{run.status}}</td><td>{{run.options.since}} – {{run.options.until}}<p>{{run.options.activate_now?'Available immediately':'Keep pending'}} · expiry {{run.options.expiry_mode}}</p></td><td>Scanned {{run.counts.scanned??0}} · eligible {{run.counts.eligible??0}} · duplicate {{run.counts.duplicates??0}}<p>{{run.counts.points??0}} points · {{run.counts.customers??0}} customers</p><p v-for="(count,reason) in run.counts.skipped" :key="reason">{{reason}}: {{count}}</p></td><td>{{run.last_order_id||'—'}}</td><td><DateText :value="run.created_at"/></td><td>{{run.last_error||'—'}}</td></tr>
    </DataTable></LoadState>
  </s-section></s-page>
</template>
