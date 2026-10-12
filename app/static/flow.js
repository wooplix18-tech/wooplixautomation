/* Dependency canvas backed only by persisted execution state; no simulated progress. */
(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pretty = value => JSON.stringify(value, null, 2);
  const labels = {client_requirements:'Understand your brief',proposal:'Prepare your proposal',proposal_review:'Your team reviews the proposal',brd:'Write the BRD',ui_ux:'Plan the user experience',implementation:'Plan how to build it',qa_uat:'Check the requirements',documentation:'Prepare guides and training',delivery_review:'Review the delivery',uat:'Client acceptance',handover:'Prepare handover',executive_summary:'Summary',in_scope:'What’s included',out_of_scope:'Not included',delivery_approach:'How the work will happen',commercials:'Separate costs',training_plan:'Training plan',open_decisions:'Questions to confirm',acceptance_criteria:'How we’ll know it works',functional_requirements:'What the system must do',non_functional_requirements:'Quality and operating needs',personas:'People using the system',business_rules:'Business rules',assumptions:'Assumptions',risks:'Risks',support:'Support after launch',objectives:'Goals',deliverables:'Documents and outputs',timeline:'Timeline',requirements:'Requirements',next_step:'Next step'};
  const human = value => labels[value] || String(value ?? '').replaceAll('_',' ').replace(/^./, c=>c.toUpperCase());
  const statusText = value => ({pending:'Not started',running:'In progress',completed:'Done',skipped:'Not needed yet',waiting:'Needs your review',failed:'Needs attention',cancelled:'Stopped',queued:'Ready',pack_ready:'Ready for review',proposal_review:'Team review',delivery_review:'Team review',uat_failed:'Changes requested'})[value] || human(value);
  const hiddenFields = new Set(['id','generated_by','generated_at','integration','shared_requirement_links','agent_audit','source_trace','trace','requirement_register','context_record_ids','status','planning_target','traditional_hours','ai_assisted_hours','human_involvement_hours']);
  function renderValue(value) {
    if(value == null || value === '') return '<span class="muted">To be confirmed</span>';
    if(Array.isArray(value)) return value.length ? `<ul class="readable-list">${value.map(item=>`<li>${item && typeof item==='object'?renderValue(item):esc(item)}</li>`).join('')}</ul>` : '<span class="muted">None listed</span>';
    if(typeof value === 'object') return `<dl class="readable-fields">${Object.entries(value).filter(([key])=>!hiddenFields.has(key)).map(([key,child])=>`<div><dt>${esc(human(key))}</dt><dd>${renderValue(child)}</dd></div>`).join('')}</dl>`;
    return esc(value).replaceAll('\n','<br>');
  }
  function renderDocument(output) {
    if(!output || typeof output!=='object' || !output.kind) return `<p class="muted">${esc(output?.message||'This step has not made a document yet.')}</p>`;
    const content=output.content||{};
    const sections=Object.entries(content).filter(([key])=>!hiddenFields.has(key));
    return `<div class="download-row"><a class="download-button" href="/api/projects/${projectId}/artifacts/${encodeURIComponent(output.kind)}/download?format=pdf">Download PDF</a><a class="download-button secondary" href="/api/projects/${projectId}/artifacts/${encodeURIComponent(output.kind)}/download?format=docx">Download Word</a></div><p class="muted small">Saved ${esc(human(output.kind))} · version ${esc(output.version||1)}. Downloads use this saved draft.</p>${sections.length?sections.map(([key,value])=>`<section class="readable-section"><h3>${esc(human(key))}</h3>${renderValue(value)}</section>`).join(''):'<p class="muted">The document is saved.</p>'}`;
  }
  const basePositions = {client_requirements:[60,180],proposal:[345,180],proposal_review:[345,305],brd:[345,465],ui_ux:[630,65],implementation:[630,295],qa_uat:[915,180],documentation:[1200,180],delivery_review:[1200,430],uat:[1485,180],handover:[1485,430]};
  const icons = {client_requirements:'↳',proposal:'✦',brd:'▤',ui_ux:'◈',implementation:'⌘',qa_uat:'✓',documentation:'▥',delivery_review:'◎',uat:'◎',handover:'↗'};
  let graph, agents = [], state = null, projectId = null, selectedNode = null, nodeData = null;
  let selectedTab = 'details', eventSource, cursor = 0, eventLog = [], autoRun = false, executing = false;
  let epoch = 0, inspectorEpoch = 0, autoTimer, pollTimer, toastTimer;
  let positions = structuredClone(basePositions), view = {x:0,y:0,scale:.75};
  try { const saved = JSON.parse(localStorage.getItem('wooplix-flow-layout')); if(saved) for(const key of Object.keys(positions)) if(Array.isArray(saved[key])&&saved[key].every(Number.isFinite)) positions[key]=saved[key]; } catch {}
  function toast(text) { $('#toast').textContent=text; $('#toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('#toast').hidden=true,6500); }
  async function api(path, options={}) {
    const response = await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
    let data;
    try { data=await response.json(); } catch { throw new Error(`Server returned ${response.status}. Inspect the run before retrying.`); }
    if(!response.ok){ const error=new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));error.status=response.status;throw error; }
    return data;
  }
  function connection(text, live=false) { $('#stream-status').textContent=text; $('#stream-status').classList.toggle('live',live); }
  function applyView() { $('#world').style.transform=`translate(${view.x}px,${view.y}px) scale(${view.scale})`;$('#zoom-value').textContent=Math.round(view.scale*100)+'%'; }
  function fit() {
    const rect=$('#viewport').getBoundingClientRect();
    const xs=Object.values(positions).map(p=>p[0]), ys=Object.values(positions).map(p=>p[1]);
    const minX=Math.min(...xs)-25,minY=Math.min(...ys)-50,width=Math.max(...xs)+250-minX,height=Math.max(...ys)+165-minY;
    view.scale=Math.min(1,(rect.width-55)/width,(rect.height-90)/height);
    view.scale=Math.max(.22,view.scale);view.x=(rect.width-width*view.scale)/2-minX*view.scale;view.y=(rect.height-height*view.scale)/2-minY*view.scale+12;applyView();
  }
  function zoom(factor,x,y) {
    const rect=$('#viewport').getBoundingClientRect();x??=rect.width/2;y??=rect.height/2;
    const next=Math.max(.2,Math.min(1.9,view.scale*factor));
    view.x=x-(x-view.x)*next/view.scale;view.y=y-(y-view.y)*next/view.scale;view.scale=next;applyView();
  }
  function drawEdges() {
    $('#edges').innerHTML=graph.edges.map(([from,to])=>{
      const a=positions[from], b=positions[to];let x1=a[0]+224,y1=a[1]+54,x2=b[0],y2=b[1]+54,path;
      if(b[0]<a[0]) {x1=a[0];x2=b[0]+224;path=`M ${x1} ${y1} C ${x1-95} ${y1},${x2+95} ${y2},${x2} ${y2}`;}
      else if(Math.abs(b[0]-a[0])<10) {x1=a[0]+112;x2=b[0]+112;y1=a[1]+106;y2=b[1];path=`M ${x1} ${y1} C ${x1} ${y1+90},${x2} ${y2-90},${x2} ${y2}`;}
      else path=`M ${x1} ${y1} C ${x1+95} ${y1},${x2-95} ${y2},${x2} ${y2}`;
      const source=state?.nodes[from]?.status, target=state?.nodes[to]?.status;
      const status=target==='running'?'running':target==='failed'?'failed':source==='skipped'?'skipped':source==='completed'?'completed':'';
      return `<path class="edge ${status}" data-target="${to}" d="${path}"><title>${esc(from)} → ${esc(to)} · click to inspect incoming data</title></path>`;
    }).join('');
    document.querySelectorAll('.edge').forEach(edge=>edge.onclick=()=>inspect(edge.dataset.target,'input'));
  }
  function renderNodes() {
    $('#nodes').innerHTML=graph.nodes.map(task=>{
      const node=state?.nodes[task.id], status=node?.status||'pending', p=positions[task.id];
      const duration=node?.duration_ms!=null?(node.duration_ms/1000).toFixed(1)+' sec':statusText(status);
      return `<button class="node ${status} ${selectedNode===task.id?'selected':''}" data-id="${task.id}" data-type="${task.type}" style="left:${p[0]}px;top:${p[1]}px" aria-label="${esc(task.label)}: ${statusText(status)}"><span class="node-title"><span class="node-icon">${icons[task.id]||'•'}</span><span><strong>${esc(task.label)}</strong><small>${task.type==='human'?'Your team reviews':'Prepared by AI'}</small></span></span><span class="node-footer"><span class="node-state"><i class="dot ${status}"></i>${statusText(status)}</span><span>${duration}</span></span></button>`;
    }).join('');
    document.querySelectorAll('.node').forEach(node=>node.addEventListener('pointerdown',event=>beginDrag(event,node.dataset.id)));
    document.querySelectorAll('.node').forEach(node=>node.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();inspect(node.dataset.id);}}));
    drawEdges();
  }
  function beginDrag(event,id=null) {
    if(event.button!==0 || event.target.closest('.zoom-controls'))return;
    event.preventDefault();event.stopPropagation();
    const x=event.clientX,y=event.clientY,origin=id?[...positions[id]]:[view.x,view.y];let moved=false;
    const target=event.currentTarget;target.setPointerCapture(event.pointerId);
    function move(e){if(Math.abs(e.clientX-x)+Math.abs(e.clientY-y)>4)moved=true;
      if(id){positions[id]=[origin[0]+(e.clientX-x)/view.scale,origin[1]+(e.clientY-y)/view.scale];const node=document.querySelector(`.node[data-id="${id}"]`);if(node){node.style.left=positions[id][0]+'px';node.style.top=positions[id][1]+'px';}drawEdges();}
      else{view.x=origin[0]+e.clientX-x;view.y=origin[1]+e.clientY-y;applyView();$('#viewport').classList.add('panning');}}
    function up(){target.removeEventListener('pointermove',move);target.removeEventListener('pointerup',up);target.removeEventListener('pointercancel',up);$('#viewport').classList.remove('panning');if(id){try{localStorage.setItem('wooplix-flow-layout',JSON.stringify(positions));}catch{}if(!moved)inspect(id);}}
    target.addEventListener('pointermove',move);target.addEventListener('pointerup',up);target.addEventListener('pointercancel',up);
  }
  function renderLog(events) {
    const known=new Set(eventLog.map(e=>e.sequence));eventLog.push(...events.filter(e=>!known.has(e.sequence)));eventLog=eventLog.slice(-250);
    const el=$('#event-log'),atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<40;
    el.innerHTML=eventLog.length?eventLog.map(event=>`<div class="log-row ${esc(event.kind)}"><time>${new Date(event.timestamp).toLocaleTimeString([],{hour12:false})}</time><span class="log-node">${esc(human(event.node_id))}</span><span>${esc(event.message)}</span></div>`).join(''):'<div class="log-empty">No activity yet.</div>';
    if(atBottom)el.scrollTop=el.scrollHeight;
  }
  function updateControls() {
    const canRun=state?.status==='queued'&&!state.lease_active&&!state.active_gate;
    $('#execute-run').disabled=!!state&&(!canRun||executing);
    $('#execute-run').textContent=executing||autoRun?'Working…':'Continue';
    $('#step-run').disabled=!canRun||executing;
    $('#retry-run').hidden=!(state&&state.retryable&&(state.status==='failed'||state.status==='cancelled'||state.recoverable));
    $('#retry-run').disabled=executing;
    $('#stop-run').hidden=!state||['cancelled','completed','pack_ready'].includes(state.status);
    $('#mode-label').textContent=state?(state.mode==='automatic'?'Draft pack · no approval pauses':'Your team can review before the BRD'):'Your team can review the proposal before the BRD';
  }
  function applySnapshot(data) {
    state=data;cursor=data.cursor;$('#run-name').textContent=data.name;$('#run-status').textContent=statusText(data.status);
    renderNodes();renderLog(data.events||[]);updateControls();
    $('#execution-hint').textContent=data.lease_active?'The AI is preparing the next part of your request.':data.active_gate?'Your team has a decision to make.':data.status==='queued'?'Ready to continue.':data.status==='pack_ready'?'Draft documents are ready for review.':'Your progress is saved.';
    const notice=$('#notice');
    if(data.recoverable){notice.textContent='The last run paused unexpectedly. Choose “Retry unfinished” to continue; saved work is kept.';notice.hidden=false;}
    else if(data.active_gate){notice.textContent='Your team needs to review this step. Select the amber card to see the proposal and choose what happens next.';notice.hidden=false;}
    else notice.hidden=true;
    if(selectedNode&&!$('#gate-feedback')?.matches(':focus')) refreshInspector();
  }
  function watch() {
    eventSource?.close();clearTimeout(pollTimer);if(!projectId)return;
    const generation=epoch;
    eventSource=new EventSource(`/api/projects/${projectId}/events?after=${cursor}`);
    eventSource.onopen=()=>{if(generation===epoch)connection('Live events',true);};
    eventSource.addEventListener('snapshot',event=>{if(generation===epoch)applySnapshot(JSON.parse(event.data));});
    eventSource.addEventListener('idle',()=>{if(generation!==epoch)return;eventSource.close();connection(state?.active_gate?'Waiting for review':'State saved');refreshRuns();if(state?.active_gate)pollIdle(generation);});
    eventSource.addEventListener('heartbeat',event=>{if(generation!==epoch)return;const data=JSON.parse(event.data);if(state&&data.recoverable){state.recoverable=true;state.lease_active=false;updateControls();$('#notice').textContent='Execution was interrupted. Retry unfinished nodes to recover.';$('#notice').hidden=false;}});
    eventSource.onerror=()=>{if(generation===epoch)connection('Reconnecting…');};
    eventSource.addEventListener('unavailable',()=>connection('Reconnecting…'));
  }
  function pollIdle(generation) {
    pollTimer=setTimeout(async()=>{
      if(generation!==epoch||!projectId)return;
      try{const next=await api(`/projects/${projectId}/flow?after=${cursor}`);if(generation!==epoch)return;
        if(next.updated_at!==state.updated_at){applySnapshot(next);watch();}
        else pollIdle(generation);
      }catch{connection('Reconnecting…');pollIdle(generation);}
    },3000);
  }
  async function refreshRuns() {
    const {projects}=await api('/projects');
    $('#runs').innerHTML=projects.length?projects.map(project=>`<button class="run-link ${project.id===projectId?'active':''}" data-project="${project.id}" data-live="${!!project.live}"><strong>${esc(project.project_name)}</strong><small>${esc(statusText(project.status))}</small></button>`).join(''):'<p class="muted small">Your requests will appear here.</p>';
    document.querySelectorAll('.run-link').forEach(button=>button.onclick=()=>{if(button.dataset.live==='true')openRun(button.dataset.project);else window.location.href='/?project='+button.dataset.project;});
  }
  async function openRun(id,start=false) {
    epoch++;autoRun=false;executing=false;clearTimeout(autoTimer);clearTimeout(pollTimer);eventSource?.close();cursor=0;eventLog=[];projectId=id;selectedNode=null;nodeData=null;
    $('#node-heading').innerHTML='<div class="empty-symbol">⌘</div><h2>Follow the data</h2><p class="muted">Select a node to inspect its role, captured input, saved output, and execution events.</p>';
    $('#inspect-tabs').hidden=true;$('#inspect-body').innerHTML='';
    const generation=epoch;history.replaceState({},'',`/workflow?project=${encodeURIComponent(id)}`);
    try{const data=await api(`/projects/${id}/flow`);if(generation!==epoch)return;applySnapshot(data);watch();refreshRuns();if(start)execute(true);}
    catch(error){toast(error.message);connection('Unable to load');}
  }
  async function execute(continuous=true) {
    if(!projectId){$('#create-dialog').showModal();return;}
    if(executing||!state||state.status!=='queued')return;
    autoRun=continuous;executing=true;const id=projectId,generation=epoch;updateControls();watch();
    try {
      const data=await api(`/projects/${id}/execute`,{method:'POST'});
      if(generation!==epoch)return;applySnapshot(data);
      if(continuous&&data.status==='queued')autoTimer=setTimeout(()=>execute(true),120);
      else {autoRun=false;refreshRuns();}
    } catch(error) {
      if(generation!==epoch)return;autoRun=false;toast(error.message);
      // A lost POST response does not mean the worker failed. Observe persisted
      // state before allowing another claim; never auto-retry provider work.
      try {applySnapshot(await api(`/projects/${id}/flow?after=${cursor}`));}catch{}
    } finally {if(generation===epoch){executing=false;updateControls();}}
  }
  async function control(action) {
    const id=projectId,generation=epoch;if(!id)return;autoRun=false;clearTimeout(autoTimer);
    try {const data=await api(`/projects/${id}/${action}`,{method:'POST'});if(generation!==epoch)return;applySnapshot(data);watch();if(action==='retry'){executing=false;execute(true);}}
    catch(error){toast(error.message);}
  }
  async function inspect(id,tab='details') {
    selectedNode=id;selectedTab=tab;$('#inspector').classList.remove('mobile-hidden');renderNodes();
    await refreshInspector();
  }
  async function refreshInspector() {
    if(!selectedNode)return;const id=selectedNode,generation=++inspectorEpoch,run=projectId;
    try{const data=run?await api(`/projects/${run}/nodes/${id}`):{definition:graph.nodes.find(n=>n.id===id),execution:{status:'pending',attempt:0},history:[],events:[]};
      if(generation!==inspectorEpoch||run!==projectId||id!==selectedNode)return;nodeData=data;renderInspector();}
    catch(error){toast(error.message);}
  }
  function renderInspector() {
    if(!nodeData)return;const {definition,execution,events,history:attempts}=nodeData;
    const agent=agents.find(a=>a.id===definition.agent_id);
    $('#node-heading').innerHTML=`<span class="pill">${esc(statusText(execution.status))}</span><h2>${esc(definition.label)}</h2><p class="muted">${esc(definition.responsibility)}</p>`;
    $('#inspect-tabs').hidden=false;document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===selectedTab));
    const body=$('#inspect-body');
    if(selectedTab==='input')body.innerHTML=`<section class="readable-section"><h3>The original request</h3>${renderValue(execution.input_data?.project?.brief||execution.input_data?.requirements||'Input appears when this step starts.')}</section>${execution.input_data?.revision_feedback?`<section class="readable-section"><h3>Changes requested</h3>${renderValue(execution.input_data.revision_feedback)}</section>`:''}`;
    else if(selectedTab==='output')body.innerHTML=renderDocument(execution.output_data)+ (attempts?.length?`<details class="history-details"><summary>See earlier drafts</summary>${attempts.map(a=>`<section class="readable-section"><h3>Earlier version ${esc(a.attempt)}</h3>${renderDocument(a.output_data)}</section>`).join('')}</details>`:'');
    else if(selectedTab==='events')body.innerHTML=(events||[]).map(e=>`<div class="subtask"><strong>${esc(e.data?.subagent||human(e.kind))}</strong><small>${new Date(e.timestamp).toLocaleTimeString()}</small><p>${esc(e.message)}</p></div>`).join('')||'<p class="muted">No activity yet.</p>';
    else {
      const subagents=new Map();for(const e of events||[])if(e.data?.subagent)subagents.set(e.data.subagent,e.kind);
      body.innerHTML=`<section class="readable-section"><h3>What this step does</h3><p>${esc(definition.responsibility)}</p></section>${execution.duration_ms!=null?`<p class="muted small">Finished in ${(execution.duration_ms/1000).toFixed(1)} seconds.</p>`:''}${definition.dependencies.length?`<section class="readable-section"><h3>Based on</h3><p>${definition.dependencies.map(p=>`<button class="upstream" data-node="${p}">${esc(labels[p]||human(p))}</button>`).join(' ')}</p></section>`:''}${execution.error?`<p class="error">${esc(execution.error)}</p>`:''}${subagents.size?`<details><summary>AI work details</summary>${[...subagents].map(([name,kind])=>`<div class="meta-row"><span>${esc(human(name))}</span><span>${kind==='subagent_completed'?'Done':'In progress'}</span></div>`).join('')}</details>`:''}`;
      body.querySelectorAll('.upstream').forEach(b=>b.onclick=()=>inspect(b.dataset.node,'output'));
    const gate=state?.active_gate;
       if(gate&&gate.payload.node_id===definition.id){
         const requirementsGate=gate.stage==='requirements_confirmation';
         const proposalGate=gate.stage==='proposal_confirmation';
          body.insertAdjacentHTML('beforeend',`<form class="gate-form"><h3>${requirementsGate?'Confirm the request':gate.stage==='uat'?'Accept the result':proposalGate?'Review the proposal':'Review the delivery'}</h3><p class="muted">${esc(gate.description)}</p>${gate.questions.map(q=>`<p>${esc(q)}</p>`).join('')}<label>Notes for the team<textarea id="gate-feedback" name="feedback" rows="3" placeholder="Add changes or context if needed"></textarea></label><div class="controls"><button class="primary" name="decision" value="${gate.stage==='uat'?'pass':'approve'}">${gate.stage==='uat'?'Accept':requirementsGate?'Approve requirements':proposalGate?'Approve proposal':'Approve delivery'}</button>${proposalGate?'<button class="primary" name="decision" value="approve" data-request-brd="true">Approve &amp; create BRD</button>':''}${gate.stage==='uat'?'<button name="decision" value="fail">Needs changes</button>':''}<button name="decision" value="request_changes">Request edits</button><button name="decision" value="reject">Stop this request</button></div></form>`);
        body.querySelector('form').onsubmit=async event=>{event.preventDefault();const form=event.target,submitter=event.submitter,decision=submitter.value,feedback=new FormData(form).get('feedback'),id=projectId,generation=epoch;form.querySelectorAll('button').forEach(b=>b.disabled=true);
          try{await api(`/projects/${id}/gates/${gate.id}/respond`,{method:'POST',body:JSON.stringify({decision,feedback,request_brd:submitter.dataset.requestBrd==='true'})});if(generation!==epoch)return;applySnapshot(await api(`/projects/${id}/flow?after=${cursor}`));watch();if(state.status==='queued')execute(true);}
          catch(error){toast(error.message);form.querySelectorAll('button').forEach(b=>b.disabled=false);}};
      }
    }
  }
  $('#new-run').onclick=()=>$('#create-dialog').showModal();$('#close-dialog').onclick=()=>$('#create-dialog').close();
  $('#refresh-runs').onclick=()=>refreshRuns().catch(e=>toast(e.message));
  $('#execute-run').onclick=()=>execute(true);$('#step-run').onclick=()=>execute(false);
  $('#retry-run').onclick=()=>control('retry');$('#stop-run').onclick=()=>control('cancel');
  $('#zoom-in').onclick=()=>zoom(1.2);$('#zoom-out').onclick=()=>zoom(1/1.2);$('#fit').onclick=fit;
  $('#reset-layout').onclick=()=>{positions=structuredClone(basePositions);try{localStorage.removeItem('wooplix-flow-layout');}catch{}renderNodes();fit();};
  $('#viewport').onpointerdown=event=>{if(!event.target.closest('.node,.edge,.zoom-controls'))beginDrag(event);};
  $('#viewport').addEventListener('wheel',event=>{event.preventDefault();const rect=event.currentTarget.getBoundingClientRect();zoom(event.deltaY<0?1.08:1/1.08,event.clientX-rect.left,event.clientY-rect.top);},{passive:false});
  $('#close-inspector').onclick=()=>{$('#inspector').classList.add('mobile-hidden');selectedNode=null;renderNodes();};
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{selectedTab=b.dataset.tab;renderInspector();});
  $('#create-flow').onsubmit=async event=>{
    event.preventDefault();const form=event.target,button=form.querySelector('[type="submit"]');button.disabled=true;$('#create-error').textContent='';
    try {const data=await api('/runs',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(form)))});$('#create-dialog').close();await openRun(data.id,true);form.reset();}
    catch(error){$('#create-error').textContent=error.message;}finally{button.disabled=false;}
  };
  $('#brief-file').onchange=async event=>{
    const file=event.target.files?.[0];if(!file)return;
    const status=$('#brief-file-status');status.textContent=`Reading ${file.name}…`;
    try{
      const form=new FormData();form.append('file',file);
      const response=await fetch('/api/intake/document',{method:'POST',body:form});
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'Could not read this document.');
      $('[name="brief"]').value=data.text;status.textContent=`Loaded ${data.filename} · ${data.characters.toLocaleString()} characters. Review before continuing.`;
    }catch(error){status.textContent=error.message;event.target.value='';}
  };
  const mobile=matchMedia('(max-width:850px)');
  if(mobile.matches)$('#inspector').classList.add('mobile-hidden');
  mobile.addEventListener('change',event=>{if(event.matches)$('#inspector').classList.add('mobile-hidden');if(graph)fit();});
  async function init(){
    const [topology,catalog]=await Promise.all([api('/live/topology'),api('/agents')]);graph=topology;agents=catalog.agents;renderNodes();fit();
    $('#provider-status').textContent=catalog.ai_provider_configured?'AI document drafting ready':'Sample templates · AI key not connected';
    refreshRuns().catch(e=>toast(e.message));
    api('/health').then(health=>$('#storage-status').textContent=health.durable_storage?`● ${health.storage_backend} · persistent`:'○ Ephemeral demo · configure Neon for live runs').catch(()=>$('#storage-status').textContent='Database connection unavailable');
    const params=new URLSearchParams(location.search);if(params.get('project'))await openRun(params.get('project'),params.get('start')==='1');
    new ResizeObserver(()=>applyView()).observe($('#viewport'));
  }
  init().catch(error=>{toast(error.message);connection('Connection failed');});
})();
