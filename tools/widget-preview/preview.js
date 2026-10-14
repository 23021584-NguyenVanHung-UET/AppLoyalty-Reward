const byId=id=>document.getElementById(id),frame=byId('frame');
const response=await fetch('/scenarios'),data=await response.json();
data.scenarios.forEach(name=>{const option=document.createElement('option');option.value=name;option.textContent=name.replaceAll('-',' ');byId('scenario').append(option);});
byId('scenario').value=data.scenario;
const reload=()=>{const query=new URLSearchParams({surface:byId('surface').value,cartLayout:byId('cartLayout').value,lang:byId('language').value,mode:byId('mode').value,editor:byId('editor').checked,estimateStyle:byId('estimateStyle').value,estimateIcon:byId('estimateIcon').value,estimateAccent:byId('estimateAccent').value,estimateHelp:byId('estimateHelp').checked});frame.src=`/frame?${query}`;};
byId('scenario').addEventListener('change',async()=>{await fetch('/scenario',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario:byId('scenario').value})});reload();});
for(const id of ['surface','cartLayout','language','mode','editor','estimateStyle','estimateIcon','estimateAccent','estimateHelp'])byId(id).addEventListener('change',reload);
byId('viewport').addEventListener('change',()=>{frame.style.width=byId('viewport').value;});
byId('appearance').addEventListener('change',()=>{frame.style.colorScheme=byId('appearance').value;});
