import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest';
import {mount,flushPromises} from '@vue/test-utils';
import {createRouter,createMemoryHistory} from 'vue-router';
import CustomerView from '../views/CustomerView.vue';
import ImportsView from '../views/ImportsView.vue';
import {apiRequest,downloadCustomerData} from '../api/client';
import {customer,button,field,page} from './helpers';
vi.mock('../api/client',async original=>({...await original<typeof import('../api/client')>(),apiRequest:vi.fn(),downloadCustomerData:vi.fn()}));
const request=vi.mocked(apiRequest),download=vi.mocked(downloadCustomerData);
beforeEach(()=>{request.mockReset();download.mockReset();});
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();});
async function open(component:typeof CustomerView|typeof ImportsView,path:string){
  const router=createRouter({history:createMemoryHistory(),routes:[{path:'/:pathMatch(.*)*',component}]});
  // CustomerView reads its numeric route param.
  router.addRoute({path:'/customers/:id',component:CustomerView});
  await router.push(path);await router.isReady();
  const wrapper=mount(component,{global:{plugins:[router]}});await flushPromises();return wrapper;
}
describe('privacy and historical imports',()=>{
  it('requires an irreversible confirmation before anonymizing and disables further attempts',async()=>{
    request.mockImplementation(async(_path,options)=>options?.method==='POST'?{...customer,is_anonymized:true}:customer);
    const wrapper=await open(CustomerView,'/customers/1');
    await button(wrapper,'Anonymize customer').trigger('click');await flushPromises();
    expect(wrapper.text()).toContain('irreversibly clears personal information');
    expect(request.mock.calls.some(([,options])=>options?.method==='POST')).toBe(false);
    request.mockResolvedValue({...customer,is_anonymized:true});
    const confirm=wrapper.findAll('s-button').filter(node=>node.text()==='Anonymize customer').at(-1)!;
    await confirm.trigger('click');await flushPromises();
    expect(request).toHaveBeenCalledWith('customers/1/anonymize',{method:'POST',body:{confirm:true}});
    expect((button(wrapper,'Anonymize customer').element as HTMLButtonElement).disabled).toBe(true);wrapper.unmount();
  });
  it('downloads JSON through the authenticated helper only after a click',async()=>{
    request.mockResolvedValue(customer);download.mockResolvedValue(new Blob(['{}'],{type:'application/json'}));
    vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:test'),revokeObjectURL:vi.fn()}));
    vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
    const wrapper=await open(CustomerView,'/customers/1');expect(download).not.toHaveBeenCalled();
    await button(wrapper,'Export data').trigger('click');await flushPromises();
    expect(download).toHaveBeenCalledExactlyOnceWith(1);wrapper.unmount();
  });
  it('filters import history and offers no action that starts an import',async()=>{
    request.mockResolvedValue(page([{id:7,status:'FAILED',options:{since:'2026-10-01',until:'2026-10-13',activate_now:true,expiry_mode:'from_now'},counts:{scanned:2,eligible:1,duplicates:0,points:10,customers:1,skipped:{test_order:1}},last_order_id:1001,last_error:'IMPORT_FAILED',created_at:'2026-10-13T00:00:00Z'}]));
    const wrapper=await open(ImportsView,'/imports');expect(wrapper.text()).toContain('test_order: 1');
    await field(wrapper,'Status','FAILED');await flushPromises();
    expect(request).toHaveBeenLastCalledWith('import-runs',expect.objectContaining({query:expect.objectContaining({status:'FAILED'})}));
    expect(wrapper.findAll('s-button').some(node=>/start import/i.test(node.text()))).toBe(false);wrapper.unmount();
  });
});
