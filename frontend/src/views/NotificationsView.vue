<script setup lang="ts">
import {ref,shallowRef,watch} from 'vue';
import {apiRequest,asApiError,type ApiError} from '../api/client';
import type {Page} from '../api/types';
import {useResource} from '../composables/resource';
import {useQuery} from '../composables/query';
import {notify} from '../composables/app';
import Field from '../components/Field.vue';
import CheckField from '../components/CheckField.vue';
import SelectField from '../components/SelectField.vue';
import LoadState from '../components/LoadState.vue';
import ErrorBanner from '../components/ErrorBanner.vue';
import DataTable from '../components/DataTable.vue';
type Settings={enabled_types:string[];sender_display_name:string;reply_to:string;default_language:string;admin_email?:string};
type Log={id:number;profile_id:number;type:string;status:string;skip_reason:string;attempts:number;created_at:string;sent_at:string|null;last_error:string};
const props=defineProps<{logOnly?:boolean}>();
const types=['POINTS_EARNED','POINTS_AVAILABLE','REWARD_CODE_ISSUED','POINTS_EXPIRING','TIER_UPGRADED','TIER_GRACE_REMINDER','WELCOME_POINTS','BIRTHDAY_POINTS','REFERRAL_REWARDED'];
const labels:Record<string,string>={POINTS_EARNED:'Points earned',POINTS_AVAILABLE:'Points available',REWARD_CODE_ISSUED:'Reward code issued',POINTS_EXPIRING:'Points expiring',TIER_UPGRADED:'Tier upgraded',TIER_GRACE_REMINDER:'Tier grace reminder (start and 7 days before deadline)',WELCOME_POINTS:'Welcome points',BIRTHDAY_POINTS:'Birthday points',REFERRAL_REWARDED:'Referral rewarded'};
const resource=useResource(signal=>apiRequest<Settings>('notifications/settings',{signal}));
const draft=ref<Settings>({enabled_types:[],sender_display_name:'',reply_to:'',default_language:'en'});
watch(resource.data,value=>{if(value)draft.value={...value,enabled_types:[...value.enabled_types]};});
const busy=ref(false),error=shallowRef<ApiError|null>(null),testAddress=ref('');
function toggle(type:string,value:boolean){draft.value.enabled_types=value?[...new Set([...draft.value.enabled_types,type])]:draft.value.enabled_types.filter(item=>item!==type);}
async function action(test=false){
  if(busy.value)return;busy.value=true;error.value=null;
  try{
    const {enabled_types,sender_display_name,reply_to,default_language}=draft.value;
    await apiRequest(test?'notifications/test':'notifications/settings',{method:test?'POST':'PATCH',body:test?(resource.data.value?.admin_email?{}:{email:testAddress.value}):{enabled_types,sender_display_name,reply_to,default_language}});
    notify(test?'Test email queued. Delivery is handled by the background worker.':'Notification settings saved.');
  }catch(failure){error.value=asApiError(failure);}finally{busy.value=false;}
}
const query=useQuery();
const logs=useResource(signal=>apiRequest<Page<Log>>('notifications/log',{signal,query:{page:query.page.value,status:query.value('status'),type:query.value('type')}}),[query.signature]);
</script>
<template>
  <s-page :heading="props.logOnly?'Notification log':'Notifications'" inlineSize="large">
    <template v-if="!props.logOnly">
      <LoadState :loading="resource.loading.value" :error="resource.error.value" @retry="resource.reload"><s-section v-if="resource.data.value" heading="Email settings">
        <p class="muted">Changes are audited. New shops start with emails disabled. Earned points and reward codes are transactional; other types require marketing consent and respect unsubscribe.</p>
        <div class="form-stack"><CheckField v-for="type in types" :key="type" :model-value="draft.enabled_types.includes(type)" :label="labels[type]??type" :disabled="busy" @update:model-value="toggle(type,$event)"/>
        <Field v-model="draft.sender_display_name" label="Sender display name" help="Leave blank to use the shop name." :disabled="busy"/>
        <Field v-model="draft.reply_to" label="Reply-to email" type="email" :disabled="busy"/>
        <SelectField v-model="draft.default_language" label="Shop default email language" :options="[{value:'en',label:'English'},{value:'vi',label:'Tiếng Việt'}]" :disabled="busy"/>
        <s-button variant="primary" :disabled.prop="busy" @click="action()">Save settings</s-button></div>
      </s-section></LoadState>
      <s-section heading="Send me a test email">
        <p v-if="resource.data.value?.admin_email">Test delivery uses your signed-in admin email.</p>
        <Field v-else v-model="testAddress" label="Your email address" type="email" help="Your Shopify session does not provide an admin email. Enter your own address." :disabled="busy"/>
        <p class="muted">Uses saved sender and language settings. Limited to one test per shop per minute.</p>
        <s-button :disabled.prop="busy||!resource.data.value" @click="action(true)">Send me a test email</s-button>
      </s-section><ErrorBanner :error="error"/>
    </template>
    <s-section heading="Notification log">
      <div class="filters"><SelectField :model-value="query.value('status')" label="Status" :options="[{value:'',label:'All'},...['QUEUED','SENT','FAILED','SKIPPED'].map(value=>({value,label:value}))]" @update:model-value="query.update({status:$event})"/>
      <SelectField :model-value="query.value('type')" label="Type" :options="[{value:'',label:'All'},...types.map(value=>({value,label:labels[value]??value}))]" @update:model-value="query.update({type:$event})"/></div>
      <p class="muted">No email addresses, codes or message bodies are stored here. SENT means the mail backend accepted the message.</p>
      <LoadState :loading="logs.loading.value" :error="logs.error.value" @retry="logs.reload"><DataTable :headers="['Customer','Type','Status','Attempts','Reason','Created']" :page="logs.data.value" @page="query.update({page:$event},false)">
        <tr v-for="row in logs.data.value?.results" :key="row.id"><td><RouterLink :to="`/customers/${row.profile_id}`">#{{row.profile_id}}</RouterLink></td><td>{{labels[row.type]??row.type}}</td><td>{{row.status}}</td><td>{{row.attempts}}</td><td>{{row.skip_reason||row.last_error||'—'}}</td><td>{{new Date(row.created_at).toLocaleString()}}</td></tr>
      </DataTable></LoadState>
    </s-section>
  </s-page>
</template>
