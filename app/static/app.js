const state = { framework: null, documents: [], claims: [], selectedClaim: null, searchResults: [] };
const titles = {dashboard:'研究總覽',documents:'文獻與索引',search:'混合檢索',claims:'主張與證據',audit:'GPS 五項稽核',writer:'受控書寫室',framework:'HGPF 31項框架'};
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];

async function api(path, options={}) {
  const init = {...options, headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}), ...(options.headers||{})}};
  const response = await fetch(path, init);
  if (!response.ok) { let message=`HTTP ${response.status}`; try { const e=await response.json(); message=e.detail||message; } catch {} throw new Error(message); }
  return response.status===204?null:response.json();
}
function formatBytes(value){
  if(!Number.isFinite(value)||value<=0)return '0 B';
  const units=['B','KB','MB','GB'];let size=value,index=0;
  while(size>=1024&&index<units.length-1){size/=1024;index+=1}
  return `${size>=10||index===0?Math.round(size):size.toFixed(1)} ${units[index]}`;
}
function setUploadProgress({fileName,percent=0,stage='準備上傳',detail='',stateName='active'}){
  const value=Math.max(0,Math.min(100,Math.round(percent)));
  const panel=$('#upload-progress');
  panel.hidden=false;panel.dataset.state=stateName;
  $('#upload-progress-file').textContent=fileName||'—';
  $('#upload-progress-stage').textContent=stage;
  $('#upload-progress-percent').textContent=`${value}%`;
  $('#upload-progress-bar').style.width=`${value}%`;
  $('#upload-progress-track').setAttribute('aria-valuenow',String(value));
  $('#upload-progress-detail').textContent=detail||stage;
}
function uploadDocumentWithProgress(file,onProgress){
  return new Promise((resolve,reject)=>{
    const form=new FormData();form.append('file',file);
    const request=new XMLHttpRequest();request.open('POST','/api/documents/upload');request.responseType='json';
    request.upload.addEventListener('progress',event=>{
      if(!event.lengthComputable)return;
      onProgress(Math.round((event.loaded/event.total)*100),event.loaded,event.total);
    });
    request.addEventListener('load',()=>{
      const body=request.response||{};
      if(request.status>=200&&request.status<300){resolve(body);return}
      reject(new Error(body.detail||`HTTP ${request.status}`));
    });
    request.addEventListener('error',()=>reject(new Error('網路中斷，文件上傳失敗。')));
    request.addEventListener('abort',()=>reject(new Error('文件上傳已取消。')));
    request.send(form);
  });
}
function escapeHtml(value=''){return String(value).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function truncate(text='', n=260){return text.length>n?text.slice(0,n)+'……':text;}
function toast(message,error=false){const node=$('#toast');node.textContent=message;node.className=error?'show error':'show';setTimeout(()=>node.className='',3200)}
function formatDate(value){return value?new Intl.DateTimeFormat('zh-TW',{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):''}

function showView(name){
  $$('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${name}`));
  $$('.nav-item').forEach(v=>v.classList.toggle('active',v.dataset.view===name));
  $('#page-title').textContent=titles[name]; window.scrollTo({top:0,behavior:'smooth'});
  if(name==='dashboard')loadDashboard(); if(name==='documents')loadDocuments(); if(name==='claims')loadClaims(); if(name==='framework')renderFramework();
}

async function loadDashboard(){
  const data=await api('/api/dashboard');
  const entries=[['documents','文獻','--green2'],['passages','證據段落','--blue'],['claims','研究主張','--gold'],['evidence_links','證據關係','--red'],['drafts','可審草稿','--green']];
  $('#stats').innerHTML=entries.map(([key,label,color])=>`<div class="stat-card" style="--accent:var(${color})"><span>${label}</span><strong>${data.stats[key]}</strong></div>`).join('');
  $('#recent-audits').className=data.latest_audits.length?'':'empty-state';
  $('#recent-audits').innerHTML=data.latest_audits.length?data.latest_audits.map(a=>`<div class="recent-item"><div class="score-disc" style="--score:${a.score}"><b>${a.score}</b></div><div><strong>${escapeHtml(a.subject)}</strong><span>${escapeHtml(a.level)}・${formatDate(a.created_at)}</span></div></div>`).join(''):'尚無稽核紀錄。';
}

async function loadFramework(){
  state.framework=await api('/api/framework');
  const opts=state.framework.fields.map(f=>`<option value="${f.id}">${f.id}. ${escapeHtml(f.local_name)}</option>`).join('');
  $('#search-field').insertAdjacentHTML('beforeend',opts); $('#claim-field').innerHTML=`<option value="">尚未指定</option>${opts}`;
  renderFramework();
}
function renderFramework(filter=''){
  if(!state.framework)return;
  const colors=['#2f765c','#3f6d81','#b68a3a','#a84d43'];
  $('#layer-grid').innerHTML=state.framework.layers.map((l,i)=>`<article class="layer-card" style="--accent:${colors[i]}"><b>${l.id}</b><h3>${escapeHtml(l.name)}</h3><em>${escapeHtml(l.question)}</em><p>${escapeHtml(l.description)}</p></article>`).join('');
  const needle=filter.trim().toLowerCase();
  const rows=state.framework.fields.filter(f=>!needle||JSON.stringify(f).toLowerCase().includes(needle));
  $('#framework-table').innerHTML=rows.map(f=>`<tr><td>${f.id}</td><td>${escapeHtml(f.category)}</td><td><strong>${escapeHtml(f.original_name)}</strong></td><td>${escapeHtml(f.local_name)}</td><td>${f.domains.map(d=>`<span class="domain-pill">${d}</span>`).join('')}</td><td>${escapeHtml(f.audit_focus)}</td></tr>`).join('');
}

async function loadDocuments(){
  state.documents=await api('/api/documents'); $('#document-count').textContent=`${state.documents.length} 份`;
  $('#document-table').innerHTML=state.documents.length?state.documents.map(d=>`<tr><td><strong>${escapeHtml(d.title)}</strong><span class="source-path" title="${escapeHtml(d.source_path)}">${escapeHtml(d.source_path)}</span></td><td>${escapeHtml(d.source_type)}</td><td>${d.passage_count}</td><td><span class="tag">${escapeHtml(d.access_level)}</span></td><td>${formatDate(d.created_at)}</td><td><button class="button ghost audit-document-button" data-audit-document="${d.id}">執行31項＋GPS預檢</button></td></tr>`).join(''):`<tr><td colspan="6" class="empty-state">尚無文獻，請匯入OCR檔。</td></tr>`;
}

function renderDocumentAudit(report){
  const s=report.summary;
  const detected=report.fields.filter(f=>f.hit_count);
  const missing=report.fields.filter(f=>!f.hit_count);
  $('#document-audit-result').className='document-audit-result';
  $('#document-audit-result').innerHTML=`
    <div class="audit-overview document-audit-overview"><div class="audit-big-score" style="--score:${s.score}"><strong>${s.score}</strong></div><div><span class="section-no">DOCUMENT READINESS・REPORT ${report.audit_id}</span><h2>${escapeHtml(report.document.title)}</h2><p>${escapeHtml(s.level)}。分數是研究就緒度提示，不是GPS通過分數。</p></div></div>
    <div class="document-audit-stats"><div><b>${s.candidate_fields}/31</b><span>HGPF候選欄位</span></div><div><b>${Math.round(s.page_locator_ratio*100)}%</b><span>原頁定位率</span></div><div><b>${s.ocr_review_count}</b><span>OCR待複核段落</span></div><div><b>${s.uncertainty_count}</b><span>限定／異說詞</span></div></div>
    <div class="audit-grid">${report.gps.map(i=>`<article class="audit-card"><span class="audit-no">${i.component}</span><h3>${escapeHtml(i.name)}</h3><span class="audit-score">${i.score}</span> <span class="status">${escapeHtml(i.status)}</span><ul>${i.findings.map(x=>`<li>${escapeHtml(x)}</li>`).join('')}</ul>${i.actions.length?`<ul>${i.actions.map(x=>`<li><strong>待辦：</strong>${escapeHtml(x)}</li>`).join('')}</ul>`:''}</article>`).join('')}</div>
    <article class="panel document-risk-panel"><div class="panel-head"><div><span class="section-no">RISKS & RULES</span><h3>風險與禁止推論</h3></div></div><div class="risk-list">${report.risks.map(r=>`<div class="risk-row"><span class="risk-severity ${escapeHtml(r.severity)}">${escapeHtml(r.severity)}</span><b>${escapeHtml(r.code)}</b><span>${escapeHtml(r.message)}</span></div>`).join('')}</div></article>
    <article class="panel hgpf-audit-panel"><div class="panel-head"><div><span class="section-no">HGPF 31</span><h3>31項欄位候選覆蓋</h3></div><div class="audit-legend"><span class="tag">候選 ${detected.length}</span><span class="tag muted-tag">未找到／可能不適用 ${missing.length}</span></div></div><p class="audit-disclaimer">${escapeHtml(report.disclaimer)}</p><div class="table-wrap tall"><table class="framework-table"><thead><tr><th>#</th><th>類別</th><th>在地欄位</th><th>狀態</th><th>命中</th><th>原文候選</th></tr></thead><tbody>${report.fields.map(f=>`<tr class="${f.hit_count?'field-hit':'field-empty'}"><td>${f.id}</td><td>${escapeHtml(f.category)}</td><td><strong>${escapeHtml(f.local_name)}</strong><br><small>${escapeHtml(f.audit_focus)}</small></td><td><span class="${f.hit_count?'field-status-hit':'field-status-empty'}">${escapeHtml(f.status)}</span></td><td>${f.hit_count}</td><td>${f.samples.length?f.samples.map(x=>`<details><summary>${x.page_hint?'頁'+escapeHtml(x.page_hint):'段落'+x.ordinal}・OCR ${Math.round(x.quality_score*100)}</summary><p>${escapeHtml(x.excerpt)}</p></details>`).join(''):'—'}</td></tr>`).join('')}</tbody></table></div></article>`;
  $('#document-audit-result').scrollIntoView({behavior:'smooth',block:'start'});
}

async function runDocumentAudit(documentId){
  const node=$('#document-audit-result');node.className='empty-state document-audit-result';node.textContent='正在執行HGPF 31項候選分析、OCR／引用檢查與GPS五項研究就緒度預檢…';
  try{const report=await api(`/api/documents/${documentId}/audit`,{method:'POST'});renderDocumentAudit(report);toast('文件HGPF＋GPS預檢完成');return report}catch(e){node.textContent='文件預檢失敗。';toast(e.message,true);throw e}
}

async function loadClaims(selectedId=null){
  state.claims=await api('/api/claims');
  const optionHtml=state.claims.map(c=>`<option value="${c.id}">${escapeHtml(c.subject)}</option>`).join('');
  ['#audit-claim','#writer-claim'].forEach(id=>$(id).innerHTML=optionHtml||'<option value="">尚無主張</option>');
  $('#search-claim').innerHTML='<option value="">不綁定</option>'+optionHtml;
  $('#claim-list').innerHTML=state.claims.length?state.claims.map(c=>`<button class="claim-item ${state.selectedClaim===c.id?'active':''}" data-claim-id="${c.id}"><div class="claim-top"><strong>${escapeHtml(c.subject)}</strong><span class="mini-score">${c.audit_score??'—'}</span></div><p>${escapeHtml(truncate(c.text,85))}</p><div class="claim-meta"><span class="confidence">${escapeHtml(c.confidence)}</span><span class="confidence">${c.evidence_count} 證據</span></div></button>`).join(''):'<div class="empty-state">尚無主張。</div>';
  const id=selectedId||state.selectedClaim||(state.claims[0]&&state.claims[0].id); if(id)await selectClaim(id);
}
async function selectClaim(id){
  id=Number(id);state.selectedClaim=id;
  $$('.claim-item').forEach(n=>n.classList.toggle('active',Number(n.dataset.claimId)===id));
  const data=await api(`/api/claims/${id}`); const c=data.claim;
  $('#claim-detail').innerHTML=`
    <div class="claim-title-row"><div><span class="section-no">${escapeHtml(c.claim_type)}・HGPF ${c.hgpf_field_id||'未指定'}</span><h2>${escapeHtml(c.subject)}</h2></div><span class="tag">${escapeHtml(c.status)}</span></div>
    <div class="claim-statement">${escapeHtml(c.text)}</div>
    <div class="evidence-section"><h3>證據關係 <span class="tag">${data.evidence.length} 筆</span></h3>${data.evidence.length?data.evidence.map(e=>`<div class="evidence-row"><span class="relation ${e.relation}">${e.relation}</span><div><strong>${escapeHtml(e.document_title)}・${e.page_hint?'頁'+escapeHtml(e.page_hint):'段落'+e.ordinal}</strong><p>${escapeHtml(truncate(e.text,230))}</p><small>${escapeHtml(e.note||'未加註')}・存取：${escapeHtml(e.access_level)}・OCR可用性 ${Math.round((e.quality_score??1)*100)}${e.quality_flags?.length?'・'+escapeHtml(e.quality_flags.join('、')):''}</small></div><button class="danger-link" data-delete-evidence="${e.id}">移除</button></div>`).join(''):'<div class="empty-state">尚無證據。請到「混合檢索」掛接結果。</div>'}</div>
    <div class="resolution-box"><strong>人工衝突處置</strong><p class="muted">若有反駁或限制證據，必須由具名研究者說明，不得由AI自行宣告已解決。</p><textarea id="resolution-note" placeholder="逐一說明衝突、來源品質、判斷理由與剩餘不確定性">${escapeHtml(c.resolution_note||'')}</textarea><div class="resolution-actions"><select id="claim-confidence-edit"><option ${c.confidence==='待查'?'selected':''}>待查</option><option ${c.confidence==='可能'?'selected':''}>可能</option><option ${c.confidence==='很可能'?'selected':''}>很可能</option><option ${c.confidence==='已證'?'selected':''}>已證</option></select><input id="claim-reviewer" placeholder="覆核者" value="${escapeHtml(c.reviewer||'')}"><button class="button primary" id="save-resolution">儲存人工判斷</button></div></div>`;
  $('#audit-claim').value=id; $('#writer-claim').value=id; $('#search-claim').value=id;
}

async function runSearch(event){
  event.preventDefault(); const query=$('#search-query').value.trim(); if(!query)return;
  $('#search-results').className='result-list empty-state'; $('#search-results').textContent='正在比對全文、語意與HGPF欄位…';
  try{
    const payload={query,limit:15,counterevidence:$('#counterevidence').checked,claim_id:Number($('#search-claim').value)||null,hgpf_field_id:Number($('#search-field').value)||null,document_ids:[]};
    const data=await api('/api/search',{method:'POST',body:JSON.stringify(payload)}); state.searchResults=data.results;
    $('#search-summary').textContent=`${data.mode}・找到 ${data.results.length} 筆候選段落；排序分數不是證據可信度。`;
    $('#search-results').className='result-list';
    $('#search-results').innerHTML=data.results.length?data.results.map((r,i)=>`<article class="result-card"><div class="result-rank">RANK ${String(i+1).padStart(2,'0')}<strong>${Math.round(r.score*100)}</strong></div><div><h3>${escapeHtml(r.document_title)} <span class="tag">${r.page_hint?'頁'+escapeHtml(r.page_hint):'段落'+r.ordinal}</span></h3><span class="source-path">${escapeHtml(r.source_path)}</span><p>${escapeHtml(r.text)}</p><div class="signals"><span class="signal">全文 ${Math.round(r.signals.lexical*100)}</span><span class="signal">語意 ${Math.round(r.signals.semantic*100)}</span><span class="signal">HGPF ${r.hgpf_fields.join(',')||'—'}</span><span class="signal ${r.quality_score<.75?'alert':''}">OCR可用性 ${Math.round((r.quality_score??1)*100)}</span>${r.quality_flags.map(t=>`<span class="signal alert">${escapeHtml(t)}</span>`).join('')}${r.signals.counterevidence.map(t=>`<span class="signal alert">${escapeHtml(t)}</span>`).join('')}</div></div><div class="result-actions"><button class="button light" data-link="${r.passage_id}" data-relation="支持">＋支持</button><button class="button ghost" data-link="${r.passage_id}" data-relation="反駁">＋反駁</button><button class="button ghost" data-link="${r.passage_id}" data-relation="限制">＋限制</button><button class="button ghost" data-link="${r.passage_id}" data-relation="脈絡">＋脈絡</button></div></article>`).join(''):'<div class="empty-state">目前索引找不到候選段落，請調整關鍵詞或匯入更多文獻。</div>';
  }catch(e){toast(e.message,true);$('#search-results').textContent='檢索失敗。'}
}

async function renderAudit(result){
  $('#audit-result').className=''; $('#audit-result').innerHTML=`<div class="audit-overview"><div class="audit-big-score" style="--score:${result.score}"><strong>${result.score}</strong></div><div><span class="section-no">LATEST AUDIT</span><h2>${escapeHtml(result.level)}</h2><p>五項合計100分；每項「通過初檢」仍保留人工責任。</p></div></div><div class="audit-grid">${result.items.map(i=>`<article class="audit-card"><span class="audit-no">${i.component}</span><h3>${escapeHtml(i.name)}</h3><span class="audit-score">${i.score}</span> <span class="status">${escapeHtml(i.status)}</span><ul>${i.findings.map(x=>`<li>${escapeHtml(x)}</li>`).join('')}</ul>${i.actions.length?`<ul>${i.actions.map(x=>`<li><strong>待辦：</strong>${escapeHtml(x)}</li>`).join('')}</ul>`:''}</article>`).join('')}</div><div class="audit-disclaimer">${escapeHtml(result.disclaimer)}</div>`;
}

function renderWriter(draft){
  $('#writer-result').className=''; $('#writer-result').innerHTML=`<article class="writer-card"><div class="draft-paper"><span class="section-no">DRAFT ${draft.id}・${escapeHtml(draft.status)}・${escapeHtml(draft.evidence_state||'待查')}</span><h2>${escapeHtml(draft.title)}</h2><pre>${escapeHtml(draft.content)}</pre></div><aside class="citation-panel"><h3>證據卡</h3>${draft.citations.map(c=>`<div class="citation-card"><b>〔${c.label}〕${escapeHtml(c.relation)}</b><br>${escapeHtml(c.document_title)}・${escapeHtml(c.locator)}<br><span class="muted">存取：${escapeHtml(c.access_level)}・OCR可用性 ${Math.round((c.quality_score??1)*100)}<br>${escapeHtml(c.excerpt)}</span></div>`).join('')}<div class="review-form"><strong>人工覆核</strong><label>狀態<select id="draft-status"><option>Needs-further-research</option><option>Human-reviewed</option><option>Approved-for-publication</option></select></label><label>覆核者<input id="draft-reviewer"></label><label>說明<textarea id="draft-note"></textarea></label><button class="button primary" data-review-draft="${draft.id}">儲存覆核</button></div></aside></article>`;
}

document.addEventListener('click',async event=>{
  const nav=event.target.closest('[data-view]'); if(nav){showView(nav.dataset.view);return}
  const go=event.target.closest('[data-go]');if(go){showView(go.dataset.go);return}
  const claim=event.target.closest('[data-claim-id]');if(claim){await selectClaim(claim.dataset.claimId);return}
  const auditDocument=event.target.closest('[data-audit-document]');if(auditDocument){await runDocumentAudit(Number(auditDocument.dataset.auditDocument));return}
  const link=event.target.closest('[data-link]');if(link){const claimId=Number($('#search-claim').value);if(!claimId){toast('請先在上方選擇要綁定的主張。',true);return}try{await api(`/api/claims/${claimId}/evidence`,{method:'POST',body:JSON.stringify({passage_id:Number(link.dataset.link),relation:link.dataset.relation,weight:.5,note:'由混合RAG提出，待人工回看原文；檢索分數未轉作證據權重。'})});toast(`已加入「${link.dataset.relation}」證據`);await loadClaims(claimId)}catch(e){toast(e.message,true)}return}
  const del=event.target.closest('[data-delete-evidence]');if(del){if(confirm('移除此證據關係？原始文獻不會刪除。')){await api(`/api/evidence/${del.dataset.deleteEvidence}`,{method:'DELETE'});await loadClaims(state.selectedClaim);toast('已移除證據關係')}return}
  if(event.target.id==='new-claim-button'){$('#claim-dialog').showModal();return}
  if(event.target.id==='seed-button'){event.target.disabled=true;try{const r=await api('/api/seed',{method:'POST',body:JSON.stringify({reset:false})});toast(`示範資料就緒：${r.documents.length} 份文獻`);await Promise.all([loadDocuments(),loadClaims(),loadDashboard()])}catch(e){toast(e.message,true)}finally{event.target.disabled=false}return}
  if(event.target.id==='upload-button'){
    const button=event.target;const file=$('#upload-file').files[0];if(!file){toast('請先選擇檔案。',true);return}
    button.disabled=true;
    setUploadProgress({fileName:file.name,percent:0,stage:'正在上傳文件',detail:`0 B / ${formatBytes(file.size)}`});
    try{
      const result=await uploadDocumentWithProgress(file,(percent,loaded,total)=>setUploadProgress({
        fileName:file.name,percent,
        stage:percent>=100?'傳輸完成，伺服器正在建立索引':'正在上傳文件',
        detail:percent>=100?'請稍候，正在解析文件並建立可追溯段落':`${formatBytes(loaded)} / ${formatBytes(total)}`
      }));
      setUploadProgress({fileName:file.name,percent:100,stage:'索引完成，正在執行框架檢核',detail:'正在執行 HGPF 31項與 GPS 五項研究就緒度預檢'});
      toast(result.duplicate?'文件已存在，直接執行預檢':'文件已上傳並建立索引');
      await Promise.all([loadDocuments(),loadDashboard()]);await runDocumentAudit(result.id);
      setUploadProgress({fileName:file.name,percent:100,stage:'上傳與檢核完成',detail:'文件已建立索引，HGPF／GPS 預檢報告已產生',stateName:'complete'});
    }catch(e){setUploadProgress({fileName:file.name,percent:0,stage:'上傳或處理失敗',detail:e.message,stateName:'error'});toast(e.message,true)}
    finally{button.disabled=false}
    return;
  }
  if(event.target.id==='audit-button'){const id=Number($('#audit-claim').value);if(!id){toast('尚無可稽核主張。',true);return}try{renderAudit(await api(`/api/audit/${id}`,{method:'POST'}));await loadDashboard()}catch(e){toast(e.message,true)}return}
  if(event.target.id==='generate-button'){const id=Number($('#writer-claim').value);if(!id){toast('尚無主張。',true);return}try{renderWriter(await api('/api/drafts',{method:'POST',body:JSON.stringify({claim_id:id})}));toast('已產生可審核草稿')}catch(e){toast(e.message,true)}return}
  if(event.target.id==='save-resolution'){try{await api(`/api/claims/${state.selectedClaim}`,{method:'PATCH',body:JSON.stringify({confidence:$('#claim-confidence-edit').value,resolution_note:$('#resolution-note').value,reviewer:$('#claim-reviewer').value,status:'人工複核'})});toast('已儲存具名人工判斷');await loadClaims(state.selectedClaim)}catch(e){toast(e.message,true)}return}
  const review=event.target.closest('[data-review-draft]');if(review){try{await api(`/api/drafts/${review.dataset.reviewDraft}`,{method:'PATCH',body:JSON.stringify({status:$('#draft-status').value,reviewer:$('#draft-reviewer').value,review_note:$('#draft-note').value})});toast('覆核狀態已儲存')}catch(e){toast(e.message,true)}return}
});

$('#search-form').addEventListener('submit',runSearch);
$('#import-form').addEventListener('submit',async event=>{event.preventDefault();try{const result=await api('/api/documents/import',{method:'POST',body:JSON.stringify({path:$('#import-path').value,source_type:$('#import-type').value,access_level:$('#import-access').value})});toast(result.duplicate?'此文件已建檔，直接執行預檢。':'文件已建立可追溯索引');await Promise.all([loadDocuments(),loadDashboard()]);await runDocumentAudit(result.id)}catch(e){toast(e.message,true)}});
$('#upload-file').addEventListener('change',event=>{
  const file=event.target.files[0];$('#upload-name').textContent=file?`${file.name}・${formatBytes(file.size)}`:'尚未選擇檔案';
  if(file)setUploadProgress({fileName:file.name,percent:0,stage:'準備上傳',detail:`檔案大小 ${formatBytes(file.size)}`,stateName:'ready'});
  else $('#upload-progress').hidden=true;
});
$('#claim-form').addEventListener('submit',async event=>{event.preventDefault();try{const c=await api('/api/claims',{method:'POST',body:JSON.stringify({claim_type:$('#claim-type').value,subject:$('#claim-subject').value,text:$('#claim-text').value,hgpf_field_id:Number($('#claim-field').value)||null,confidence:$('#claim-confidence').value,asserted_value:''})});$('#claim-dialog').close();toast('已建立研究主張');await loadClaims(c.id)}catch(e){toast(e.message,true)}});
$('#framework-filter').addEventListener('input',e=>renderFramework(e.target.value));

async function init(){
  try{await api('/api/health');$('#health').innerHTML='<i></i>本機資料庫已連線';await loadFramework();await Promise.all([loadDashboard(),loadDocuments(),loadClaims()]);}
  catch(e){$('#health').textContent='系統未連線';toast(e.message,true)}
}
init();
