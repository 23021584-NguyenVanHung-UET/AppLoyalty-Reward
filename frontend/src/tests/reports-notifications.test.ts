import {beforeEach,describe,expect,it,vi} from 'vitest';
import {mount,flushPromises} from '@vue/test-utils';
import {createMemoryHistory,createRouter} from 'vue-router';
import {apiRequest,downloadCSV} from '../api/client';
import ReportsView from '../views/ReportsView.vue';
import NotificationsView from '../views/NotificationsView.vue';
import {button,field,page} from './helpers';
vi.mock('../api/client',async original=>({...await original<typeof import('../api/client')>(),apiRequest:vi.fn(),downloadCSV:vi.fn()}));
const request=vi.mocked(apiRequest),download=vi.mocked(downloadCSV);
beforeEach(()=>{request.mockReset();download.mockReset();});
async function open(component:typeof ReportsView|typeof NotificationsView){
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:pathMatch(.*)*',component}]});
  await router.push('/');await router.isReady();const wrapper=mount(component,{global:{plugins:[router]}});await flushPromises();return wrapper;
}
function reports(){request.mockImplementation(async path=>{
  if(path.endsWith('points-summary'))return {totals:{earned:10,revoked:2,redeemed:3,expired:1,adjusted:0,net:4},rows:[{period:'2026-10-13T00:00:00Z',net:4}]};
  if(path.endsWith('liability'))return {available:10,pending:4,outstanding:14,debt:0,expiring_7:0,expiring_30:10,expiring_60:14};
  if(path.endsWith('redemptions'))return {issued:2,used:1,conversion_rate:.5,rows:[]};
  if(path.endsWith('top-customers'))return {lifetime:[],period:[]};
  return {exists:false,rows:[]};
});}
describe('reports and notification screens',()=>{
  it('lets the merchant enable the tier grace reminder type',async()=>{
    request.mockImplementation(async path=>path==='notifications/settings'?{enabled_types:[],sender_display_name:'',reply_to:'',default_language:'en',admin_email:''}:page([]));
    const wrapper=await open(NotificationsView);
    const checkbox=wrapper.findAll('s-checkbox').find(node=>node.attributes('label')?.startsWith('Tier grace reminder'))!;
    expect(checkbox.exists()).toBe(true);
    Object.assign(checkbox.element,{checked:true});await checkbox.trigger('change');
    await button(wrapper,'Save settings').trigger('click');await flushPromises();
    expect(request.mock.calls.find(([path,options])=>path==='notifications/settings'&&options?.method==='PATCH')?.[1]?.body).toMatchObject({enabled_types:['TIER_GRACE_REMINDER']});
    wrapper.unmount();
  });
  it('loads SQL report endpoints and renders the inline chart',async()=>{
    reports();const wrapper=await open(ReportsView);
    expect(request).toHaveBeenCalledTimes(5);expect(wrapper.find('svg[role="img"]').exists()).toBe(true);
    expect(wrapper.text()).toContain('50.0%');
    await wrapper.find('input[type="date"]').setValue('2026-10-01');await wrapper.find('form').trigger('submit');await flushPromises();
    expect(request.mock.calls.at(-5)?.[1]?.query?.from).toBe('2026-10-01');wrapper.unmount();
  });
  it('requires confirmation before a PII export and surfaces cap errors',async()=>{
    reports();download.mockRejectedValue(new Error('failure'));
    const wrapper=await open(ReportsView);
    const checkbox=wrapper.find('s-checkbox');Object.assign(checkbox.element,{checked:true});await checkbox.trigger('change');
    await button(wrapper,'Export customers').trigger('click');await flushPromises();
    expect(download).not.toHaveBeenCalled();expect(wrapper.text()).toContain('Export personal information?');
    await button(wrapper,'Export with personal information').trigger('click');await flushPromises();
    expect(download.mock.calls[0]?.[0]).toBe('customers');expect(download.mock.calls[0]?.[1]?.include_pii).toBe('1');wrapper.unmount();
  });
  it('saves toggles without leaking admin email into settings payload and uses typed fallback for test',async()=>{
    request.mockImplementation(async path=>path==='notifications/settings'?{enabled_types:[],sender_display_name:'',reply_to:'',default_language:'vi',admin_email:''}:page([]));
    const wrapper=await open(NotificationsView);
    const checkbox=wrapper.find('s-checkbox');Object.assign(checkbox.element,{checked:true});await checkbox.trigger('change');
    await button(wrapper,'Save settings').trigger('click');await flushPromises();
    expect(request.mock.calls.find(([path,o])=>path==='notifications/settings'&&o?.method==='PATCH')?.[1]?.body).toEqual({enabled_types:['POINTS_EARNED'],sender_display_name:'',reply_to:'',default_language:'vi'});
    await field(wrapper,'Your email address','admin@example.com');
    await button(wrapper,'Send me a test email').trigger('click');await flushPromises();
    expect(request.mock.calls.find(([path])=>path==='notifications/test')?.[1]?.body).toEqual({email:'admin@example.com'});wrapper.unmount();
  });
  it('uses signed-in admin email when available and filters notification logs',async()=>{
    request.mockImplementation(async path=>path==='notifications/settings'?{enabled_types:[],sender_display_name:'',reply_to:'',default_language:'en',admin_email:'trusted@example.com'}:page([]));
    const wrapper=await open(NotificationsView);
    expect(wrapper.text()).not.toContain('Your Shopify session does not provide');
    await button(wrapper,'Send me a test email').trigger('click');await flushPromises();
    expect(request.mock.calls.find(([path])=>path==='notifications/test')?.[1]?.body).toEqual({});
    const select=wrapper.findAll('s-select').find(node=>node.attributes('label')==='Status')!;
    Object.assign(select.element,{value:'FAILED'});await select.trigger('change');await flushPromises();
    expect(request.mock.calls.at(-1)?.[1]?.query?.status).toBe('FAILED');wrapper.unmount();
  });
});
