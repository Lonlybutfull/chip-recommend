/* Per-session run cards. No raw database paths are accepted from the browser. */
(() => {
  const state={mode:null,offset:0,limit:10,timer:null,generation:0,restore:null};
  const storageKey='data-agent-run-cards-v1';
  function saveView(){
    const root=document.getElementById('run-history');if(!root||!state.mode)return;
    const cards=[...root.querySelectorAll('.run-history-card')].map(c=>({key:c.dataset.runKey,open:c.open,sections:[...c.querySelectorAll('[data-run-section][open]')].map(x=>x.dataset.runSection),jobs:[...c.querySelectorAll('[data-job-id][open]')].map(x=>x.dataset.jobId)}));
    try{sessionStorage.setItem(storageKey,JSON.stringify({mode:state.mode,offset:state.offset,cards,scrollY:scrollY}))}catch(_){}
  }
  addEventListener('pagehide',saveView);
  const labels={running:'执行中',queued:'排队中',awaiting_agent:'等待提取',awaiting_publish:'候选待审核/发布',success:'已完成',succeeded:'成功',partial:'部分失败',failed:'失败',rejected:'已拒绝',interrupted:'已中断',not_run:'未执行',ready_to_publish:'待审核',published:'已发布',new:'新快照'};
  const txt=v=>esc(v==null?'未记录':String(v));
  const badge=s=>`<span class="run-badge ${txt(s)}">${txt(labels[s]||s)}</span>`;
  const value=v=>v==null?'未记录':v===''?'空值':typeof v==='object'?JSON.stringify(v):String(v);
  function link(url){
    if(!url)return '<span>来源未记录</span>';
    try{const u=new URL(url);if(!['http:','https:'].includes(u.protocol))return txt(url);return `<a href="${txt(redactDataAgentUrl(u.href))}" target="_blank" rel="noopener noreferrer">${txt(redactDataAgentUrl(u.href))}</a>`}catch(_){return txt(url)}
  }
  const duration=r=>{const a=Date.parse(r.started_at),b=r.active?Date.now():Date.parse(r.finished_at);return Number.isFinite(a)&&Number.isFinite(b)?Math.max(0,(b-a)/1000).toFixed(1)+' 秒':'未记录'};
  function summary(r){const s=r.statuses||{};return `<div class="run-card-head"><div><h3>周期 #${Number(r.id)} · ${txt(r.title)}<span class="run-title-hint">展开详情 ↓</span></h3><div class="run-card-meta">${r.session==='formal'?'正式运行':'隔离测试'} · 会话 ${txt(r.session)}<br>开始 ${txt(formatUpdateTime(r.started_at))} · ${r.active?'尚未结束':'结束 '+txt(formatUpdateTime(r.finished_at))} · 耗时 ${duration(r)}</div></div>${badge(r.status)}</div><div class="run-card-stats"><span class="run-card-stat">成功 ${Number(s.succeeded||0)}</span><span class="run-card-stat">失败 ${Number(s.failed||0)}</span><span class="run-card-stat">拒绝 ${Number(s.rejected||0)}</span><span class="run-card-stat">执行/等待 ${Number(s.running||0)+Number(s.queued||0)+Number(s.awaiting_agent||0)}</span><span class="run-card-stat">候选字段 ${Number(r.candidate_count||0)}</span></div>`}
  function fieldTable(rows){
    if(!rows.length)return '<div class="run-note">尚无候选字段。计划检查的字段不代表一定会更新。</div>';
    const basis={candidate_snapshot:'候选生成时快照',publication_record:'发布前记录',current_reference:'当前库参考值 · 历史旧值未记录',unknown:'旧值未记录'};
    return '<div class="run-field-scroll"><table class="run-field-table"><thead><tr><th>实体 / 字段</th><th>原值与原来源</th><th>候选新值与新来源</th><th>原文证据</th><th>变更与状态</th></tr></thead><tbody>'+rows.map(c=>`<tr><td><b>${txt(c.entity?.chip_model||c.entity?.model_name||c.entity?.name||'实体 #'+(c.entity?.id||'?'))}</b><br>${txt(c.target_table)}.${txt(c.field_name)}<div class="run-note">候选 #${Number(c.id)} · ${txt(c.owner_skill)}</div></td><td><div class="run-value">${txt(value(c.old_value))}</div><div class="run-note">${txt(basis[c.old_value_basis])}</div>${(c.old_sources||[]).map(s=>`<div class="run-links">${link(s.source_url)}<br>${txt(s.source_type)} · ${txt(s.updated_at)}</div>`).join('')||'<div class="run-note">原来源未记录</div>'}</td><td><div class="run-value run-new-value">${txt(c.proposed_value)}</div><div class="run-links">${link(c.source_url)}</div><div class="run-note">${txt(c.source_type)} · 置信度 ${txt(c.confidence)}</div></td><td><div class="run-value">${txt(c.evidence_text)}</div></td><td>${badge(c.published?'published':c.status)}<div class="run-note">${txt({added:'填补空值',changed:'值有变化',unchanged:'值相同',unknown:'无法比较'}[c.change_kind])}${c.published?'':' · 尚未写入业务表'}</div>${c.rejection_reason?txt(c.rejection_reason):''}</td></tr>`).join('')+'</tbody></table></div>';
  }
  const section=(id,title,body,open=false)=>`<details data-run-section="${id}" ${open?'open':''}><summary>${title}</summary>${body}</details>`;
  function detail(d){
    const params={...d.parameters}, names={limit:'首轮来源数量上限',force:'强制运行',mode:'运行模式',max_attempts:'抓取重试上限',proxy_enabled:'使用代理',max_crawl_depth:'最大抓取深度',selected_link_ids:'选定来源 ID',parser_version:'解析版本',model:'模型',thinking:'思考模式'};
    for(const k of ['limit','max_attempts','max_crawl_depth','model','thinking'])if(!(k in params))params[k]=null;
    const p=Object.entries(params).map(([k,v])=>`<div><b>${txt(names[k]||k)}</b>${txt(value(v))}</div>`).join('')+`<div><b>已记录模型响应 / Token 用量</b>${Number(d.runtime?.recorded_responses||0)} 次 / ${Number(d.runtime?.total_tokens||0).toLocaleString()} tokens</div>`;
    const phases=d.stages.map(s=>`<div class="run-stage"><b>${txt(s.label)}</b><br>${badge(s.status)}<p>${txt(s.description)}</p><p>${Object.entries(s.counts).map(([k,v])=>`${txt(labels[k]||k)} ${v}`).join(' · ')}</p></div>`).join('');
    const fields=d.planned_fields.map(s=>`<div class="run-source"><b>${txt(s.skill)}</b><div class="run-note">${s.fields.map(txt).join(' · ')}</div></div>`).join('')||'<div class="run-note">尚未规划字段提取。</div>';
    const discoveries=d.discoveries.map(e=>`<div class="run-source"><b>链路 #${Number(e.id)} · 发现任务 #${Number(e.discovery_job_id)}</b> · 深度 ${Number(e.crawl_depth)} · ${badge(e.status)}<div class="run-links">${link(e.parent_url)}<br>→ ${link(e.child_url||e.source_url)}</div><div class="run-note">访问 ${txt(formatUpdateTime(e.fetched_at))} ${txt(e.reason||'')}</div></div>`).join('')||'<div class="run-note">本次运行没有 URL 发现链路。</div>';
    const sources=d.sources.map(s=>`<div class="run-source"><b>来源访问 #${Number(s.id)} · 抓取批次 #${Number(s.run_id)}</b> · HTTP ${txt(s.http_status)} · ${badge(s.outcome)}<div class="run-links">请求 ${link(s.requested_url)}<br>最终 ${link(s.final_url)}</div><div class="run-note">${txt(formatUpdateTime(s.checked_at))} · ${txt(s.duration_ms)} ms · ${txt(s.response_bytes)} bytes</div>${s.error_message?`<div class="error">${txt(s.error_message)}</div>`:''}</div>`).join('');
    const timeline=d.timeline.map(e=>`<div><span class="run-note">${txt(formatUpdateTime(e.created_at))} · ${txt(e.actor)} · ${txt(e.stage)}</span><br>${badge(e.status)} ${txt(e.message)}${e.details&&Object.keys(e.details).length?`<details><summary>事件参数</summary><pre class="run-json">${txt(JSON.stringify(e.details,null,2))}</pre></details>`:''}</div>`).join('');
    return `<div class="run-toolbar"><button class="btn secondary" data-download-run>下载本次运行 JSON</button><span class="run-note">所有记录只属于本会话周期。候选通过格式与原文校验，并不等于语义审核通过。</span></div><div class="run-stage-grid">${phases}</div>`+
      section('params','运行信息与参数',`<div class="run-parameters">${p}</div><p class="run-note">历史运行未保存的参数显示“未记录”，不以当前配置补填。</p>`)+
      section('changes',`预计字段变更 · ${d.field_changes.length} 个候选 / ${d.field_changes.filter(x=>x.published).length} 个已发布`,fieldTable(d.field_changes),true)+
      section('planned','本轮计划检查哪些字段',`<p class="run-note">以下为已分配领域的可提取范围，具体预计更新以候选对比表为准。</p>${fields}`)+
      section('links',`两轮 URL 发现链路 · ${d.discoveries.length} 条`,discoveries)+
      section('sources',`网页访问记录（含全部轮次）· ${d.sources.length} 条`,sources)+
      section('jobs',`独立子任务 · ${d.jobs.length} 个`,d.jobs.map(renderDataAgentJob).join(''))+
      section('timeline',`本次运行时间线 · ${d.timeline.length} 条`,`<div class="run-timeline">${timeline}</div>`);
  }
  async function fill(card,run,refresh=false){
    const body=card.querySelector('.run-card-body');if(body.dataset.loaded&&!refresh)return;
    const d=await fetchJSON(`${API}/data-agent/runs/${encodeURIComponent(run.session)}/${run.id}`);
    if(!card.isConnected)return;
    if(d._error){body.innerHTML=`<div class="error">${txt(d._error)}</div>`;return}
    const serialized=JSON.stringify(d);if(body.dataset.snapshot===serialized)return;
    const open=[...body.querySelectorAll('details[open]')].map(x=>x.dataset.runSection?'s:'+x.dataset.runSection:x.dataset.jobId?'j:'+x.dataset.jobId:null);
    const previous=body.dataset.loaded;
    body.innerHTML=detail(d);body.dataset.loaded='1';body.dataset.snapshot=serialized;
    if(previous)body.querySelectorAll('details').forEach(x=>{if(x.dataset.runSection)x.open=open.includes('s:'+x.dataset.runSection);else if(x.dataset.jobId)x.open=open.includes('j:'+x.dataset.jobId)});
    else {const saved=state.restore?.cards?.find(x=>x.key===run.key);if(saved)body.querySelectorAll('details').forEach(x=>{if(x.dataset.runSection)x.open=saved.sections.includes(x.dataset.runSection);else if(x.dataset.jobId)x.open=saved.jobs.includes(x.dataset.jobId)})}
    body.querySelector('[data-download-run]').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`run-${run.session}-${run.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
  }
  window.loadRunHistory=async function({silent=false}={}){
    clearTimeout(state.timer);const mode=dataAgentMode();const generation=++state.generation;
    let root=$('run-history');if(!root){root=document.createElement('div');root.id='run-history';$('source-updates-list').before(root)}
    if(mode!==state.mode){const initial=state.mode===null;state.mode=mode;state.offset=0;root.innerHTML='';if(initial){try{const saved=JSON.parse(sessionStorage.getItem(storageKey));if(saved?.mode===mode){state.restore=saved;state.offset=saved.offset||0}}catch(_){}}}
    const data=await fetchJSON(`${API}/data-agent/runs?mode=${mode}&limit=${state.limit}&offset=${state.offset}`);
    if(generation!==state.generation)return;
    if(data._error){if(!silent)root.innerHTML=`<div class="error">${txt(data._error)}</div>`;return}
    if(!root.querySelector('.run-history-list'))root.innerHTML='<div class="run-history-header"></div><div class="run-history-list"></div><div class="run-history-pages run-toolbar"></div>';
    root.querySelector('.run-history-header').innerHTML=`<div><h3 style="margin:0">独立运行记录 <span class="run-note">${Number(data.total)} 次</span></h3><div class="run-note">每张卡片包含一次完整运行；展开查看流程、字段对比与日志。</div></div>`;
    const list=root.querySelector('.run-history-list');const keys=new Set(data.runs.map(r=>r.key));
    [...list.children].forEach(c=>{if(!keys.has(c.dataset.runKey))c.remove()});
    const promises=[];
    for(const [i,r] of data.runs.entries()){
      let card=[...list.children].find(c=>c.dataset.runKey===r.key);
      if(!card){card=document.createElement('details');card.className='run-history-card';card.dataset.runKey=r.key;card.innerHTML='<summary></summary><div class="run-card-body">加载详情…</div>';const saved=state.restore?.cards?.find(x=>x.key===r.key);card.open=saved?saved.open:i===0;list.append(card);card.addEventListener('toggle',()=>{if(card.open)fill(card,r)})}
      if(list.children[i]!==card)list.insertBefore(card,list.children[i]||null);
      card.querySelector(':scope > summary').innerHTML=summary(r);
      if(card.open)promises.push(fill(card,r,true));
    }
    if(!data.runs.length)list.innerHTML='<div class="empty">暂无独立运行记录。</div>';
    const pages=root.querySelector('.run-history-pages');pages.innerHTML=`<button class="btn secondary" data-prev ${state.offset===0?'disabled':''}>上一页</button><span class="run-note">${data.total?state.offset+1:0}–${Math.min(state.offset+state.limit,data.total)} / ${data.total}</span><button class="btn secondary" data-next ${state.offset+state.limit>=data.total?'disabled':''}>下一页</button>${data.warnings.length?`<span class="run-note">${data.warnings.length} 个旧会话不可读取</span>`:''}`;
    pages.querySelector('[data-prev]').onclick=()=>{state.offset=Math.max(0,state.offset-state.limit);list.innerHTML='';loadRunHistory()};pages.querySelector('[data-next]').onclick=()=>{state.offset+=state.limit;list.innerHTML='';loadRunHistory()};
    await Promise.all(promises);
    if(state.restore){const y=state.restore.scrollY;state.restore=null;window.scrollTo(0,y||0)}
    if(generation===state.generation)state.timer=setTimeout(()=>{if($('tab-status').classList.contains('active'))loadRunHistory({silent:true})},15000);
  };
})();
