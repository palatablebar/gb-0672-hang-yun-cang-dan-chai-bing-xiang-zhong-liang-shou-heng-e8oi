const rid=document.body.dataset.runId;
const msg=document.querySelector('#detention-message');
const meta=document.querySelector('#ledger-meta');
const genList=document.querySelector('#generation-list');
const stamp=document.querySelector('#seal-stamp');

async function call(url,options){
  msg.textContent='处理中…';
  try{
    const res=await fetch(url,{headers:{'Content-Type':'application/json'},...options});
    const data=await res.json();
    if(!res.ok)throw Error(data.detail||res.status);
    return data;
  }catch(e){msg.textContent=e.message;return null;}
}

function renderLedger(data){
  const tbody=document.querySelector('#ledger-table tbody');
  tbody.innerHTML='';
  for(const e of data.entries){
    const tr=document.createElement('tr');
    tr.className='ledger-row';tr.dataset.container=e.container;
    const allocs=e.allocations.map(b=>`<div class="alloc">${b.master_bill}：<b>${b.bill_fee}</b><small>（${b.share_pieces}件）</small></div>`).join('');
    tr.innerHTML=`<td>${e.container}</td>
      <td class="${e.entry_status==='争议暂记'?'fail':'ok'}">${e.entry_status}</td>
      <td>${e.basis==='carrier'?'承运人口径':'暂不起算'}</td>
      <td class="num">${e.overdue_days??'—'}</td>
      <td class="num">${e.container_fee}</td><td>${allocs}</td>
      <td class="${e.sealable?'ok':'fail'}">${e.sealable?'可封存':'不可封存'}</td>
      <td class="fail">${e.block_codes.join(' / ')||'—'}</td>
      <td title="${e.basis_detail}">${e.basis_detail.slice(0,38)}…</td>`;
    tbody.appendChild(tr);
  }
  meta.textContent=`正式账 v${data.generation}｜正式 ${data.summary.formal} 箱｜争议 ${data.summary.disputed} 箱｜金额 ${data.summary.formal_fee}`;
  stamp.textContent=data.summary.disputed===0?'可随舱单封存':'争议待闭合';
  stamp.style.color=data.summary.disputed===0?'var(--ok)':'var(--bad)';
  genList.innerHTML=(data.evidence||[]).map(v=>`#${v.seq} ${v.kind} ${v.created_at.slice(11,19)}`).join('<br>')||'暂无证据链。';
  msg.textContent='责任账已更新';
}

document.querySelector('#open-ledger').addEventListener('click',async()=>{
  const data=await call(`/api/detention/${rid}/ledger`,{method:'POST',body:'{}'});
  if(data)renderLedger(data);
});
document.querySelector('#refresh-ledger').addEventListener('click',async()=>{
  const data=await call(`/api/detention/${rid}/ledger`);
  if(data){if(data.generation===0){msg.textContent='尚未生成正式账，请先点击生成';}else renderLedger(data);}
});
document.querySelector('#submit-receipt').addEventListener('click',async()=>{
  const payload={receipt_id:v('#rcp-id'),container:v('#rcp-box'),event:document.querySelector('#rcp-event').value,
    event_time:v('#rcp-time'),closed:document.querySelector('#rcp-closed').value,actor:'web补证'};
  const data=await call(`/api/detention/${rid}/receipts`,{method:'POST',body:JSON.stringify(payload)});
  if(data)renderLedger(data);
});
document.querySelector('#submit-share').addEventListener('click',async()=>{
  const payload={container:v('#sh-box'),master_bill:v('#sh-bill'),pieces:Number(v('#sh-pieces')),note:'web补证'};
  const data=await call(`/api/detention/${rid}/shares`,{method:'POST',body:JSON.stringify(payload)});
  if(data)renderLedger(data);
});
document.querySelector('#recompute-all').addEventListener('click',async()=>{
  const data=await call(`/api/detention/${rid}/recompute`,{method:'POST',body:'{}'});
  if(data)renderLedger(data);
});
function v(sel){return document.querySelector(sel).value.trim();}
