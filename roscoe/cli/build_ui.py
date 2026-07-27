"""The single page served by ``roscoe build``.

Kept apart from the command so the Python stays readable. Plain HTML, CSS and JS —
no build step and no CDN, so the editor works offline and cannot break because a
package moved.

The canvas never invents its own idea of a graph: an edge *is* a field on a node
(``next``, ``then``, ``else``, ``on_reject``), so dragging a connection just sets
that field. The YAML stays the single source of truth and the picture cannot drift
from it.
"""

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>roscoe build</title>
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#f7f8fa;color:#1e293b;height:100vh;overflow:hidden;font-size:13px}
  .app{display:grid;grid-template-columns:170px 1fr 300px;grid-template-rows:48px 1fr;height:100vh}
  header{grid-column:1/-1;background:#eef1f6;border-bottom:1px solid #dbe1e8;
    display:flex;align-items:center;gap:10px;padding:0 16px}
  header h1{font-size:14px;font-weight:600}
  header .file{font-size:11.5px;color:#64748b;font-family:ui-monospace,monospace}
  header .sp{margin-left:auto}
  button{font:inherit;border:1px solid #cbd5e1;background:#fff;border-radius:7px;
    padding:6px 12px;cursor:pointer}
  button:hover{background:#f1f5f9}
  button.primary{background:#2563eb;color:#fff;border-color:#2563eb}
  button.primary:hover{background:#1d4ed8}
  button.danger{color:#dc2626;border-color:#fca5a5}

  .palette{background:#fff;border-right:1px solid #e2e8f0;padding:12px;overflow-y:auto}
  .palette h2{font-size:11px;text-transform:uppercase;letter-spacing:.04em;
    color:#94a3b8;margin:4px 0 8px}
  .palette button{width:100%;text-align:left;margin-bottom:6px;padding:8px 10px}
  .hint{font-size:11px;color:#94a3b8;line-height:1.6;margin-top:10px}

  .canvas{position:relative;overflow:auto;background:
    radial-gradient(#dbe1e8 1px,transparent 1px);background-size:18px 18px}
  .canvas .sheet{position:relative;width:2600px;height:1800px}
  svg.edges{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
  svg.edges path{fill:none;stroke:#94a3b8;stroke-width:1.6}
  svg.edges path.dashed{stroke-dasharray:5 4}
  svg.edges text{font-size:10px;fill:#64748b}

  .node{position:absolute;width:190px;background:#fff;border:1px solid #cbd5e1;
    border-radius:9px;box-shadow:0 1px 2px rgba(0,0,0,.05);user-select:none}
  .node.sel{border-color:#2563eb;box-shadow:0 0 0 2px #bfdbfe}
  .node.entry{border-left:4px solid #2563eb}
  .node.gated{border-left:4px solid #b45309}
  .node .hd{padding:6px 9px;font-weight:600;cursor:grab;display:flex;gap:6px;align-items:center}
  .node .hd .t{font-size:9.5px;font-weight:500;color:#64748b;background:#f1f5f9;
    padding:1px 6px;border-radius:20px}
  .node .bd{padding:0 9px 8px;font-size:11px;color:#64748b;font-family:ui-monospace,monospace;
    word-break:break-word;line-height:1.5}
  .port{position:absolute;right:-7px;width:13px;height:13px;border-radius:50%;
    background:#fff;border:2px solid #94a3b8;cursor:crosshair}
  .port:hover{border-color:#2563eb;background:#dbeafe}
  .port .lbl{position:absolute;left:16px;top:-3px;font-size:9.5px;color:#64748b;white-space:nowrap}

  .panel{background:#fff;border-left:1px solid #e2e8f0;padding:14px;overflow-y:auto}
  .panel h2{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;margin-bottom:10px}
  label{display:block;font-size:11px;color:#64748b;margin:9px 0 3px}
  input,select,textarea{width:100%;padding:6px 9px;border:1px solid #cbd5e1;
    border-radius:7px;font:inherit;background:#fff}
  textarea{min-height:70px;resize:vertical;font-family:inherit}
  input[type=checkbox]{width:auto;margin-right:6px}
  .row{display:flex;gap:6px}.row input{flex:1}
  .issues{margin-top:12px;font-size:11.5px;line-height:1.6}
  .issues div{padding:5px 8px;border-radius:6px;margin-bottom:4px}
  .issues .error{background:#fef2f2;color:#b91c1c}
  .issues .warning{background:#fffbeb;color:#92400e}
  .issues .ok{background:#f0fdf4;color:#15803d}

  header .tab{padding:5px 12px;border-radius:7px;border:1px solid transparent;background:none}
  header .tab.on{background:#fff;border-color:#cbd5e1;font-weight:600}

  .setup{grid-column:1/-1;overflow-y:auto;padding:20px;display:none}
  .setup .wrap{max-width:760px;margin:0 auto}
  .setup section{background:#fff;border:1px solid #e2e8f0;border-radius:10px;
    padding:16px 18px;margin-bottom:16px}
  .setup section > h2{font-size:13px;font-weight:600;color:#1e293b;margin-bottom:2px}
  .setup section > p{font-size:11.5px;color:#94a3b8;line-height:1.6;margin-bottom:10px}
  .setup .grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}
  .card{border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:10px;
    background:#fbfcfd}
  .card .top{display:flex;gap:8px;align-items:center;margin-bottom:2px}
  .card .top input,.card .top select{flex:1}
  .kv{display:flex;gap:6px;margin-bottom:5px}
  .kv input{flex:1}
  .kv button,.card .top button{flex:0 0 auto;padding:5px 9px}
  .tools{display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:4px}
  .tools label{display:flex;align-items:center;margin:0;font-size:11.5px;color:#475569}
</style></head><body>
<div class="app">
  <header>
    <h1>roscoe build</h1>
    <button class="tab on" id="tabFlow" onclick="tab('flow')">Flow</button>
    <button class="tab" id="tabSetup" onclick="tab('setup')">Setup</button>
    <span class="file" id="file"></span>
    <span class="sp"></span>
    <span id="flowActions">
      <button onclick="check()">Validate</button>
      <button class="primary" onclick="save()">Save workflow.yaml</button>
    </span>
    <span id="setupActions" style="display:none">
      <button class="primary" onclick="saveSetup()">Save setup</button>
    </span>
  </header>

  <div class="palette">
    <h2>Add node</h2>
    <button onclick="addNode('connector_action')">Action</button>
    <button onclick="addNode('condition')">Decision</button>
    <button onclick="addNode('llm_step')">Prompt</button>
    <button onclick="addNode('agent_step')">Agent</button>
    <h2 style="margin-top:16px">Workflow</h2>
    <label>Entry node</label>
    <select id="entry" onchange="setEntry(this.value)"></select>
    <label>Shared system prompt</label>
    <textarea id="system" style="min-height:80px" oninput="setSystem(this.value)"></textarea>
    <label>Final output</label>
    <input id="wfout" oninput="setOutput(this.value)" placeholder="{{ decision }}">
    <p class="hint">Drag a node's circle onto another node to connect them.
      Model, connectors, agents and the web page live in the Setup tab.
      Saving rewrites the file, so comments in it are lost.</p>
  </div>

  <div class="canvas" id="canvas">
    <div class="sheet" id="sheet">
      <svg class="edges" id="edges"></svg>
    </div>
  </div>

  <div class="panel" id="panel"><h2>Nothing selected</h2>
    <p class="hint">Click a node to edit it.</p></div>

  <div class="setup" id="setup"><div class="wrap">
    <section>
      <h2>Model</h2>
      <p>Which LLM the prompts and agents run on. Put secrets in .env and reference
         them as ${VAR_NAME} — they are never read or written in plain text here.</p>
      <div id="model"></div>
    </section>

    <section>
      <h2>Connectors</h2>
      <p>The systems this agent can reach. Each one's methods become choices on
         every action node.</p>
      <div id="connectors"></div>
      <button onclick="addConnector()">+ connector</button>
    </section>

    <section>
      <h2>Agents</h2>
      <p>Only needed for agent nodes — a task that needs its own judgement, like
         "one task per action item". Tick the tools it may use.</p>
      <div id="agentList"></div>
      <button onclick="addAgent()">+ agent</button>
    </section>

    <section>
      <h2>Web page</h2>
      <p>What <code>roscoe run</code> serves in the browser. Adding inputs turns the
         chat box into a form — each name is readable in the workflow as
         <code>{{ input.name }}</code>.</p>
      <div id="uiFields"></div>
      <label style="margin-top:12px">Form inputs</label>
      <div id="uiInputs"></div>
      <button onclick="addUiInput()">+ input</button>
    </section>

    <div class="issues" id="setupIssues"></div>
  </div></div>
</div>

<script>
let wf = {entry:'', nodes:[], system:'', output:''};
let layout = {}, methods = {}, agents = [], selected = null;
// Setup tab. Connectors and agents are held as arrays, not objects: a name is
// being edited keystroke by keystroke, and rekeying an object on every one of
// those loses focus and mangles order.
let model = {}, conns = [], agentsArr = [], ui = {}, uiInputs = [], connectorTypes = [];
const sheet = document.getElementById('sheet'), svg = document.getElementById('edges');

const PROVIDERS = ['openai','azure_openai','anthropic','gemini','nvidia','ollama'];
const UI_TEXT = [['title','Title'],['subtitle','Subtitle'],['heading','Heading'],
  ['intro','Intro'],['greeting','Chat greeting'],['placeholder','Chat placeholder'],
  ['submit','Button text'],['accent','Accent colour']];
const INPUT_TYPES = ['text','date','email','number','select'];

// Which field each outgoing port writes to, per node type.
function ports(n){
  if(n.type === 'condition') return [{field:'then', label:'yes'}, {field:'else', label:'no'}];
  if(n.type === 'connector_action' && (n.requires_approval || n.on_reject))
    return [{field:'next', label:'approved'}, {field:'on_reject', label:'rejected'}];
  return [{field:'next', label:''}];
}

function summary(n){
  if(n.type === 'connector_action') return ((n.connector ? n.connector+'.' : '') + (n.method||'?')) + '()';
  if(n.type === 'condition') return n.when || '?';
  if(n.type === 'agent_step') return 'agent: ' + (n.agent||'?');
  return (n.parse==='json'?'{ } ':'') + (n.prompt||'').slice(0, 56);
}
const SHORT = {connector_action:'action', condition:'decision', llm_step:'prompt', agent_step:'agent'};

async function load(){
  const d = await (await fetch('/api/workflow')).json();
  wf = d.workflow; layout = d.layout || {}; methods = d.methods || {}; agents = d.agents || [];
  adoptConfig(d);
  document.getElementById('file').textContent = d.file;
  document.getElementById('system').value = wf.system || '';
  document.getElementById('wfout').value = wf.output || '';
  wf.nodes.forEach((n, i) => { if(!layout[n.id]) layout[n.id] = {x: 80 + (i%3)*260, y: 60 + Math.floor(i/3)*150}; });
  render();
  renderSetup();
  if(d.error) showIssues([{level:'error', message:d.error}]);
}

// --- Setup tab -------------------------------------------------------------

function adoptConfig(d){
  connectorTypes = d.connector_types || [];
  const c = d.config || {};
  model = c.model || {};
  ui = c.ui || {};
  uiInputs = (ui.inputs || []).map(f => typeof f === 'string' ? {name:f} : Object.assign({}, f));
  conns = Object.entries(c.connectors || {}).map(([name, s]) => {
    const settings = Object.assign({}, s || {});
    // `type` defaults to the connector's own name, which is how a single-use
    // connector is usually written. Show that rather than an empty dropdown.
    const type = settings.type || name;
    delete settings.type;
    return {name, type, settings: Object.entries(settings).map(([k,v]) => [k, String(v)])};
  });
  agentsArr = Object.entries(d.agents_detail || {}).map(([name, spec]) => ({
    name,
    system_prompt: (spec||{}).system_prompt || '',
    tools: ((spec||{}).tools || []).map(String),
  }));
}

function tab(which){
  const setup = which === 'setup';
  document.getElementById('setup').style.display = setup ? 'block' : 'none';
  for(const id of ['palette','canvas','panel'])
    document.querySelector('.'+id).style.display = setup ? 'none' : '';
  document.getElementById('flowActions').style.display = setup ? 'none' : '';
  document.getElementById('setupActions').style.display = setup ? '' : 'none';
  document.getElementById('tabFlow').className = 'tab' + (setup ? '' : ' on');
  document.getElementById('tabSetup').className = 'tab' + (setup ? ' on' : '');
  if(setup) renderSetup();
}

function opts(list, chosen, blank){
  return (blank ? '<option value=""></option>' : '')
    + list.map(o => '<option'+(o===chosen?' selected':'')+'>'+esc(o)+'</option>').join('');
}
function txt(label, value, oninput, placeholder){
  return '<label>'+label+'</label><input value="'+esc(value||'')+'" placeholder="'
    + esc(placeholder||'') + '" oninput="'+oninput+'">';
}

function renderSetup(){
  document.getElementById('model').innerHTML =
      '<div class="grid2">'
    + '<div><label>Provider</label><select onchange="model.provider=this.value">'
      + opts(PROVIDERS, model.provider, true) + '</select></div>'
    + '<div>' + txt('Model name', model.model, 'model.model=this.value', 'gpt-4o-mini') + '</div>'
    + '<div>' + txt('API key', model.api_key, 'model.api_key=this.value', '${OPENAI_API_KEY}') + '</div>'
    + '<div><label>Temperature</label><input type="number" step="0.1" min="0" max="2" value="'
      + esc(model.temperature == null ? 0.1 : model.temperature)
      + '" oninput="model.temperature=parseFloat(this.value)"></div>'
    + '</div>';

  document.getElementById('connectors').innerHTML = conns.map((c, i) =>
      '<div class="card"><div class="top">'
    + '<input value="'+esc(c.name)+'" placeholder="name used in nodes" oninput="conns['+i+'].name=this.value">'
    + '<select onchange="conns['+i+'].type=this.value">' + opts(connectorTypes, c.type, true) + '</select>'
    + '<button class="danger" onclick="conns.splice('+i+',1);renderSetup()">remove</button>'
    + '</div><label>Settings</label>'
    + c.settings.map(([k,v], j) =>
        '<div class="kv"><input value="'+esc(k)+'" placeholder="key" oninput="conns['+i+'].settings['+j+'][0]=this.value">'
      + '<input value="'+esc(v)+'" placeholder="value or ${VAR}" oninput="conns['+i+'].settings['+j+'][1]=this.value">'
      + '<button onclick="conns['+i+'].settings.splice('+j+',1);renderSetup()">&times;</button></div>').join('')
    + '<button onclick="conns['+i+'].settings.push([\'\',\'\']);renderSetup()">+ setting</button></div>'
  ).join('') || '<p class="hint">No connectors yet.</p>';

  const toolRefs = [].concat(...Object.entries(methods).map(
    ([c, ms]) => ms.map(m => c + '.' + m)));
  document.getElementById('agentList').innerHTML = agentsArr.map((a, i) =>
      '<div class="card"><div class="top">'
    + '<input value="'+esc(a.name)+'" placeholder="agent name" oninput="agentsArr['+i+'].name=this.value">'
    + '<button class="danger" onclick="agentsArr['+i+']&&agentsArr.splice('+i+',1);renderSetup()">remove</button>'
    + '</div><label>System prompt</label>'
    + '<textarea oninput="agentsArr['+i+'].system_prompt=this.value">'+esc(a.system_prompt)+'</textarea>'
    + '<label>Tools</label>'
    + (toolRefs.length
        ? '<div class="tools">' + toolRefs.map(ref =>
            '<label><input type="checkbox"'+(a.tools.indexOf(ref)>=0?' checked':'')
            + ' onchange="toggleTool('+i+',\''+esc(ref)+'\',this.checked)">'+esc(ref)+'</label>').join('')
          + '</div>'
        // No live connector means no method list. Fall back to typing, rather
        // than showing an empty box that looks like the agent can use nothing.
        : '<input value="'+esc(a.tools.join(', '))+'" placeholder="connector.method, comma separated"'
          + ' oninput="agentsArr['+i+'].tools=this.value.split(\',\').map(s=>s.trim()).filter(Boolean)">')
    + '</div>'
  ).join('') || '<p class="hint">No agents yet — only needed for agent nodes.</p>';

  document.getElementById('uiFields').innerHTML = '<div class="grid2">' + UI_TEXT.map(([k, label]) =>
    '<div>' + txt(label, ui[k], 'ui[\''+k+'\']=this.value', k==='accent'?'#2563eb':'') + '</div>').join('')
    + '</div>';

  document.getElementById('uiInputs').innerHTML = uiInputs.map((f, i) =>
      '<div class="card"><div class="top">'
    + '<input value="'+esc(f.name)+'" placeholder="name (matches {{ input.name }})" oninput="uiInputs['+i+'].name=this.value">'
    + '<select onchange="uiInputs['+i+'].type=this.value;renderSetup()">'+opts(INPUT_TYPES, f.type||'text', false)+'</select>'
    + '<button class="danger" onclick="uiInputs.splice('+i+',1);renderSetup()">remove</button>'
    + '</div><div class="grid2">'
    + '<div>' + txt('Label', f.label, 'uiInputs['+i+'].label=this.value') + '</div>'
    + '<div>' + txt('Default', f.default, 'uiInputs['+i+'].default=this.value') + '</div>'
    + (f.type === 'select'
        ? '<div>' + txt('Options (comma separated)', (f.options||[]).join(', '),
            'uiInputs['+i+'].options=this.value.split(\',\').map(s=>s.trim()).filter(Boolean)') + '</div>'
        : '<div>' + txt('Placeholder', f.placeholder, 'uiInputs['+i+'].placeholder=this.value') + '</div>')
    + '<div><label>&nbsp;</label><label><input type="checkbox"'+(f.required?' checked':'')
      + ' onchange="uiInputs['+i+'].required=this.checked">Required</label></div>'
    + '</div></div>'
  ).join('') || '<p class="hint">No inputs — the page serves a chat box.</p>';
}

function toggleTool(i, ref, on){
  const t = agentsArr[i].tools;
  const at = t.indexOf(ref);
  if(on && at < 0) t.push(ref); else if(!on && at >= 0) t.splice(at, 1);
}
function addConnector(){ conns.push({name:'', type:'', settings:[['','']]}); renderSetup(); }
function addAgent(){ agentsArr.push({name:'', system_prompt:'', tools:[]}); renderSetup(); }
function addUiInput(){ uiInputs.push({name:'', type:'text', required:false}); renderSetup(); }

function setupPayload(){
  const connectors = {};
  for(const c of conns){
    if(!c.name) continue;
    const block = {};
    // `type` is only written when it differs from the name — that is exactly the
    // case the loader needs it for, and it keeps single-use blocks uncluttered.
    if(c.type && c.type !== c.name) block.type = c.type;
    for(const [k, v] of c.settings) if(k) block[k] = v;
    connectors[c.name] = block;
  }
  const agentBlock = {};
  for(const a of agentsArr){
    if(!a.name) continue;
    const spec = {};
    if(a.system_prompt) spec.system_prompt = a.system_prompt;
    if(a.tools.length) spec.tools = a.tools;
    agentBlock[a.name] = spec;
  }
  const uiBlock = {};
  for(const [k] of UI_TEXT) if(ui[k]) uiBlock[k] = ui[k];
  const fields = uiInputs.filter(f => f.name).map(f => {
    const out = {name: f.name};
    for(const k of ['label','type','placeholder','default']) if(f[k]) out[k] = f[k];
    if(f.type === 'select' && (f.options||[]).length) out.options = f.options;
    if(f.required) out.required = true;
    return out;
  });
  if(fields.length) uiBlock.inputs = fields;

  const m = {};
  for(const k of ['provider','model','api_key','temperature'])
    if(model[k] !== '' && model[k] != null) m[k] = model[k];
  return {model: m, connectors, ui: uiBlock, agents: agentBlock};
}

async function saveSetup(){
  const r = await (await fetch('/api/save-config', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(setupPayload())})).json();
  methods = r.methods || {}; agents = r.agents || [];
  renderSetup();   // tool checkboxes now reflect connectors that actually built
  const box = document.getElementById('setupIssues');
  box.innerHTML = (r.issues||[]).map(i =>
    '<div class="'+i.level+'">'+esc(i.message)+'</div>').join('');
}

function render(){
  document.querySelectorAll('.node').forEach(e => e.remove());
  for(const n of wf.nodes){
    const p = layout[n.id] || {x:80, y:60};
    const el = document.createElement('div');
    el.className = 'node' + (n.id===selected?' sel':'') + (n.id===wf.entry?' entry':'')
      + (n.requires_approval?' gated':'');
    el.style.left = p.x+'px'; el.style.top = p.y+'px';
    el.innerHTML = '<div class="hd"><span>'+esc(n.id)+'</span><span class="t">'+SHORT[n.type]+'</span></div>'
      + '<div class="bd">'+esc(summary(n))+'</div>';
    el.onmousedown = e => { if(!e.target.classList.contains('port')) startDrag(e, n.id); };
    el.onclick = () => { selected = n.id; render(); panel(); };
    ports(n).forEach((port, i, all) => {
      const dot = document.createElement('div');
      dot.className = 'port'; dot.style.top = (26 + i*20) + 'px';
      dot.innerHTML = all.length>1 ? '<span class="lbl">'+port.label+'</span>' : '';
      dot.onmousedown = e => startLink(e, n.id, port.field);
      el.appendChild(dot);
    });
    sheet.appendChild(el);
  }
  drawEdges(); fillEntry();
}

function drawEdges(){
  let out = '';
  for(const n of wf.nodes){
    const a = layout[n.id]; if(!a) continue;
    ports(n).forEach((port, i) => {
      let target = n[port.field];
      let dashed = false;
      if(!target && port.field === 'next'){ target = fallthrough(n); dashed = true; }
      if(!target) return;
      const x1 = a.x+190, y1 = a.y+32+i*20;
      if(target === 'END'){
        // Show termination explicitly — otherwise a node that ends the run looks
        // identical to one whose routing was forgotten.
        out += '<path d="M'+x1+','+y1+' l26,0"/>'
             + '<circle cx="'+(x1+32)+'" cy="'+y1+'" r="5" fill="#fff" stroke="#94a3b8" stroke-width="1.6"/>'
             + '<text x="'+(x1+42)+'" y="'+(y1+4)+'">end</text>';
        return;
      }
      const b = layout[target]; if(!b) return;
      const x2 = b.x, y2 = b.y+30, mx = (x1+x2)/2;
      out += '<path class="'+(dashed?'dashed':'')+'" d="M'+x1+','+y1+' C'+mx+','+y1+' '+mx+','+y2+' '+x2+','+y2+'"/>';
      if(port.label) out += '<text x="'+(x1+8)+'" y="'+(y1-4)+'">'+port.label+'</text>';
    });
  }
  svg.innerHTML = out;
}

// A node with no `next` falls through to the following node in the list.
function fallthrough(n){
  const i = wf.nodes.indexOf(n);
  return i >= 0 && i+1 < wf.nodes.length ? wf.nodes[i+1].id : null;
}

// --- dragging ---

function startDrag(e, id){
  e.preventDefault();
  const p = layout[id], sx = e.clientX, sy = e.clientY, ox = p.x, oy = p.y;
  const move = ev => { p.x = Math.max(0, ox+ev.clientX-sx); p.y = Math.max(0, oy+ev.clientY-sy); render(); };
  const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); };
  document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
}

function startLink(e, fromId, field){
  e.preventDefault(); e.stopPropagation();
  const up = ev => {
    document.removeEventListener('mouseup', up);
    const el = ev.target.closest('.node');
    const node = wf.nodes.find(n => n.id === fromId);
    if(el){
      const toId = el.querySelector('.hd span').textContent;
      if(toId !== fromId){ node[field] = toId; }
    } else {
      node[field] = 'END';   // dropped on empty canvas: end the run
    }
    render(); panel();
  };
  document.addEventListener('mouseup', up);
}

// --- property panel ---

function panel(){
  const n = wf.nodes.find(x => x.id === selected);
  const box = document.getElementById('panel');
  if(!n){ box.innerHTML = '<h2>Nothing selected</h2><p class="hint">Click a node to edit it.</p>'; return; }

  const nodeOpts = (val) => '<option value=""></option><option value="END"'+(val==='END'?' selected':'')+'>END</option>'
    + wf.nodes.filter(o=>o.id!==n.id).map(o=>'<option'+(o.id===val?' selected':'')+'>'+esc(o.id)+'</option>').join('');

  let html = '<h2>'+SHORT[n.type]+'</h2>';
  html += f('Name', '<input value="'+esc(n.id)+'" onchange="rename(this.value)">');

  if(n.type === 'connector_action'){
    const conns = Object.keys(methods);
    html += f('Connector (optional)', '<select onchange="set(\'connector\',this.value);panel()">'
      + '<option value=""></option>'
      + conns.map(c=>'<option'+(c===n.connector?' selected':'')+'>'+esc(c)+'</option>').join('')+'</select>');
    const avail = methods[n.connector] || [].concat(...Object.values(methods));
    html += f('Method', avail.length
      ? '<select onchange="set(\'method\',this.value)"><option value=""></option>'
        + avail.map(m=>'<option'+(m===n.method?' selected':'')+'>'+esc(m)+'</option>').join('')+'</select>'
      : '<input value="'+esc(n.method||'')+'" onchange="set(\'method\',this.value)">');
    html += '<label>Inputs</label>' + inputRows(n);
    html += f('Show instead of the raw result (optional)',
      '<textarea onchange="set(\'output_message\',this.value)">'+esc(n.output_message||'')+'</textarea>')
      + '<p class="hint">A connector\'s own return is API-shaped — an id, a status code.'
      + ' Fill this in to show something readable instead, e.g. "Recap sent."</p>';
    html += '<label style="margin-top:10px"><input type="checkbox"'+(n.requires_approval?' checked':'')
      + ' onchange="set(\'requires_approval\',this.checked);panel()">Needs approval</label>';
    if(n.requires_approval)
      html += f('If rejected, go to', '<select onchange="set(\'on_reject\',this.value)">'+nodeOpts(n.on_reject)+'</select>');
  } else if(n.type === 'condition'){
    html += f('When (expression)', '<input value="'+esc(n.when||'')+'" onchange="set(\'when\',this.value)">');
    html += f('Yes &rarr;', '<select onchange="set(\'then\',this.value)">'+nodeOpts(n.then)+'</select>');
    html += f('No &rarr;', '<select onchange="set(\'else\',this.value)">'+nodeOpts(n['else'])+'</select>');
  } else if(n.type === 'llm_step'){
    html += f('Prompt', '<textarea onchange="set(\'prompt\',this.value)">'+esc(n.prompt||'')+'</textarea>');
    html += f('System (overrides shared)', '<textarea onchange="set(\'system\',this.value)">'+esc(n.system||'')+'</textarea>');
    html += '<label style="margin-top:10px"><input type="checkbox"'+(n.parse==='json'?' checked':'')
      + ' onchange="set(\'parse\',this.checked?\'json\':\'\')">Parse reply as JSON</label>'
      + '<p class="hint">Read fields back with {{ '+esc(n.output||'result')+'.field }} instead of one long string.</p>';
  } else if(n.type === 'agent_step'){
    html += f('Agent', agents.length
      ? '<select onchange="set(\'agent\',this.value)"><option value=""></option>'
        + agents.map(a=>'<option'+(a===n.agent?' selected':'')+'>'+esc(a)+'</option>').join('')+'</select>'
      : '<input value="'+esc(n.agent||'')+'" onchange="set(\'agent\',this.value)">');
    html += f('Task', '<textarea onchange="set(\'task\',this.value)">'+esc(n.task||'')+'</textarea>');
  }

  html += f('Save result as', '<input value="'+esc(n.output||'')+'" onchange="set(\'output\',this.value)" placeholder="state key">');
  if(n.type !== 'condition')
    html += f('Then go to', '<select onchange="set(\'next\',this.value)">'+nodeOpts(n.next)+'</select>');
  html += '<div style="margin-top:14px"><button class="danger" onclick="removeNode()">Delete node</button></div>';
  html += '<div class="issues" id="issues"></div>';
  box.innerHTML = html;
}

function f(label, control){ return '<label>'+label+'</label>'+control; }

function inputRows(n){
  const entries = Object.entries(n.inputs || {});
  let html = '<div id="inputs">';
  entries.forEach(([k,v],i)=>{
    const val = typeof v === 'string' ? v : JSON.stringify(v);
    html += '<div class="row" style="margin-bottom:4px">'
      + '<input value="'+esc(k)+'" onchange="renameInput('+i+',this.value)" placeholder="name">'
      + '<input value="'+esc(val)+'" onchange="setInput('+i+',this.value)" placeholder="value">'
      + '</div>';
  });
  html += '</div><button style="margin-top:4px" onclick="addInput()">+ input</button>';
  return html;
}

// --- mutations ---

function node(){ return wf.nodes.find(x => x.id === selected); }
function set(field, value){
  const n = node();
  if(value === '' || value === false) delete n[field]; else n[field] = value;
  render();
}
function rename(newId){
  const n = node(), old = n.id;
  if(!newId || newId === old || wf.nodes.some(o=>o.id===newId)) { render(); return; }
  n.id = newId;
  layout[newId] = layout[old]; delete layout[old];
  // Repoint every edge that referenced the old name.
  for(const o of wf.nodes) for(const fld of ['next','then','else','on_reject'])
    if(o[fld] === old) o[fld] = newId;
  if(wf.entry === old) wf.entry = newId;
  selected = newId; render(); panel();
}
function addNode(type){
  let i = 1; while(wf.nodes.some(n=>n.id === type.split('_')[0]+'_'+i)) i++;
  const n = {id: type.split('_')[0]+'_'+i, type};
  if(type === 'condition'){ n.when = 'true'; n.then = ''; }
  if(type === 'llm_step') n.prompt = '';
  if(type === 'agent_step'){ n.agent = agents[0] || ''; n.task = ''; }
  if(type === 'connector_action') n.method = '';
  wf.nodes.push(n);
  layout[n.id] = {x: 80 + (wf.nodes.length%3)*260, y: 60 + Math.floor(wf.nodes.length/3)*150};
  if(!wf.entry) wf.entry = n.id;
  selected = n.id; render(); panel();
}
function removeNode(){
  const id = selected;
  wf.nodes = wf.nodes.filter(n => n.id !== id);
  for(const o of wf.nodes) for(const fld of ['next','then','else','on_reject'])
    if(o[fld] === id) delete o[fld];
  if(wf.entry === id) wf.entry = wf.nodes[0] ? wf.nodes[0].id : '';
  delete layout[id]; selected = null; render(); panel();
}
function addInput(){ const n = node(); n.inputs = n.inputs || {}; n.inputs[''] = ''; panel(); }
function renameInput(i, key){
  const n = node(), e = Object.entries(n.inputs); e[i][0] = key;
  n.inputs = Object.fromEntries(e.filter(([k])=>k!=='')); panel();
}
function setInput(i, raw){
  const n = node(), e = Object.entries(n.inputs);
  // A value that looks like JSON is stored as JSON, so `params: ["..."]` works.
  let v = raw;
  if(/^\s*[\[{]/.test(raw)){ try { v = JSON.parse(raw); } catch(_){} }
  e[i][1] = v; n.inputs = Object.fromEntries(e); render();
}
function setEntry(v){ wf.entry = v; render(); }
function setSystem(v){ wf.system = v; }
function setOutput(v){ wf.output = v; }
function fillEntry(){
  const sel = document.getElementById('entry');
  sel.innerHTML = wf.nodes.map(n=>'<option'+(n.id===wf.entry?' selected':'')+'>'+esc(n.id)+'</option>').join('');
}

// --- server ---

function payload(){
  // Carry through everything the file had, not just what the canvas draws — a
  // setting the editor forgets is a setting the next save deletes.
  const w = Object.assign({}, wf, {entry: wf.entry, nodes: wf.nodes});
  for(const k of ['system','output']) if(!w[k]) delete w[k];
  return w;
}
async function check(){
  const r = await (await fetch('/api/validate', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({workflow: payload()})})).json();
  showIssues(r.issues, r.ok ? 'No problems found.' : null);
  return r.ok;
}
async function save(){
  const r = await (await fetch('/api/save', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({workflow: payload(), layout})})).json();
  showIssues(r.issues, r.saved ? 'Saved ' + r.file : null);
}
function showIssues(issues, okMsg){
  let box = document.getElementById('issues');
  if(!box){ panel(); box = document.getElementById('issues'); }
  if(!box) return;
  let html = okMsg ? '<div class="ok">'+esc(okMsg)+'</div>' : '';
  html += (issues||[]).map(i =>
    '<div class="'+i.level+'">'+(i.node?'['+esc(i.node)+'] ':'')+esc(i.message)+'</div>').join('');
  box.innerHTML = html;
}

function esc(s){ const d = document.createElement('div'); d.textContent = s==null?'':s; return d.innerHTML; }

load();
</script>
</body></html>"""
