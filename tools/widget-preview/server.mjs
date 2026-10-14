// Local design tooling only. No Shopify calls, credentials or npm dependencies.
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {fixture,rejectionCodes,scenarios} from './scenarios.mjs';

const root=fileURLToPath(new URL('../../shopify-app/extensions/loyalty-widget/',import.meta.url));
const tool=fileURLToPath(new URL('./',import.meta.url));
let scenario='has-points',data=fixture(scenario), attempts=new Map(), pendingReads=0;
const json=(res,status,value)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
const readBody=async req=>{let body='';for await(const chunk of req){body+=chunk;if(body.length>8000)throw new Error('Too large');}return JSON.parse(body||'{}');};
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  try{
    if(url.pathname==='/scenarios')return json(res,200,{scenario,scenarios});
    if(url.pathname==='/scenario'&&req.method==='POST'){
      const body=await readBody(req);if(!scenarios.includes(body.scenario))return json(res,400,{});
      scenario=body.scenario;data=fixture(scenario);attempts=new Map();pendingReads=0;return json(res,200,{});
    }
    if(url.pathname==='/frame'){
      const lang=url.searchParams.get('lang')==='vi'?'vi':'en',mode=url.searchParams.get('mode')==='inline'?'inline':'floating';
      const locale=JSON.parse(await readFile(path.join(root,'locales',lang==='vi'?'vi.json':'en.default.json'),'utf8')).lp;
      const config={mode,locale:lang,strings:locale,loggedIn:scenario!=='logged-out',designMode:url.searchParams.get('editor')==='true',previewData:fixture('near-expiry'),
        showPoints:false,position:'bottom-right',offset:24,primary:'#5b37d4',textColor:'#ffffff',radius:16,zIndex:1000,tabs:{rewards:true,codes:true,history:true},rootUrl:'/',loginUrl:'/account/login',registerUrl:'/account/register'};
      const estimate={locale:lang,strings:locale,designMode:config.designMode,currency:'USD',variantId:1,variants:[{id:1,price:4500},{id:2,price:9900}],
        appearance:{style:['card','pill','text'].includes(url.searchParams.get('estimateStyle'))?url.searchParams.get('estimateStyle'):'card',
          icon:['gift','star','none'].includes(url.searchParams.get('estimateIcon'))?url.searchParams.get('estimateIcon'):'gift',
          accent:/^#[a-f\d]{6}$/i.test(url.searchParams.get('estimateAccent'))?url.searchParams.get('estimateAccent'):'#5b37d4',showHelp:url.searchParams.get('estimateHelp')!=='false'}};
      const safe=value=>JSON.stringify(value).replaceAll('<','\\u003c');
      res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'});
      if(url.searchParams.get('surface')==='cart'){
        const cart={...config,appearance:{layout:url.searchParams.get('cartLayout')==='grid'?'grid':'list',accent:estimate.appearance.accent,limit:3,showCheckout:true}};
        return res.end(`<!doctype html><html lang="${lang}"><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>Cart offers preview</title><link rel="stylesheet" href="/preview.css"></head><body class="store"><header class="store-header"><strong>STUDIO / EVERYDAY</strong><span>Your shopping bag</span></header><main class="product"><section><p class="kicker">YOUR BAG</p><h1>A little everyday joy</h1><p class="price">USD 45.00</p><p>Choose the cart ready scenario to preview two unused codes. All data is fictional.</p></section><section><lp-cart-offers data-proxy="/proxy" data-css="/assets/loyalty-cart.css"><script type="application/json">${safe(cart)}</script></lp-cart-offers></section></main><script type="module" src="/assets/loyalty-cart.js"></script></body></html>`);
      }
      return res.end(`<!doctype html><html lang="${lang}"><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>Widget preview</title><link rel="stylesheet" href="/preview.css"></head><body class="store"><header class="store-header"><strong>STUDIO / EVERYDAY</strong><span>Objects to enjoy, every day.</span></header><main class="product"><div class="product-art">A LITTLE<br>EVERYDAY JOY<span>the everyday collection</span></div><section><p class="kicker">THOUGHTFULLY MADE</p><h1>Your new everyday favorite</h1><p class="price">USD 45.00</p><p>A small detail that makes a lovely difference. Our local preview brings your rewards into a familiar shopping experience.</p><form action="/cart/add"><label>Size<select name="id"><option value="1">Original — USD 45.00</option><option value="2">Large — USD 99.00</option></select></label></form><lp-estimate data-proxy="/proxy" data-css="/assets/loyalty-widget.css"><script type="application/json">${safe(estimate)}</script></lp-estimate><button class="cart">Add to bag</button><lp-loyalty data-proxy="/proxy" data-css="/assets/loyalty-widget.css"><script type="application/json">${safe(config)}</script></lp-loyalty></section></main><script type="module" src="/assets/loyalty-widget.js"></script></body></html>`);
    }
    if(url.pathname.startsWith('/proxy/')){
      if(scenario==='slow-network')await new Promise(resolve=>setTimeout(resolve,2500));
      if(scenario==='500')return json(res,500,{code:'INTERNAL_ERROR'});
      const endpoint=url.pathname.split('/').at(-1);
      if(endpoint==='program')return json(res,200,data.program);
      if(scenario==='logged-out')return json(res,401,{code:'NOT_LOGGED_IN'});
      if(endpoint==='me')return json(res,200,data.me);
      if(endpoint==='referral')return json(res,200,data.referral??{code:null,share_link:null,counts:{pending:0,rewarded:0,rejected:0},points_earned:0});
      if(url.pathname==='/proxy/referral/code')return json(res,200,data.referral??{code:'H7K9M2NP',share_link:'https://preview-shop.myshopify.com/?ref=H7K9M2NP',counts:{pending:0,rewarded:0,rejected:0},points_earned:0});
      if(url.pathname==='/proxy/profile/birthday'){
        const body=await readBody(req);data.me.birthday={month:body.month,day:body.day,editable:false,editable_at:new Date(Date.now()+366*86400000).toISOString()};return json(res,200,data.me.birthday);
      }
      if(endpoint==='rewards')return json(res,200,{results:data.rewards});
      if(endpoint==='redemptions'){
        if(scenario==='redeem-pending'&&attempts.size&&++pendingReads>=3){for(const row of data.codes)row.status='ISSUED';}
        return json(res,200,{results:data.codes.map(row=>row.status==='ISSUED'?row:(({code,...safe})=>safe)(row))});
      }
      if(endpoint==='redeem'&&req.method==='POST'){
        const body=await readBody(req);
        if(scenario==='429')return json(res,429,{code:'RATE_LIMITED'});
        if(rejectionCodes.includes(scenario))return json(res,scenario==='UNSUPPORTED_REWARD'?422:409,{code:scenario});
        let row=attempts.get(body.client_key);
        if(!row){
          const reward=data.rewards.find(row=>row.id===body.reward_id);if(!reward)return json(res,422,{code:'INVALID_ID'});
          row={reference:`local-${attempts.size+1}`,reward_id:reward.id,points_spent:reward.points_cost,status:['redeem-pending','pending-timeout'].includes(scenario)?'PENDING_CODE':'ISSUED',code:'PREVIEW-ONLY',created_at:new Date().toISOString(),expires_at:new Date(Date.now()+30*86400000).toISOString()};
          attempts.set(body.client_key,row);data.codes.unshift(row);data.me.available-=reward.points_cost;
          data.rewards.forEach(item=>item.affordable=data.me.available>=item.points_cost);
        }
        return json(res,row.status==='ISSUED'?200:202,row.status==='ISSUED'?{status:'ISSUED',code:row.code,expires_at:row.expires_at}:{status:'PENDING',poll:'redemptions'});
      }
      return json(res,404,{code:'NOT_FOUND'});
    }
    if(url.pathname==='/cart.js')return json(res,200,{item_count:2});
    const allowed={'/':'index.html','/preview.js':'preview.js','/preview.css':'preview.css'};
    const asset=/^\/assets\/(loyalty-core\.js|loyalty-(?:widget|cart)\.(?:js|css))$/.exec(url.pathname);
    const filename=asset?path.join(root,'assets',asset[1]):allowed[url.pathname]?path.join(tool,allowed[url.pathname]):null;
    if(!filename){res.writeHead(404);return res.end('Not found');}
    const contents=await readFile(filename);res.writeHead(200,{'Content-Type':filename.endsWith('.css')?'text/css':filename.endsWith('.js')?'text/javascript':'text/html; charset=utf-8','Cache-Control':'no-store'});res.end(contents);
  }catch{json(res,400,{code:'INVALID_INPUT'});}
});
const port=Number(process.env.WIDGET_PREVIEW_PORT||4174);
server.listen(port,'127.0.0.1',()=>process.stdout.write(`Widget preview: http://127.0.0.1:${server.address().port}\n`));
