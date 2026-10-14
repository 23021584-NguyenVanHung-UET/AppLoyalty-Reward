import '@shopify/ui-extensions/preact';
import type {Api} from '@shopify/ui-extensions/customer-account.order-status.block.render';
import {render} from 'preact';
import {useEffect,useState} from 'preact/hooks';
import {OrderSummary} from '../../../customer-shared/OrderSummary';
declare const shopify:Api;
function OrderStatus() {
  const [order,setOrder]=useState(shopify.order.value);
  useEffect(()=>shopify.order.subscribe(setOrder),[]);
  return <OrderSummary api={shopify} orderGid={order?.id} rewardsHref="extension:loyalty-account/"/>;
}
export default ()=>render(<OrderStatus/>,document.body);
