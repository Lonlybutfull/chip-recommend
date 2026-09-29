/* Per-session run cards. No raw database paths are accepted from the browser. */
(() => {
  const state={mode:null,offset:0,limit:10,timer:null,generation:0,restore:null};
  const storageKey='data-agent-run-cards-v1';
  function saveView(){
    const root=document.getElementById('run-history');if(!root||!state.mode)return;
    const cards=[...root.querySelectorAll('.run-history-card')].map(c=>({key:c.dataset.runKey,open:c.open,sections:[...c.querySelectorAll('[data-run-section][open]')].map(x=>x.dataset.runSection),jobs:[...c.querySelectorAll('[data-job-id][open]')].map(x=>x.dataset.jobId)}));
    const openWeb=[...document.querySelectorAll('#open-web-test-history .open-web-run')].map(c=>({session:c.dataset.openWebSession,open:c.open,sections:[...c.querySelectorAll('[data-run-section][open]')].map(x=>x.dataset.runSection)}));
    try{sessionStorage.setItem(storageKey,JSON.stringify({mode:state.mode,offset:state.offset,cards,openWeb,scrollY:scrollY}))}catch(_){}
  }
  addEventListener('pagehide',saveView);
  const labels={running:'进行中',queued:'等待',retry_wait:'待重试',awaiting_agent:'等待提取',awaiting_publish:'等待保存测试结果',success:'完成',succeeded:'可用',partial:'部分成功',failed:'失败',rejected:'未通过',interrupted:'已中断',not_run:'未访问',ready_to_publish:'已保存到测试区',published:'已保存',new:'新网页'};
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
    const details=`<div class="run-detail-block"><h4>运行阶段</h4><div class="run-stage-grid">${phases}</div></div>`+
      `<div class="run-detail-block"><h4>运行参数</h4><div class="run-parameters">${p}</div></div>`+
      `<div class="run-detail-block"><h4>计划提取字段</h4>${fields}</div>`+
      `<div class="run-detail-block"><h4>URL 发现链路 · ${d.discoveries.length} 条</h4>${discoveries}</div>`+
      `<div class="run-detail-block"><h4>网页访问记录 · ${d.sources.length} 条</h4>${sources||'<div class="run-note">本次运行没有网页访问记录。</div>'}</div>`+
      `<div class="run-detail-block"><h4>Agent 任务 · ${d.jobs.length} 个</h4>${d.jobs.map(renderDataAgentJob).join('')||'<div class="run-note">本次运行没有 Agent 任务。</div>'}</div>`+
      `<div class="run-detail-block"><h4>执行时间线 · ${d.timeline.length} 条</h4><div class="run-timeline">${timeline||'<div class="run-note">本次运行没有时间线记录。</div>'}</div></div>`;
    return `<div class="run-toolbar"><button class="btn secondary" data-download-run>下载本次运行 JSON</button></div>`+
      section('changes',`字段提取与校验 · ${d.field_changes.length} 条`,fieldTable(d.field_changes),true)+
      section('details','详细记录',details);
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
  const auditSkillLabels={'chip-identity':'芯片型号','chip-specs':'基础参数','chip-compute':'算力指标','chip-compatibility':'兼容信息','chip-benchmark':'实测数据','chip-deployment':'部署资料'};
  const auditCategoryOrder=['芯片型号','基础参数','算力指标','兼容信息','实测数据','部署资料'];
  const auditFieldLabels={
    vendor:'厂商',chip_series:'芯片系列',chip_model:'芯片型号',release_date:'发布时间',production_status:'发布状态',
    vram_gb:'显存容量',vram_type:'显存类型',vram_bw_gb_s:'显存带宽',tdp_w:'功耗',process_node_nm:'制程',form_factor:'产品形态',bus_interface:'接口类型',architecture:'架构',interconnect_bw_gb_s:'互联带宽',interconnect_tech:'互联技术',
    precision_support:'支持精度',precision_perf:'理论峰值',compute_units:'计算单元',tensor_cores:'张量计算单元',sm_count:'流处理器组',
    model_id:'模型',compat_status:'兼容状态',framework:'框架',precision:'精度',notes:'说明',
    suite_name:'测试套件',workload_type:'训练或推理',hardware_config:'硬件配置',chip_count:'芯片数量',batch_size:'批大小',input_seq_length:'输入长度',output_seq_length:'输出长度',concurrency:'并发数',throughput_tok_s:'总吞吐',prefill_throughput:'Prefill 吞吐',decode_throughput:'Decode 吞吐',time_to_first_token_ms:'首字时延',inter_token_latency_ms:'输出时延',tpot_ms:'每 Token 时延',memory_peak_mb:'峰值显存',mfu_pct:'MFU',test_date:'测试日期',
    backend:'部署后端',title:'资料标题',source_type:'资料类型'
  };
  const num=v=>Number.isFinite(Number(v))?Number(v):0;
  const isReachableStatus=s=>['new','changed','unchanged'].includes(String(s||''));
  const auditSkillName=skill=>auditSkillLabels[skill]||skill||'未记录';
  const auditSkillBadges=skills=>{
    const values=[...new Set((Array.isArray(skills)?skills:[skills]).filter(Boolean))];
    return values.length
      ?`<span class="audit-origin-skills">${values.map(skill=>`<span class="audit-skill-badge" title="${txt(skill)}">${txt(auditSkillName(skill))}</span>`).join('')}</span>`
      :'<span class="audit-origin-empty">未记录</span>';
  };
  const auditUrlKey=value=>String(value||'').trim().replace(/#.*$/,'').replace(/\/$/,'');
  function openWebSummary(run){
    const c=run.counts||{};
    if(run.skill==='all-skills'&&run.audit_summary){
      const a=run.audit_summary,existing=a.existing||{},fresh=a.new||{};
      const issueCount=Math.max(0,num(existing.safe_unique_urls)-num(existing.reachable))+Math.max(0,num(fresh.visited_unique_urls)-num(fresh.reachable));
      return `<div class="run-card-head"><div><h3>全部链接检查<span class="run-title-hint">展开结果 ↓</span></h3><div class="run-card-meta">已有链接库 + 6 类信息的开放互联网搜索<br>开始 ${txt(formatUpdateTime(run.started_at))} · 结束 ${txt(formatUpdateTime(run.finished_at))}</div></div>${badge(run.status)}</div><div class="run-card-stats"><span class="run-card-stat strong">已有链接可用 ${num(existing.reachable)} / ${num(existing.safe_unique_urls)}</span><span class="run-card-stat strong">新网址可用 ${num(fresh.reachable)} / ${num(fresh.visited_unique_urls)}</span><span class="run-card-stat warning">需处理 ${issueCount}</span></div>`;
    }
    return `<div class="run-card-head"><div><h3>开放互联网测试 · ${txt(run.skill_label||'基础参数')}</h3><div class="run-card-meta">${txt((run.chips||[]).join('、')||'自定义搜索')}<br>开始 ${txt(formatUpdateTime(run.started_at))} · 结束 ${txt(formatUpdateTime(run.finished_at))}</div></div>${badge(run.status)}</div><div class="run-card-stats"><span class="run-card-stat">网页访问 ${num(c.visit_succeeded)} / ${num(c.visited||c.coarse_passed)}</span><span class="run-card-stat">有效字段 ${num(c.validated)}</span></div>`;
  }
  function openWebCard(run){
    const audit=run.skill==='all-skills'&&run.audit_summary;
    return `<details class="run-history-card open-web-run${audit?' audit-run':''}" data-open-web-session="${txt(run.session_id)}"><summary>${openWebSummary(run)}</summary><div class="run-card-body">${audit?'展开查看检查覆盖、搜索结果与问题分布…':'展开查看测试结果…'}</div></details>`;
  }
  function auditFailureGroups(urls){
    const groups=new Map();
    for(const u of urls.filter(x=>!isReachableStatus(x.fetch_status))){
      const reason=String(u.precise_reason||'');let label='其他访问问题';
      if(/403|forbidden/i.test(reason))label='HTTP 403';
      else if(/404|not found/i.test(reason))label='HTTP 404';
      else if(/超过|too large|max.bytes|response.*large|2\s*MB/i.test(reason))label='响应内容过大';
      else if(/empty|空内容|无正文|没有.*正文/i.test(reason))label='无可用正文';
      else if(/timed out|timeout|超时/i.test(reason))label='访问超时';
      else if(/certificate|ssl|tls/i.test(reason))label='证书问题';
      else if(/network|connection|连接|unreachable/i.test(reason))label='网络连接失败';
      groups.set(label,(groups.get(label)||0)+1);
    }
    return [...groups.entries()].sort((a,b)=>b[1]-a[1]);
  }
  function auditFieldsByCategory(row){
    const result={};
    for(const label of auditCategoryOrder)result[label]=[];
    const existing=row._fieldsByCategory||row.fields_by_category||{};
    for(const label of auditCategoryOrder){
      if(Array.isArray(existing[label]))result[label]=[...new Set(existing[label].filter(Boolean).map(String))];
    }
    const activeLabel=auditSkillLabels[row.skill]||row.information_category||'';
    if(activeLabel&&result[activeLabel]&&Array.isArray(row.extracted_fields)){
      result[activeLabel]=[...new Set([...result[activeLabel],...row.extracted_fields.filter(Boolean).map(String)])];
    }
    return result;
  }
  function mergeAuditLinkAssets(rows){
    const merged=new Map(),rank={failed:0,retry_wait:1,unchanged:2,changed:3,new:4};
    for(const source of rows){
      const key=source.url||source.canonical_url||`missing-${merged.size}`;
      const fields=auditFieldsByCategory(source);
      if(!merged.has(key)){
        merged.set(key,{...source,information_categories:[...new Set(source.information_categories||[])],extracted_fields:[...new Set(source.extracted_fields||[])],discovery_skills:[...new Set([...(source.discovery_skills||[]),source.skill].filter(Boolean))],_fieldsByCategory:fields});
        continue;
      }
      const row=merged.get(key);
      row.information_categories=[...new Set([...(row.information_categories||[]),...(source.information_categories||[])])];
      row.extracted_fields=[...new Set([...(row.extracted_fields||[]),...(source.extracted_fields||[])])];
      row.discovery_skills=[...new Set([...(row.discovery_skills||[]),...(source.discovery_skills||[]),source.skill].filter(Boolean))];
      for(const label of auditCategoryOrder)row._fieldsByCategory[label]=[...new Set([...(row._fieldsByCategory[label]||[]),...(fields[label]||[])])];
      if((rank[source.fetch_status]??-1)>(rank[row.fetch_status]??-1)){
        for(const keyName of ['fetch_status','http_status','final_url','asset_status','decision_reason'])if(source[keyName]!=null&&source[keyName]!=='')row[keyName]=source[keyName];
      }
      if(!row.title&&source.title)row.title=source.title;
    }
    return [...merged.values()];
  }
  function auditCategoryCell(row,label){
    const categories=new Set(row.information_categories||[]);
    if(row.information_category)categories.add(row.information_category);
    const fields=(row._fieldsByCategory||auditFieldsByCategory(row))[label]||[];
    const fieldNames=fields.map(field=>auditFieldLabels[field]||field);
    if(fields.length)return `<span class="audit-category-state extracted" title="${txt(label)}：已提取并校验 ${fields.length} 个字段（${txt(fieldNames.join('、'))}）">${fields.length}</span>`;
    if(categories.has(label))return `<span class="audit-category-state matched" title="${txt(label)}：识别到该类信息，尚无通过校验的字段">✓</span>`;
    return `<span class="audit-category-state empty" title="${txt(label)}：未识别">—</span>`;
  }
  const auditCategoryHeader=()=>auditCategoryOrder.map(label=>`<div class="audit-link-category">${txt(label)}</div>`).join('');
  const auditCategoryCells=row=>auditCategoryOrder.map(label=>`<div class="audit-link-category">${auditCategoryCell(row,label)}</div>`).join('');
  function auditLinkRows(rows,group){
    if(!rows.length)return '<div class="run-note">本组没有链接。</div>';
    return `<div class="audit-link-list" data-audit-link-list="${txt(group)}"><div class="audit-link-header"><div>状态</div><div>链接与发现来源</div>${auditCategoryHeader()}<div>链接信息</div></div>${rows.map((row,index)=>{
      const url=row.url||row.canonical_url||'';
      const finalUrl=row.final_url&&row.final_url!==url?row.final_url:'';
      const title=row.title||row.description||url||`链接 ${index+1}`;
      const categories=(row.information_categories||[]).join('、')||row.information_category||row.category||'类别未记录';
      const owner=row.chip_model||row.vendor||row.source_domain||'';
      const http=row.http_status==null?'HTTP 未记录':`HTTP ${row.http_status}`;
      const reason=row.error||row.decision_reason||row.precise_reason||'';
      const origin=group==='existing'
        ?'<span class="audit-library-badge">已有链接库</span>'
        :auditSkillBadges(row.discovery_skills||row.skill);
      const strategy=row.query_strategy||'';
      const discoveryQuery=row.discovery_query||'';
      const reachable=isReachableStatus(row.fetch_status);
      const status=row.audit_excluded?'not_run':reachable?'succeeded':row.fetch_status==='retry_wait'?'retry_wait':'failed';
      const discoveryLabels=(row.discovery_skills||[]).map(auditSkillName).join(' ');
      const search=[title,url,finalUrl,categories,discoveryLabels,owner,row.source_domain,row.discovery_query,reason].filter(Boolean).join(' ').toLowerCase();
      return `<article class="audit-link-row" data-audit-link-row data-audit-link-search="${txt(search)}"><div class="audit-link-status"><span class="audit-link-index">${index+1}</span>${badge(status)}</div><div class="audit-link-main"><b>${txt(title)}</b><div class="run-links">${link(url)}${finalUrl?`<br><span>跳转至：</span>${link(finalUrl)}`:''}</div><div class="audit-discovery-line"><span class="audit-origin-label">发现来源</span>${origin}</div>${discoveryQuery?`<div class="audit-discovery-query"><span>搜索词</span>${txt(discoveryQuery)}${strategy?` · ${txt(strategy)}`:''}</div>`:''}${reason?`<div class="audit-link-reason">${txt(reason)}</div>`:''}</div>${auditCategoryCells(row)}<div class="audit-link-meta">${owner?`<span>${txt(owner)}</span>`:''}<span>${txt(http)}</span></div></article>`;
    }).join('')}</div>`;
  }
  function auditLinkGroup(group,label,rows){
    return `<div class="audit-link-tools"><label class="audit-link-filter-label">筛选${txt(label)}<input type="search" class="audit-link-filter" data-audit-link-filter="${txt(group)}" placeholder="输入标题、域名、Skill 或 URL"></label><span class="run-note">显示 <b data-audit-link-count="${txt(group)}">${rows.length}</b> / ${rows.length} 条</span></div><div class="audit-category-legend"><span><b>发现来源</b> 表示哪个 Skill 找到了该链接</span><span><b>右侧六列</b> 表示访问网页后复核出的信息类别</span><span><b>数字</b> 已提取并校验的字段数</span><span><b>✓</b> 识别到该类信息</span><span><b>—</b> 未识别</span></div>${auditLinkRows(rows,group)}`;
  }
  function bindAuditLinkFilters(root){
    root.querySelectorAll('[data-audit-link-filter]').forEach(input=>{
      input.addEventListener('input',()=>{
        const group=input.dataset.auditLinkFilter;
        const needle=input.value.trim().toLowerCase();
        const list=root.querySelector(`[data-audit-link-list="${CSS.escape(group)}"]`);
        if(!list)return;
        let visible=0;
        list.querySelectorAll('[data-audit-link-row]').forEach(row=>{
          const matched=!needle||row.dataset.auditLinkSearch.includes(needle);
          row.hidden=!matched;if(matched)visible+=1;
        });
        const count=root.querySelector(`[data-audit-link-count="${CSS.escape(group)}"]`);
        if(count)count.textContent=String(visible);
      });
    });
  }
  function auditSearchDetails(d){
    const results=Array.isArray(d.search_results)?d.search_results:[];
    const plan=Array.isArray(d.search_plan)&&d.search_plan.length?d.search_plan:(d.queries||[]).map(query=>({query}));
    const byQuery=new Map();
    for(const result of results){
      const query=String(result.query||'').trim();
      if(!byQuery.has(query))byQuery.set(query,[]);
      byQuery.get(query).push(result);
    }
    const known=new Set(plan.map(item=>String(item.query||'').trim()));
    for(const query of byQuery.keys())if(query&&!known.has(query))plan.push({query});
    if(!plan.length)return '<div class="run-note">本轮没有保存搜索词明细。</div>';
    const statusText={selected:'进入候选',filtered:'未入选',unsafe:'安全过滤'};
    return `<div class="audit-search-summary">每条搜索词都标明生成它的目标 Skill、目标芯片和构词策略；展开后，每条 URL 也会保留这组发现来源。</div><div class="audit-search-queries">${plan.map((item,index)=>{
      const query=String(item.query||'').trim(),rows=(byQuery.get(query)||[]).sort((a,b)=>num(a.rank)-num(b.rank));
      const skill=item.skill||rows[0]?.skill||'',chip=item.chip||rows[0]?.chip||'',strategy=item.strategy||item.query_strategy||rows[0]?.strategy||rows[0]?.query_strategy||'';
      return `<details class="audit-search-query"><summary><span class="audit-search-index">${index+1}</span><span class="audit-search-query-main"><b>${txt(query||'未记录搜索词')}</b><span class="audit-query-context"><span><em>目标 Skill</em>${auditSkillBadges(skill)}</span>${chip?`<span><em>目标芯片</em><b>${txt(chip)}</b></span>`:''}${strategy?`<span><em>构词策略</em><b>${txt(strategy)}</b></span>`:''}</span></span><strong>${rows.length} 条结果</strong></summary><div class="audit-search-results">${rows.length?rows.map((row,resultIndex)=>{
        const reasons=Array.isArray(row.coarse_reasons)?row.coarse_reasons.join('；'):row.coarse_reason||'';
        const state=statusText[row.status]||row.status||'未判定';
        const resultSkill=row.skill||skill;
        const provider=row.provider||'搜索来源未记录';
        const providerRank=num(row.rank)?` · 原始排名 #${num(row.rank)}`:'';
        return `<article class="audit-search-result"><span class="audit-search-rank">#${resultIndex+1}</span><div><b>${txt(row.title||row.url||'未命名结果')}</b><div class="run-links">${link(row.url)}</div><div class="audit-search-result-origin"><span>发现来源</span>${auditSkillBadges(resultSkill)}</div>${row.snippet?`<p>${txt(row.snippet)}</p>`:''}<small>${txt(provider)}${txt(providerRank)} · ${txt(state)} · 粗筛 ${num(row.coarse_score)} 分${reasons?' · '+txt(reasons):''}</small></div></article>`;
      }).join(''):'<div class="run-note">本次搜索没有返回结果，或旧记录未保存结果明细。</div>'}</div></details>`;
    }).join('')}</div>`;
  }
  function renderAuditRun(d){
    const report=d.audit_report||{},existing=report.existing||{},fresh=report.new||{},outcomes=report.outcomes||{};
    const urls=d.urls||[],assets=d.url_assets||[];
    const existingAssets=mergeAuditLinkAssets(assets.filter(a=>a.search_provider==='link_library'||(a.query_strategy||'')==='已有资产复查'));
    const discoverySkillsByUrl=new Map();
    for(const result of (d.search_results||[])){
      if(result.status!=='selected'||!result.skill)continue;
      const key=auditUrlKey(result.url);
      if(!key)continue;
      if(!discoverySkillsByUrl.has(key))discoverySkillsByUrl.set(key,new Set());
      discoverySkillsByUrl.get(key).add(result.skill);
    }
    const newAssets=assets.filter(a=>a.search_provider!=='link_library'&&(a.query_strategy||'')!=='已有资产复查').map(asset=>({...asset,discovery_skills:[...new Set([...(asset.discovery_skills||[]),...Array.from(discoverySkillsByUrl.get(auditUrlKey(asset.url))||[]),asset.skill].filter(Boolean))]}));
    const uniqueNew=mergeAuditLinkAssets(newAssets);
    const issueCount=Math.max(0,num(existing.safe_unique_urls)-num(existing.reachable))+
      Math.max(0,num(fresh.visited_unique_urls)-num(fresh.reachable));
    const summaryTiles=[
      ['链接库记录',num(existing.database_rows),'其中模型链接 '+num(existing.model_links_skipped)+' 条未纳入'],
      ['已有链接可用',`${num(existing.reachable)} / ${num(existing.safe_unique_urls)}`,'完成安全校验后的去重链接'],
      ['开放网络搜索',num(fresh.queries)+' 次','返回 '+num(fresh.search_mentions)+' 条搜索结果'],
      ['新增网址可用',`${num(fresh.reachable)} / ${num(fresh.visited_unique_urls)}`,'去重后实际访问'],
      ['访问问题',issueCount,'已有链接与新增网址合计'],
      ['正式数据库',d.formal_database_modified?'有变化':'未变化',d.formal_database_modified?'需要立即核验':'本轮只记录检查结果'],
    ].map(([label,val,note])=>`<div class="audit-metric"><span>${txt(label)}</span><strong>${txt(val)}</strong><small>${txt(note)}</small></div>`).join('');
    const stages=[
      ['1','整理已有链接',`${num(existing.database_rows)} 条记录，排除 ${num(existing.model_links_skipped)} 条模型链接`,'完成'],
      ['2','检查已有链接',`${num(existing.safe_unique_urls)} 个去重网址，${num(existing.reachable)} 个可用`,'完成'],
      ['3','搜索新网址',`${num(fresh.queries)} 次搜索，得到 ${num(fresh.search_mentions)} 条结果`,'完成'],
      ['4','粗筛并访问',`${num(fresh.selected_unique_skill_urls)} 条 Skill 候选，${num(fresh.visited_unique_urls)} 个唯一网址`,'完成'],
      ['5','形成最终汇总','汇总可用链接、候选网址与访问问题','完成'],
    ].map(([n,title,note,status])=>`<div class="audit-step"><span class="audit-step-no">${n}</span><div><b>${txt(title)}</b><p>${txt(note)}</p></div><em>${txt(status)}</em></div>`).join('');
    return `<div class="audit-overview"><div class="audit-metrics">${summaryTiles}</div><div class="audit-note">本页只保留本轮全部链接检查的最终汇总。</div><div class="audit-steps">${stages}</div></div>`+
      section('audit-search-results',`搜索新网址明细 · ${num(fresh.queries)} 次 / ${num(fresh.search_mentions)} 条结果`,auditSearchDetails(d))+
      section('audit-existing',`已有链接明细 · ${existingAssets.length} 条（${num(existing.reachable)} 条可用）`,auditLinkGroup('existing','已有链接',existingAssets))+
      section('audit-new',`开放网络新增链接 · ${uniqueNew.length} 条（${num(fresh.reachable)} 条可用）`,auditLinkGroup('new','新增链接',uniqueNew),true)+
      `<div class="run-note">抓取结果：可用 ${num(outcomes.new)} · 等待重试 ${num(outcomes.retry_wait)} · 失败 ${num(outcomes.failed)}</div>`;
  }
  async function fillOpenWeb(card){
    const body=card.querySelector('.run-card-body');if(body.dataset.loaded)return;
    const d=await fetchJSON(`${API}/data-agent/open-web-runs/${encodeURIComponent(card.dataset.openWebSession)}`);
    if(d._error){body.innerHTML=`<div class="error">${txt(d._error)}</div>`;return}
    if(d.skill==='all-skills'&&d.audit_report){body.innerHTML=renderAuditRun(d);bindAuditLinkFilters(body);body.dataset.loaded='1';return}
    const assetByUrl=new Map((d.url_assets||[]).map(a=>[a.url,a]));
    const urls=(d.urls||[]).map(u=>{const a=assetByUrl.get(u.canonical_url)||{};const categories=(a.information_categories||[]).join('、')||'未确认';const fields=(a.extracted_fields||[]).join('、')||'无';return `<div class="run-source"><b>${txt(u.title||u.canonical_url)}</b> · ${badge(u.fetch_status||'not_run')}<div class="run-links">${link(u.canonical_url)}</div><div class="run-note">${txt(a.asset_status||'尚未形成资产记录')} · 信息类别：${txt(categories)} · 提取字段：${txt(fields)}<br>发现方式：${txt(a.query_strategy||'未记录')} · 粗筛 ${Number(u.coarse_score||0)} 分 · ${txt(u.coarse_reason||'')}${u.precise_reason?'<br>'+txt(u.precise_reason):''}</div></div>`}).join('')||'<div class="run-note">没有通过粗筛的网页。</div>';
    const facts=(d.facts||[]).map(f=>`<div class="run-source"><b>${txt(f.chip_model||'未确认芯片')} · ${txt(f.field_name)}</b> = ${txt(f.proposed_value)} ${txt(f.unit||'')}<div class="run-links">${link(f.source_url)}</div><div class="run-note">${txt(f.validation_status==='validated'?'校验通过':'未通过：'+(f.rejection_reason||'原因未记录'))}</div><div class="run-value">原文：${txt(f.evidence_text)}</div></div>`).join('')||'<div class="run-note">本次没有校验通过的信息。</div>';
    const events=(d.events||[]).map(e=>`<div><span class="run-note">${txt(formatUpdateTime(e.time))} · ${txt(e.stage)}</span><br>${badge(e.status)} ${txt(e.message)}</div>`).join('');
    const details=`<div class="run-detail-block"><h4>URL 资产与访问记录 · ${d.urls?.length||0} 条</h4>${urls}</div>`+
      `<div class="run-detail-block"><h4>执行时间线 · ${d.events?.length||0} 条</h4><div class="run-timeline">${events||'<div class="run-note">本次运行没有时间线记录。</div>'}</div></div>`+
      `<div class="run-detail-block"><h4>技术参数</h4><pre class="run-json">${txt(JSON.stringify({search_provider:d.search_provider,extractor_model:d.extractor_model,target_fields:d.target_fields,queries:d.queries,schema_version:d.schema_version},null,2))}</pre></div>`;
    body.innerHTML=`${section('ow-facts',`字段提取与校验 · ${Number(d.counts?.validated||0)} 条`,facts,true)}${section('ow-details','详细记录',details)}`;
    body.dataset.loaded='1';
  }
  async function loadOpenWebHistory(){
    let box=$('open-web-test-history');
    if(!box){box=document.createElement('div');box.id='open-web-test-history';$('run-history')?.before(box)}
    const data=await fetchJSON(`${API}/data-agent/open-web-runs?final_only=true`);
    if(data._error){box.innerHTML='';return}
    const runs=data.runs||[];if(!runs.length){box.innerHTML='';return}
    if(!box.querySelector('.open-web-history-list'))box.innerHTML='<div class="run-history-header"><div><h3 style="margin:0">最新检查结果</h3></div></div><div class="open-web-history-list"></div>';
    const list=box.querySelector('.open-web-history-list'),sessions=new Set(runs.map(r=>r.session_id));
    [...list.children].forEach(card=>{if(!sessions.has(card.dataset.openWebSession))card.remove()});
    for(const [i,run] of runs.entries()){
      let card=[...list.children].find(c=>c.dataset.openWebSession===run.session_id);
      if(!card){
        const wrap=document.createElement('div');wrap.innerHTML=openWebCard(run);card=wrap.firstElementChild;
        const saved=state.restore?.openWeb?.find(x=>x.session===run.session_id);if(saved)card.open=saved.open;
        card.addEventListener('toggle',()=>{if(card.open)fillOpenWeb(card)});list.append(card);
      }
      if(list.children[i]!==card)list.insertBefore(card,list.children[i]||null);
      card.classList.toggle('audit-run',run.skill==='all-skills'&&Boolean(run.audit_summary));
      card.querySelector(':scope > summary').innerHTML=openWebSummary(run);
      if(card.open)fillOpenWeb(card);
    }
  }
  window.loadRunHistory=async function({silent=false}={}){
    clearTimeout(state.timer);const mode=dataAgentMode();const generation=++state.generation;
    let root=$('run-history');if(!root){root=document.createElement('div');root.id='run-history';$('source-updates-list').before(root)}
    if(mode!==state.mode){const initial=state.mode===null;state.mode=mode;state.offset=0;root.innerHTML='';if(initial){try{const saved=JSON.parse(sessionStorage.getItem(storageKey));if(saved?.mode===mode){state.restore=saved;state.offset=saved.offset||0}}catch(_){}}}
    await loadOpenWebHistory();
    const data=await fetchJSON(`${API}/data-agent/runs?mode=${mode}&limit=${state.limit}&offset=${state.offset}`);
    if(generation!==state.generation)return;
    if(data._error){if(!silent)root.innerHTML=`<div class="error">${txt(data._error)}</div>`;return}
    if(!data.runs.length){root.innerHTML='';return}
    if(!root.querySelector('.run-history-list'))root.innerHTML='<details class="run-history-archive"><summary class="run-history-archive-summary"></summary><div class="run-history-list"></div><div class="run-history-pages run-toolbar"></div></details>';
    root.querySelector('.run-history-archive-summary').innerHTML=`历史运行 <span class="run-note">${Number(data.total)} 次</span>`;
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
    const pages=root.querySelector('.run-history-pages');pages.innerHTML=`<button class="btn secondary" data-prev ${state.offset===0?'disabled':''}>上一页</button><span class="run-note">${data.total?state.offset+1:0}–${Math.min(state.offset+state.limit,data.total)} / ${data.total}</span><button class="btn secondary" data-next ${state.offset+state.limit>=data.total?'disabled':''}>下一页</button>${data.warnings.length?`<span class="run-note">${data.warnings.length} 个旧会话不可读取</span>`:''}`;
    pages.querySelector('[data-prev]').onclick=()=>{state.offset=Math.max(0,state.offset-state.limit);list.innerHTML='';loadRunHistory()};pages.querySelector('[data-next]').onclick=()=>{state.offset+=state.limit;list.innerHTML='';loadRunHistory()};
    await Promise.all(promises);
    if(state.restore){const y=state.restore.scrollY;state.restore=null;window.scrollTo(0,y||0)}
    if(generation===state.generation)state.timer=setTimeout(()=>{if($('tab-status').classList.contains('active'))loadRunHistory({silent:true})},15000);
  };
})();
