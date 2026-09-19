"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let current = null, selected = null, sampleKey = "routing", busy = false;
let evidenceLayer = 'activity', layerCase = null;
function visibleEvents(){return evidenceLayer==='transfers' && current?.trace_review ? current.trace_review.events : current.analysis.snapshot.events;}
function renderTrace(){
  const trace=current?.trace_review;
  $('trace-panel').hidden=!trace;$('layer-control').hidden=!trace;
  if(!trace)return;
  $('evidence-layer').value=evidenceLayer;
  const scope=trace.scope, q=trace.queries;
  $('trace-summary').textContent=`${trace.selected_transfer_count} receipt-backed transfers displayed · ${trace.discovered_unique_logs} unique discovery logs`;
  $('trace-window').textContent=`Polygon blocks ${scope.from_block}–${scope.to_block} · ${trace.bounds[0].timestamp} to ${trace.bounds[1].timestamp}`;
  $('trace-counts').innerHTML=q.map((x,i)=>`<div><span class="detail-label">${i===0?'Subject incoming':i===1?'Subject outgoing':'Selected recipient outgoing'}</span><strong>${x.returned_logs}</strong><small>returned logs${x.at_result_cap?' · result cap reached':''}</small></div>`).join('');
  $('trace-continuation').textContent=trace.continuation.detail;
  $('trace-recipient').textContent=`Selected recipient: ${scope.recipient}`;
  $('trace-scope').textContent=`Contract: ${scope.contract}. One selected counterparty was queried; this does not link particular funds to later spending.`;
  $('trace-limits').innerHTML=trace.limitations.map(x=>`<p class="fine">${esc(x)}</p>`).join('');
}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json","X-Local-Investigation":"1"},body:JSON.stringify(data)});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "The operation failed.");
  return payload;
}
async function run(action) {
  if (busy) return;
  busy = true; $("error").hidden = true; $("status-message").textContent = "Working…";
  document.querySelectorAll("button").forEach(b => b.disabled = true);
  try { await action(); $("status-message").textContent = ""; }
  catch (error) { $("error").textContent = error.message; $("error").hidden = false; $("status-message").textContent = ""; }
  finally { busy = false; document.querySelectorAll("button").forEach(b => b.disabled = false); syncExport(); }
}
function syncExport() {
  const allowed = Boolean(current?.id && current.analysis.snapshot.source.export_allowed);
  $("export-report").disabled = !allowed; $("export-snapshot").disabled = !allowed;
  $("save-case").disabled = !current || Boolean(current.id);
  $("save-case").textContent = current?.id ? "Case saved ✓" : "Save as case ↗";
}
function show(view) {
  for (const name of ["investigation","cases","sources"]) {
    $(name+"-view").hidden = name !== view;
    $("nav-"+(name === "investigation" ? "investigate" : name)).classList.toggle("active", name === view);
  }
}
function drawGraph(events) {
  const shown = events.slice(0,10), names = [...new Set(shown.flatMap(e=>[e.from,e.to]))];
  if (!shown.length) { $("graph").innerHTML = '<p class="muted">No observations returned within this source query. This does not establish inactivity.</p>'; return; }
  const positions = [[80,116],[275,116],[475,43],[475,185],[685,185],[685,43]];
  const height = names.length > 6 ? 340+Math.floor((names.length-7)/4)*100 : 270;
  const coords = new Map(names.map((name,i)=>[name,positions[i] || [80+((i-6)%4)*195,290+Math.floor((i-6)/4)*100]]));
  let svg = `<svg viewBox="0 0 780 ${height}" role="img" aria-label="${shown.length} recorded relationships. Use the ledger below for complete event details."><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#7892b8"/></marker></defs>`;
  for (const [i,e] of shown.entries()) {
    const [x1,y1]=coords.get(e.from),[x2,y2]=coords.get(e.to), isSelected=e.id===selected;
    const offset = e.from === e.to ? 40 : (i-shown.length/2)*12;
    svg += `<path d="M ${x1} ${y1} Q ${(x1+x2)/2} ${(y1+y2)/2+offset} ${x2} ${y2}" fill="none" stroke="${isSelected?'#225ad7':'#9aacc6'}" stroke-width="${isSelected?3:1.8}" ${e.kind==='market_activity'?'stroke-dasharray="5 4"':'marker-end="url(#arrow)"'}/>`;
    if(e.kind==="bridge"&&!e.destination_tx) svg+=`<path d="M ${x2} ${y2+24} v25" stroke="#bd8739" stroke-dasharray="4 3" fill="none"/><text x="${x2}" y="${y2+65}" text-anchor="middle" fill="#94601e" font-size="12">Destination unresolved</text>`;
  }
  for(const name of names){ const [x,y]=coords.get(name); const focal=name.includes("wallet") && !name.includes("Funding");
    svg+=`<g><rect x="${x-72}" y="${y-22}" width="144" height="44" rx="7" fill="${focal?'#e9f1ff':'#fff'}" stroke="${focal?'#6794e0':'#cbd7e7'}"/><text x="${x}" y="${y+5}" text-anchor="middle" fill="#233d5f" font-size="13" font-family="Segoe UI,Arial">${esc(name.length>20?name.slice(0,17)+"…":name)}</text></g>`;
  }
  svg+='</svg>';
  if(events.length>10) svg+='<p class="fine">Graph displays the first 10 observations; the ledger contains the full bounded snapshot.</p>';
  $("graph").innerHTML=svg;
}
function selectEvent(id) {
  selected=id;
  const s={events:visibleEvents()}, e=s.events.find(x=>x.id===id);
  document.querySelectorAll("#ledger tr").forEach(r=>r.classList.toggle("selected",r.dataset.id===id));
  renderReceipt(e);
  if(!e){$("detail").innerHTML='<p class="muted">No event evidence is available.</p>';drawGraph(s.events);return;}
  $("evidence-kind").textContent=e.kind.replaceAll("_"," ");
  const field=(label,value,mono=false)=>`<div class="detail-label">${label}</div><div class="detail-value ${mono?'mono':''}">${esc(value)}</div>`;
  $("detail").innerHTML=`<div class="detail-label">${e.evidence_basis?'Exact raw collateral units':'Recorded amount · asset units'}</div><div class="detail-amount">${esc(e.amount_display)} <small>${esc(e.asset.symbol)}</small></div>`+field("From",e.from)+field("To",e.to)+field("Network",e.chain)+field("Evidence status",e.evidence_basis||e.status)+field("Transaction / locator",`${e.tx} / ${e.locator}`,true)+field("Evidence identifier",e.id,true)+(e.source?field('RPC source',e.source):'')+(e.destination_chain?field("Claimed destination",e.destination_chain):"");
  drawGraph(s.events);
}
function renderReceipt(event) {
  const review=current?.receipt_review, checks=review?.checks || [];
  $("receipt-count").textContent=checks.length?`${checks.length} selected transactions replayed`:'No replayable evidence';
  $("receipt-notice").textContent=review?.notice || 'Import a case bundle to inspect receipt evidence. Source observations and older analyst summaries are not independently replayed here.';
  const check=checks.find(c=>c.tx===event?.tx?.toLowerCase() && event.chain==='eip155:137');
  $("receipt-nav").innerHTML=checks.map((c,i)=>`<button class="secondary" data-receipt="${esc(c.tx)}" aria-pressed="${c===check}">Receipt ${i+1}</button>`).join('');
  $("receipt-nav").querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{const e=visibleEvents().find(e=>e.tx.toLowerCase()===b.dataset.receipt&&e.chain==='eip155:137');if(e)selectEvent(e.id);}));
  if(!check){$("receipt-detail").innerHTML=`<p class="review-empty">${checks.length?'The selected activity has no included receipt check. Select a receipt above or another ledger row.':'No chain conclusion is available for the selected observation.'}</p>`;return;}
  const r=check.receipt, settlement=check.settlement, finality=check.finality;
  const field=(name,value)=>`<div><span class="detail-label">${esc(name)}</span><div class="detail-value">${esc(value)}</div></div>`;
  const finalityLabels={rpc_reported_finalized:'Finalized according to imported RPC evidence',not_yet_finalized:'Finalized boundary not reached',unresolved:'Finality unresolved',changed_block:'Block identity changed'};
  const meta=check.metadata, decimals=meta.find(x=>x.field==='decimals'), symbol=meta.find(x=>x.field==='symbol');
  const units=(raw)=>{if(!decimals)return `${raw} raw units`;const d=decimals.value,p=raw.padStart(d+1,'0');return `${d?p.slice(0,-d)+'.'+p.slice(-d):p} ${symbol?.value||'token units'}`;};
  let html=`<div class="receipt-grid">${field('Execution',r.execution || 'Unresolved')}${field('Receipt block',r.block_number ?? 'Unavailable')}${field('Finality',finalityLabels[finality.state]||'Unresolved')}${field('RPC retrieval time',check.retrieved_at)}</div><p class="receipt-reference"><a href="https://polygonscan.com/tx/${check.tx}" target="_blank" rel="noopener noreferrer">View transaction on PolygonScan ↗</a><span class="mono">${esc(check.tx)}</span></p><p class="fine">Recorded RPC source: ${esc(check.source)}. This import did not contact that source.</p>`;
  if(settlement.state==='complementary_buy_supported'){
    html+=`<div class="settlement-summary"><strong>Purchase and fee supported by matching events</strong><div class="receipt-grid">${field('Payment to seller',units(settlement.collateral_payment_raw))}${field('Exchange fee · separate from gas',units(settlement.collateral_fee_raw))}${field('Total collateral debit',units(settlement.collateral_total_debit_raw))}${field('Outcome tokens received',settlement.outcome_received_raw+' raw units')}</div><p class="fine">Order events describe this settlement; they are not additional purchases. Seller and fee-recipient roles do not identify their beneficial owners.</p></div>`;
  }else html+=`<p class="review-empty">Settlement unresolved: ${esc(settlement.reason || 'No supported pattern.')}</p>`;
  if(meta.length)html+=`<p class="metadata-note">Metadata: ${esc(meta.map(m=>`${m.field}=${m.value} at block ${m.block_number}`).join('; '))}. ${meta.some(m=>!m.at_receipt_block)?'Metadata was queried at a different block; historical state at the receipt block is not established.':'Metadata corresponds to the receipt block.'} Token identity and backing are not independently audited by this import.</p>`;
  if(r.transfers?.length)html+=`<details class="receipt-logs"><summary>Exact transfer evidence · ${r.transfers.length} logs</summary><div class="table-scroll"><table><thead><tr><th>Locator</th><th>From → To</th><th>Raw units</th><th>Emitting contract</th></tr></thead><tbody>${r.transfers.map(t=>`<tr><td>${esc(t.locator)}</td><td class="mono">${esc(t.from)}<br>→ ${esc(t.to)}</td><td>${esc(t.amount_raw)}</td><td class="mono">${esc(t.contract)}</td></tr>`).join('')}</tbody></table></div></details>`;
  if(settlement.events?.length)html+=`<details class="receipt-logs"><summary>Decoded settlement events · ${settlement.events.length} records</summary>${settlement.events.map(e=>`<div class="decoded-event"><strong>${esc(e.locator)} · ${esc(e.kind)}</strong><pre>${esc(JSON.stringify(e,null,2))}</pre></div>`).join('')}</details>`;
  html+=`<p class="fine">${esc(review.scope)} ${esc(finality.reason||'RPC finality is not an independently verified consensus proof.')} ${esc(r.reason||'')}</p>`;
  $("receipt-detail").innerHTML=html;
}
function render() {
  const a=current.analysis,s=a.snapshot;
  if(layerCase!==current.id){evidenceLayer=current.trace_review?'transfers':'activity';layerCase=current.id;}
  if(!current.trace_review)evidenceLayer='activity';
  const events=visibleEvents();renderTrace();
  $("case-title").textContent=current.title;$("case-label").textContent=current.id || "UNSAVED EXAMPLE";
  const latestDisposition=current.notes.at(-1)?.disposition || 'Under review';
  $("active-context").textContent=`Viewing: ${s.source.provider} · Latest disposition: ${latestDisposition}`;
  $("disposition").value=latestDisposition;$("note").value='';
  $("provider").value=current.receipt_review?'bundle':s.source.provider==='Built-in synthetic fixtures'?'sample':s.source.provider.startsWith('Polymarket')?'polymarket':'snapshot';
  if($("provider").value==='sample'){$("sample").value=s.source.reference.includes('ordinary')?'ordinary':'routing';sampleKey=$("sample").value;}
  if($("provider").value==='polymarket')$("wallet").value=s.subject.address;
  syncProviderFields();
  $("subject").textContent=`${s.subject.chain} / ${s.subject.address}`;
  $("synthetic").textContent=s.synthetic?"Synthetic data":"Source-reported data";
  $("coverage").textContent=s.coverage.state+" coverage";
  $("event-count").textContent=a.summary.event_count;$("chain-count").textContent=a.summary.chain_count;$("boundary-count").textContent=a.summary.boundary_count;
  $("ledger-count").textContent=evidenceLayer==='transfers'?`${events.length} selected receipt-backed transfers`:`${events.length} source observations`;
  $('graph-basis').textContent=evidenceLayer==='transfers'?'Solid arrows show selected token transfers. The window and later recipient path remain bounded.':'Dashed lines show market activity associations; solid arrows show declared transfers.';
  $("ledger").innerHTML=events.map(e=>`<tr data-id="${esc(e.id)}"><td><button data-event="${esc(e.id)}">${esc(e.label)}</button><small>${esc(e.from)} → ${esc(e.to)}</small></td><td>${esc(e.amount_display)}<small>${esc(e.asset.symbol)}</small></td><td><span class="badge ${s.synthetic?'amber':'neutral'}">${e.evidence_basis?'RPC receipt matched':esc(e.status.replaceAll('_',' '))}</span></td><td>${esc(e.timestamp.replace('T',' ').replace('Z','').slice(0,16))}</td></tr>`).join("");
  $("ledger").querySelectorAll("button").forEach(b=>b.addEventListener("click",()=>selectEvent(b.dataset.event)));
  $("boundaries").innerHTML=a.boundaries.length?a.boundaries.map(b=>`<div class="boundary"><strong>${esc(b.label)}</strong><p>${esc(b.detail)}</p></div>`).join(""):'<div class="boundary"><strong>No declared boundary events</strong><p>This does not establish complete coverage or a low-risk finding.</p></div>';
  if(current.trace_review)$("boundaries").innerHTML+=`<div class="boundary"><strong>Selected recipient continuation</strong><p>${esc(current.trace_review.continuation.detail)}</p></div>`;
  $("source-detail").innerHTML=`<div class="detail-label">Source</div><div class="detail-value">${esc(s.source.provider)}</div><div class="detail-label">Reference</div><div class="source-ref">${esc(s.source.reference)}</div><div class="detail-label">Retrieved</div><div class="detail-value">${esc(s.source.retrieved_at)}</div><div class="detail-label">Coverage notes</div>${s.coverage.limitations.map(x=>`<p class="fine">${esc(x)}</p>`).join("")}`;
  $("notes").innerHTML=current.notes.map(n=>`<div class="note-entry"><span class="badge neutral">${esc(n.disposition)}</span><p>${esc(n.text)}</p><small>${esc(n.created_at)}</small></div>`).join("");
  selectEvent(events.some(e=>e.id===selected)?selected:events[0]?.id);
  syncExport();show("investigation");
}
async function openExample(key) {
  if(!["routing","ordinary"].includes(key))throw new Error("Unknown example.");
  current=await api('/api/samples/'+key);sampleKey=key;selected=null;$("note").value="";
  $("provider").value="sample";$("sample").value=key;syncProviderFields();render();
  return {example:key,event_count:current.analysis.summary.event_count,synthetic:true};
}
async function refreshCases(){const cases=await api('/api/cases');$("case-count").textContent=cases.length;return cases;}
$("open-demo").addEventListener("click",()=>run(async()=>{
 const bundle=await api('/api/demo-bundle');
 current=await api('/api/cases/import-bundle',{bundle});
 selected=null;$("note").value="";$("provider").value="bundle";
 syncProviderFields();render();await refreshCases();
 $('investigation-view').scrollIntoView({behavior:'smooth',block:'start'});
}));
async function saveCase(){if(!current)throw new Error("Open an investigation first.");if(!current.id){current=await api('/api/cases',{provider:"sample",subject:sampleKey,title:current.title});render();await refreshCases();}return current;}
async function saveNote(text,disposition){await saveCase();current=await api(`/api/cases/${current.id}/notes`,{text,disposition});$("note").value="";render();return {case_id:current.id,note_count:current.notes.length};}
$("load").addEventListener("click",()=>run(async()=>{
 const provider=$("provider").value;
 if(provider==="sample")return openExample($("sample").value);
 if(provider==="snapshot"||provider==="bundle"){
   const file=$("snapshot-file").files[0];if(!file)throw new Error(provider==='bundle'?"Select a version 1.1 or 1.2 case bundle JSON file.":"Select a version 1.0 snapshot JSON file.");if(file.size>1_000_000)throw new Error("Import file exceeds the 1 MB limit.");
   let snapshot;try{snapshot=JSON.parse(await file.text());}catch{throw new Error("The file is not valid JSON.");}
   current=provider==='bundle'?await api('/api/cases/import-bundle',{bundle:snapshot}):await api('/api/cases',{provider,subject:"import",title:"Imported evidence snapshot",snapshot});
 }else{current=await api('/api/cases',{provider,subject:$("wallet").value.trim(),title:"Public wallet activity"});}
 selected=null;render();await refreshCases();
}));
function syncProviderFields(){const p=$("provider").value;$("sample-field").hidden=p!=="sample";$("wallet-field").hidden=p!=="polymarket";$("import-field").hidden=!["snapshot","bundle"].includes(p);$("import-label").textContent=p==='bundle'?'Case bundle · version 1.1 / 1.2':'Evidence snapshot · version 1.0';$("load").textContent=p==="sample"?"Open example":["snapshot","bundle"].includes(p)?"Import as case":"Fetch & create case";}
$('evidence-layer').addEventListener('change',()=>{const note=$('note').value,disposition=$('disposition').value;evidenceLayer=$('evidence-layer').value;selected=null;render();$('note').value=note;$('disposition').value=disposition;});
$("provider").addEventListener("change",syncProviderFields);
$("save-case").addEventListener("click",()=>run(saveCase));
$("save-note").addEventListener("click",()=>run(async()=>{if(!$("note").value.trim())throw new Error("Write an investigation note first.");return saveNote($("note").value,$("disposition").value);}));
$("export-report").addEventListener("click",()=>{if(current?.id)location.href=`/api/cases/${current.id}/report`;});
$("export-snapshot").addEventListener("click",()=>{if(current?.id)location.href=`/api/cases/${current.id}/snapshot`;});
$("nav-investigate").addEventListener("click",()=>show("investigation"));$("back-investigate").addEventListener("click",()=>show("investigation"));
$("nav-cases").addEventListener("click",()=>run(async()=>{const cases=await refreshCases();$("cases-list").innerHTML=cases.length?cases.map(c=>`<div class="list-item"><div><strong>${esc(c.title)}</strong><p>${esc(c.id)}</p><small>${esc(c.created_at)}</small></div><button class="secondary" data-case="${esc(c.id)}">Open case</button></div>`).join(""):'<div class="list-item"><p>No saved cases yet. Open an example and save it to begin.</p></div>';$("cases-list").querySelectorAll("button").forEach(b=>b.addEventListener("click",()=>run(async()=>{current=await api('/api/cases/'+b.dataset.case);selected=null;render();})));show("cases");}));
$("nav-sources").addEventListener("click",()=>run(async()=>{const providers=await api('/api/providers');$("sources-list").innerHTML=providers.map(p=>`<div class="list-item"><div><strong>${esc(p.name)}</strong><p>${esc(p.description)}</p><span class="badge ${p.state==='available'?'blue':'neutral'}">${esc(p.state.replaceAll('_',' '))}</span></div><strong>${esc(p.cost)}</strong></div>`).join("");show("sources");}));
// Optional browser tools use the same actions as the visible application.
if(document.modelContext?.registerTool){const lifecycle=new AbortController();addEventListener("pagehide",()=>lifecycle.abort(),{once:true});
 const tools=[{name:"read_investigation",description:"Read the displayed investigation and its coverage; no changes.",inputSchema:{type:"object",properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute:()=>current?{title:current.title,case_id:current.id,summary:current.analysis.summary,coverage:current.analysis.snapshot.coverage}:null},
 {name:"open_example",description:"Display a synthetic example without creating a saved case.",inputSchema:{type:"object",properties:{example:{type:"string",enum:["routing","ordinary"]}},required:["example"],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:false},execute:async input=>{if(busy)throw new Error("An operation is already running.");return openExample(input?.example);}}];
 for(const tool of tools){try{Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{}}
}
run(async()=>{await openExample("routing");await refreshCases();});
