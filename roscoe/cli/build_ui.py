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
  .canvas .sheet{position:relative;width:2600px;height:1800px;
    transform-origin:0 0;transition:transform .12s ease-out}
  .zoombar{position:absolute;right:14px;bottom:14px;display:flex;gap:4px;z-index:20}
  .zoombar button{padding:4px 9px;background:#fff;box-shadow:0 1px 3px rgba(15,23,42,.12)}
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
  /* The dot is 14px, but the hit area is bigger (via padding + background-clip)
     so grabbing one doesn't take pixel-precision. */
  .port{position:absolute;right:-11px;top:12px;width:22px;height:22px;
    display:flex;align-items:center;justify-content:center;cursor:crosshair}
  .port::after{content:'';width:14px;height:14px;border-radius:50%;
    background:#fff;border:2px solid #94a3b8;pointer-events:none}
  .port:hover::after{border-color:#2563eb;background:#dbeafe;transform:scale(1.15)}
  .port .lbl{position:absolute;left:20px;top:2px;font-size:9.5px;color:#64748b;white-space:nowrap}
  /* while a connection is being dragged, every other node dims except the one
     currently under the cursor — that's the "drop here" affordance */
  .canvas.linking .node{opacity:.45;transition:opacity .1s}
  .canvas.linking .node.drop-target{opacity:1;border-color:#2563eb;box-shadow:0 0 0 3px #bfdbfe}
  #liveLink{stroke:#2563eb;stroke-width:2;fill:none;stroke-dasharray:5 4;pointer-events:none}

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

  /* Validate/Save feedback. Lives outside the property panel on purpose: the
     panel only exists while a node is selected, so anything rendered into it
     is invisible the rest of the time — which silently swallowed every save
     confirmation and every validation error. */
  .toast{position:fixed;right:18px;bottom:18px;max-width:400px;z-index:50;
    display:none;flex-direction:column;gap:6px}
  .toast.show{display:flex}
  .toast div{padding:9px 12px;border-radius:8px;font-size:11.5px;line-height:1.5;
    box-shadow:0 4px 14px rgba(15,23,42,.14);cursor:pointer}
  .toast .error{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca}
  .toast .warning{background:#fffbeb;color:#92400e;border:1px solid #fde68a}
  .toast .ok{background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0}

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

  /* Connector picker — what each one is, not a list of type names. */
  .picker{margin-top:6px}
  .pgroup h3{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;
    color:#94a3b8;margin:12px 0 6px;font-weight:600}
  .pick{display:block;width:100%;text-align:left;margin-bottom:5px;padding:8px 11px;
    line-height:1.45}
  .pick b{display:block;font-size:12.5px;font-weight:600;color:#1e293b}
  .pick span{display:block;font-size:11px;color:#94a3b8}
  .ctype{flex:0 0 auto;font-size:11px;font-weight:600;color:#2563eb;
    background:#eff6ff;padding:3px 9px;border-radius:20px;white-space:nowrap}
  /* A real brand mark (Simple Icons SVG) dropped inline where an emoji used
     to sit — sized down to match, and coloured via currentColor so it never
     clashes with the accent colour marking a selected connector. */
  .ctype svg,.pick b svg{width:13px;height:13px;vertical-align:-2px;margin-right:1px}
  .blurb{font-size:11.5px;color:#64748b;margin:6px 0 2px}
  .fhelp{font-size:10.5px;color:#94a3b8;line-height:1.5;margin:3px 0 0}
  .fhelp.setup{margin-top:10px;padding-top:8px;border-top:1px solid #eef2f6}
  .opt{color:#cbd5e1;font-weight:400}

  /* Below this the fixed 170/300px rails squeeze the canvas to nothing and
     clip the panel's own text ("NOTHI..."). Give both columns less room and
     let the header wrap rather than overflow. */
  @media (max-width: 1100px){
    .app{grid-template-columns:132px 1fr 240px}
    header{gap:6px;padding:0 10px;flex-wrap:wrap}
    header h1{font-size:13px}
    .palette{padding:9px}
  }

  /* Run tab */
  .steps{margin-top:12px;font-size:11.5px}
  .steps div{padding:3px 0;color:#64748b}
  .steps div::before{content:'✓ ';color:#16a34a}
  .steps div.doing{color:#1e293b;font-weight:600}
  .steps div.doing::before{content:'· ';color:#2563eb}
  .answer{margin-top:12px;padding:12px 14px;border-radius:9px;white-space:pre-wrap;
    line-height:1.6;background:#f0fdf4;border:1px solid #bbf7d0;color:#14532d}
  .answer.bad{background:#fef2f2;border-color:#fecaca;color:#7f1d1d}
  .meta{margin-top:6px;font-size:11px;color:#94a3b8}

  /* Activity tab */
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:14px}
  .kpi{background:#fbfcfd;border:1px solid #e2e8f0;border-radius:9px;padding:10px 12px}
  .kpi .n{font-size:19px;font-weight:700;color:#0f172a}
  .kpi .l{font-size:10.5px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em}
  table{width:100%;border-collapse:collapse;font-size:11.5px}
  th{text-align:left;color:#94a3b8;font-weight:500;padding:5px 8px;border-bottom:1px solid #e2e8f0}
  td{padding:5px 8px;border-bottom:1px solid #f1f5f9;color:#475569}
  td .pill{padding:1px 7px;border-radius:20px;font-size:10.5px}
  .pill.success{background:#f0fdf4;color:#15803d}
  .pill.error{background:#fef2f2;color:#b91c1c}
  .pill.paused{background:#fffbeb;color:#92400e}
</style></head><body>
<div class="app">
  <header>
    <h1>roscoe build</h1>
    <button class="tab on" id="tabFlow" onclick="tab('flow')">Flow</button>
    <button class="tab" id="tabSetup" onclick="tab('setup')">Setup</button>
    <button class="tab" id="tabRun" onclick="tab('run')">Run</button>
    <button class="tab" id="tabActivity" onclick="tab('activity')">Activity</button>
    <span class="file" id="file"></span>
    <span class="sp"></span>
    <span id="flowActions">
      <button onclick="undo()" title="Ctrl+Z">Undo</button>
      <button onclick="exportPython()" title="A folder that runs without roscoe">Download Python</button>
      <button onclick="check()">Validate</button>
      <button class="primary" onclick="save()" title="Ctrl+S">Save workflow.yaml</button>
    </span>
    <span id="setupActions" style="display:none">
      <button class="primary" onclick="saveSetup()">Save setup</button>
    </span>
  </header>

  <div class="palette">
    <h2>Add node</h2>
    <button onclick="addNode('trigger')">📅 Schedule</button>
    <button onclick="addNode('connector_action')">⚡ Action</button>
    <button onclick="addNode('condition')">🔀 Decision</button>
    <button onclick="addNode('llm_step')">💬 Prompt</button>
    <button onclick="addNode('agent_step')">🤖 Agent</button>
    <button onclick="addNode('parallel')">🪢 Parallel</button>
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
    <div class="zoombar">
      <button onclick="zoomBy(-0.1)" title="Zoom out">&minus;</button>
      <button onclick="zoomFit()" title="Fit everything on screen">Fit</button>
      <button onclick="zoomBy(0.1)" title="Zoom in">+</button>
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
      <h2>🔌 Connectors</h2>
      <p>The systems this agent can reach. Each one's methods become choices on
         every action node. You can also add these directly from an Action
         node's panel on the Flow tab.</p>
      <div id="connectors"></div>
      <div id="picker" class="picker"></div>
    </section>

    <section>
      <h2>🤖 Sub-agents</h2>
      <p>Extra agents that live inside <em>this same project</em> — used by Agent
         nodes for a task that needs its own judgement, like "one task per action
         item". Everything runs in one <code>roscoe run</code>, no other process or
         port involved. (This is different from the "Another agent" connector,
         which calls a separate roscoe project running elsewhere — for that,
         add it as a connector on an Action node instead.) Tick the tools each
         one may use. You can also add these directly from an Agent node's panel
         on the Flow tab.</p>
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

  </div></div>

  <div class="setup" id="run"><div class="wrap">
    <section>
      <h2>Try it</h2>
      <p>Runs the saved workflow, so save your changes on the Flow tab first.
         Every run is recorded and shows up under Activity.</p>
      <div id="runInputs"></div>
      <button class="primary" id="runBtn" onclick="doRun()" style="margin-top:10px">Run</button>
      <div id="runSteps" class="steps"></div>
      <div id="runOut"></div>
    </section>
  </div></div>

  <div class="setup" id="activity"><div class="wrap">
    <section>
      <h2>Activity</h2>
      <p>Every run this project has done — the same figures <code>roscoe monitor</code>
         reports, read from its audit log.</p>
      <div id="kpis" class="kpis"></div>
      <div id="runsTable"></div>
    </section>
  </div></div>
</div>

<div class="toast" id="status" onclick="this.classList.remove('show')"></div>

<script>
let wf = {entry:'', nodes:[], system:'', output:''};
let layout = {}, methods = {}, agents = [], selected = null;
// Setup tab. Connectors and agents are held as arrays, not objects: a name is
// being edited keystroke by keystroke, and rekeying an object on every one of
// those loses focus and mangles order.
let model = {}, conns = [], agentsArr = [], ui = {}, uiInputs = [], connectorTypes = [];
let CATALOG = [];
const sheet = document.getElementById('sheet'), svg = document.getElementById('edges');
const canvas = document.getElementById('canvas');

const PROVIDERS = ['openai','azure_openai','anthropic','gemini','nvidia','ollama'];
const UI_TEXT = [['title','Title'],['subtitle','Subtitle'],['heading','Heading'],
  ['intro','Intro'],['greeting','Chat greeting'],['placeholder','Chat placeholder'],
  ['submit','Button text'],['accent','Accent colour']];
const INPUT_TYPES = ['text','date','email','number','select'];
// Plain-language intervals, so nobody has to know what "*/15 * * * *" means.
const EVERY = [['15m','every 15 minutes'],['30m','every 30 minutes'],['1h','every hour'],
  ['6h','every 6 hours'],['12h','every 12 hours'],['1d','once a day'],['7d','once a week']];

// Which field each outgoing port writes to, per node type.
function ports(n){
  if(n.type === 'condition') return [{field:'then', label:'yes'}, {field:'else', label:'no'}];
  if(n.type === 'connector_action' && (n.requires_approval || n.on_reject))
    return [{field:'next', label:'approved'}, {field:'on_reject', label:'rejected'}];
  return [{field:'next', label:''}];
}

function summary(n){
  if(n.type === 'trigger') return n.kind === 'webhook' ? 'webhook'
    : 'every ' + (n.every||'?') + (n.at ? ', at '+n.at : '');
  if(n.type === 'connector_action') return ((n.connector ? n.connector+'.' : '') + (n.method||'?')) + '()';
  if(n.type === 'condition') return n.when || '?';
  if(n.type === 'agent_step') return 'agent: ' + (n.agent||'?');
  if(n.type === 'parallel'){
    const names = Object.keys(n.branches||{});
    return names.length ? names.length+' branches: '+names.join(', ') : 'no branches yet';
  }
  return (n.parse==='json'?'{ } ':'') + (n.prompt||'').slice(0, 56);
}
const SHORT = {trigger:'schedule', connector_action:'action', condition:'decision',
  llm_step:'prompt', agent_step:'agent', parallel:'parallel'};
const NODE_ICON = {trigger:'📅', connector_action:'⚡', condition:'🔀',
  llm_step:'💬', agent_step:'🤖', parallel:'🪢'};

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
  CATALOG = d.catalog || [];
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

const TABS = ['flow','setup','run','activity'];

function tab(which){
  const onFlow = which === 'flow';
  // The flow view is three separate grid children, not one pane, so it is
  // shown/hidden as a group rather than by id like the others.
  for(const id of ['palette','canvas','panel'])
    document.querySelector('.'+id).style.display = onFlow ? '' : 'none';
  for(const t of TABS){
    const pane = document.getElementById(t);
    if(pane) pane.style.display = (t === which) ? 'block' : 'none';
    const btn = document.getElementById('tab' + t[0].toUpperCase() + t.slice(1));
    if(btn) btn.className = 'tab' + (t === which ? ' on' : '');
  }
  document.getElementById('flowActions').style.display = onFlow ? '' : 'none';
  document.getElementById('setupActions').style.display = which==='setup' ? '' : 'none';
  if(which === 'setup') renderSetup();
  if(which === 'run') renderRun();
  if(which === 'activity') loadActivity();
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

  document.getElementById('connectors').innerHTML = conns.map((c, i) => connectorCardHtml(i, 'renderSetup'))
    .join('') || '<p class="hint">Nothing connected yet. Add one below.</p>';

  // The picker: what each connector is, grouped, rather than a list of type names.
  document.getElementById('picker').innerHTML = pickerGroupsHtml('addConnector');

  document.getElementById('agentList').innerHTML = agentsArr.map((a, i) => agentCardHtml(i)).join('')
    || '<p class="hint">No sub-agents yet — only needed for Agent nodes. Add one here, or from an Agent node\'s panel on the Flow tab.</p>';

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
// Settings are held as [key, value] pairs so a half-typed key doesn't rekey an
// object on every keystroke. These read/write one named setting within that.
function settingOf(c, key){
  const row = c.settings.find(([k]) => k === key);
  return row ? row[1] : '';
}

function setSetting(i, key, value){
  const c = conns[i];
  const row = c.settings.find(([k]) => k === key);
  if(row) row[1] = value; else c.settings.push([key, value]);
}

// Switching auth mode drops whatever the *other* modes had filled in (unless
// the new mode also uses that same field name, e.g. ServiceNow's
// instance_url) — otherwise a config saved after switching modes keeps
// shipping the old mode's placeholders alongside the new one's real fields.
function switchAuthMode(i, key){
  const c = conns[i];
  const spec = CATALOG.find(s => s.type === c.type);
  c.authMode = key;
  const keep = new Set(fieldsOf(spec, key).map(f => f.name));
  const others = new Set();
  spec.auth_modes.forEach(m => { if(m.key !== key) m.fields.forEach(f => others.add(f.name)); });
  c.settings = c.settings.filter(([k]) => !others.has(k) || keep.has(k));
  refreshAll();
}

function rawSettings(c, i){
  return '<label>Settings</label>'
    + c.settings.map(([k,v], j) =>
        '<div class="kv"><input value="'+esc(k)+'" placeholder="key" oninput="conns['+i+'].settings['+j+'][0]=this.value">'
      + '<input value="'+esc(v)+'" placeholder="value or ${VAR}" oninput="conns['+i+'].settings['+j+'][1]=this.value">'
      + '<button onclick="conns['+i+'].settings.splice('+j+',1);renderSetup()">&times;</button></div>').join('')
    + '<button onclick="conns['+i+'].settings.push([\'\',\'\']);renderSetup()">+ setting</button>';
}

// Both the Setup tab and a node's own panel (Flow tab) render the same
// connector/agent cards and the same type picker — added once here so a
// connector or sub-agent created from either place looks and behaves
// identically, and neither view can drift from the other.
function refreshAll(){ renderSetup(); panel(); }
function iconFor(spec){ return spec && spec.icon ? spec.icon + ' ' : ''; }

// A connector with more than one valid way to authenticate (an API token vs
// OAuth, say) picks a mode explicitly rather than mixing every mode's fields
// into one list with no way to tell which ones a given setup actually needs.
function fieldsOf(spec, mode){
  if(!spec.auth_modes) return spec.fields;
  return (spec.auth_modes.find(m => m.key === mode) || spec.auth_modes[0]).fields;
}

// An explicit choice (conns[i].authMode) always wins. Failing that, infer
// from which mode's *own* fields (the ones no other mode also uses) already
// have values — so loading an existing config lands on the mode it was
// actually configured for, not always the first one listed.
function modeOf(c, spec){
  if(!spec.auth_modes) return null;
  if(c.authMode) return c.authMode;
  const nameCount = {};
  spec.auth_modes.forEach(m => m.fields.forEach(f => { nameCount[f.name] = (nameCount[f.name]||0)+1; }));
  let best = spec.auth_modes[0].key, bestScore = -1;
  for(const m of spec.auth_modes){
    const score = m.fields.filter(f => nameCount[f.name] === 1 && settingOf(c, f.name)).length;
    if(score > bestScore){ bestScore = score; best = m.key; }
  }
  return best;
}

function connectorCardHtml(i){
  const c = conns[i];
  const spec = CATALOG.find(s => s.type === c.type);
  const head = '<div class="card"><div class="top">'
    + '<span class="ctype">' + iconFor(spec) + esc(spec ? spec.label : (c.type || 'Pick one below')) + '</span>'
    + '<input value="'+esc(c.name)+'" placeholder="name used in nodes" oninput="conns['+i+'].name=this.value">'
    + '<button class="danger" onclick="conns.splice('+i+',1);refreshAll()">remove</button></div>';

  // Not catalogued (an older config, or a type added without a catalog entry):
  // fall back to the raw key/value editor rather than hiding its settings.
  if(!spec) return head + rawSettings(c, i) + '</div>';

  const mode = modeOf(c, spec);
  const modeSelector = spec.auth_modes
    ? '<label>How do you want to authenticate?</label><select onchange="switchAuthMode('+i+',this.value)">'
      + spec.auth_modes.map(m => '<option value="'+m.key+'"'+(m.key===mode?' selected':'')+'>'
          + esc(m.label)+'</option>').join('') + '</select>'
    : '';

  const body = fieldsOf(spec, mode).map(fd => {
    const val = settingOf(c, fd.name);
    const set = "setSetting("+i+",'"+fd.name+"',this.value)";
    const control = fd.choices
      ? '<select onchange="'+set+'">' + opts(fd.choices, String(val||fd.default||''), !fd.required) + '</select>'
      : '<input value="'+esc(val)+'" placeholder="'+esc(fd.placeholder || (fd.default==null?'':fd.default))+'" oninput="'+set+'">';
    return '<label>' + esc(fd.label) + (fd.required ? '' : ' <span class="opt">optional</span>') + '</label>'
      + control
      + (fd.help ? '<p class="fhelp">'+esc(fd.help)+'</p>' : '');
  }).join('');

  return head + '<p class="blurb">'+esc(spec.blurb)+'</p>' + modeSelector + body
    + (spec.setup ? '<p class="fhelp setup">'+esc(spec.setup)+'</p>' : '') + '</div>';
}

// What each connector type is, grouped by category, rather than a bare list of
// type names — `onclickFn` is the name of the JS function invoked with the
// chosen type, so the same markup drives both the Setup picker and the
// smaller one embedded in an Action node's panel.
function pickerGroupsHtml(onclickFn){
  const groups = {};
  for(const s of CATALOG) (groups[s.category] = groups[s.category] || []).push(s);
  return Object.keys(groups).sort().map(cat =>
    '<div class="pgroup"><h3>'+esc(cat)+'</h3>'
    + groups[cat].map(s =>
        '<button class="pick" onclick="'+onclickFn+'(&quot;'+s.type+'&quot;)">'
        + '<b>'+iconFor(s)+esc(s.label)+'</b><span>'+esc(s.blurb)+'</span></button>').join('')
    + '</div>').join('');
}

function agentCardHtml(i){
  const a = agentsArr[i];
  const toolRefs = [].concat(...Object.entries(methods).map(([c, ms]) => ms.map(m => c + '.' + m)));
  return '<div class="card"><div class="top">'
    + '<span class="ctype">🤖</span>'
    + '<input value="'+esc(a.name)+'" placeholder="agent name" oninput="agentsArr['+i+'].name=this.value">'
    + '<button class="danger" onclick="agentsArr['+i+']&&agentsArr.splice('+i+',1);refreshAll()">remove</button>'
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
    + '</div>';
}

function addConnector(type){
  const spec = CATALOG.find(s => s.type === type);
  // Prefill every secret as ${VAR}. Typing a real key into a form that gets
  // written to a committed file is the mistake worth designing out. A
  // multi-mode connector starts on its first mode — modeOf() falls back to
  // it anyway once nothing is filled in yet, so there's nothing to record.
  const startFields = spec ? fieldsOf(spec, spec.auth_modes ? spec.auth_modes[0].key : null) : null;
  const settings = startFields
    ? startFields.filter(f => f.env || f.default != null)
        .map(f => [f.name, f.env ? '${'+f.env+'}' : String(f.default)])
    : [['','']];
  let base = (type || 'connector').split('_')[0], name = base, n = 2;
  while(conns.some(c => c.name === name)) name = base + n++;
  conns.push({name, type: type || '', settings});
  renderSetup();
  return name;
}
function addAgent(){ agentsArr.push({name:'', system_prompt:'', tools:[]}); renderSetup(); }
function addUiInput(){ uiInputs.push({name:'', type:'text', required:false}); renderSetup(); }

// --- creating a connector or sub-agent from inside a node's own panel, so
// wiring up an Action or Agent node never requires a trip to the Setup tab.
// Persisted immediately (rather than waiting for "Save setup") so the new
// connector's methods, or the new agent, are available to pick the moment
// they're created.

async function addConnectorFromNode(type){
  const n = node(); if(!n || !type) return;
  n.connector = addConnector(type);
  await saveSetup();
  panel(); render();
}

async function addAgentFromNode(){
  const n = node(); if(!n) return;
  let base = 'agent', name = base, k = 2;
  while(agentsArr.some(a => a.name === name)) name = base + (k++);
  agentsArr.push({name, system_prompt: '', tools: []});
  n.agent = name;
  await saveSetup();
  panel(); render();
}

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
  // Saving with nothing wrong used to render an empty box and read as a no-op.
  const bad = (r.issues || []).some(i => i.level === 'error');
  showIssues(r.issues, bad ? null : 'Saved agent_config.yaml');
}

function render(){
  document.querySelectorAll('.node').forEach(e => e.remove());
  for(const n of wf.nodes){
    const p = layout[n.id] || {x:80, y:60};
    const el = document.createElement('div');
    el.className = 'node' + (n.id===selected?' sel':'') + (n.id===wf.entry?' entry':'')
      + (n.requires_approval?' gated':'');
    el.dataset.id = n.id;
    el.style.left = p.x+'px'; el.style.top = p.y+'px';
    el.innerHTML = '<div class="hd"><span class="nid">'+(NODE_ICON[n.type]||'')+' '+esc(n.id)+'</span>'
      + '<span class="t">'+SHORT[n.type]+'</span></div>'
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
  // Divided by zoom: the pointer moves in screen pixels, the layout is stored
  // in sheet pixels, and at 0.5x an unscaled delta sends the node twice as far
  // as the cursor went.
  const move = ev => {
    p.x = Math.max(0, ox + (ev.clientX - sx) / zoom);
    p.y = Math.max(0, oy + (ev.clientY - sy) / zoom);
    render();
  };
  const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); };
  document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
}

// Same math drawEdges() uses for a real edge's start point, so the live
// preview line starts from exactly where the actual edge will.
function portPos(fromId, field){
  const node = wf.nodes.find(n => n.id === fromId);
  const a = layout[fromId];
  const i = Math.max(0, ports(node).findIndex(p => p.field === field));
  return {x: a.x+190, y: a.y+32+i*20};
}

function startLink(e, fromId, field){
  e.preventDefault(); e.stopPropagation();
  const origin = portPos(fromId, field);
  canvas.classList.add('linking');
  const live = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  live.id = 'liveLink';
  svg.appendChild(live);

  const clearDropTarget = () =>
    document.querySelectorAll('.node.drop-target').forEach(el => el.classList.remove('drop-target'));

  const move = ev => {
    const r = sheet.getBoundingClientRect();
    // getBoundingClientRect is post-transform, so undo the scale to land back
    // in the same coordinates the edges are drawn in.
    const mx = (ev.clientX - r.left) / zoom, my = (ev.clientY - r.top) / zoom;
    const midx = (origin.x+mx)/2;
    live.setAttribute('d', 'M'+origin.x+','+origin.y+' C'+midx+','+origin.y+' '+midx+','+my+' '+mx+','+my);
    clearDropTarget();
    const hovered = document.elementFromPoint(ev.clientX, ev.clientY);
    const nodeEl = hovered && hovered.closest('.node');
    if(nodeEl && nodeEl.dataset.id !== fromId) nodeEl.classList.add('drop-target');
  };
  const up = ev => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    canvas.classList.remove('linking');
    clearDropTarget();
    live.remove();
    const el = ev.target.closest('.node');
    const node = wf.nodes.find(n => n.id === fromId);
    if(el){
      const toId = el.dataset.id;
      if(toId !== fromId){ node[field] = toId; }
    } else {
      node[field] = 'END';   // dropped on empty canvas: end the run
    }
    render(); panel();
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
}

// --- property panel ---

function panel(){
  const n = wf.nodes.find(x => x.id === selected);
  const box = document.getElementById('panel');
  if(!n){ box.innerHTML = '<h2>Nothing selected</h2><p class="hint">Click a node to edit it.</p>'; return; }

  const nodeOpts = (val) => '<option value=""></option><option value="END"'+(val==='END'?' selected':'')+'>END</option>'
    + wf.nodes.filter(o=>o.id!==n.id).map(o=>'<option'+(o.id===val?' selected':'')+'>'+esc(o.id)+'</option>').join('');

  let html = '<h2>'+(NODE_ICON[n.type]||'')+' '+SHORT[n.type]+'</h2>';
  html += f('Name', '<input value="'+esc(n.id)+'" onchange="rename(this.value)">');

  if(n.type === 'trigger'){
    const kind = n.kind === 'webhook' ? 'webhook' : 'schedule';
    html += f('Starts', '<select onchange="setTriggerKind(this.value)">'
      + '<option value="schedule"'+(kind==='schedule'?' selected':'')+'>📅 On a schedule</option>'
      + '<option value="webhook"'+(kind==='webhook'?' selected':'')+'>🪝 From a webhook (HTTP request)</option>'
      + '</select>');
    if(kind === 'webhook'){
      html += '<p class="hint">Once running, <code>roscoe run</code> exposes '
        + '<code>POST /webhook</code> — the request body becomes this workflow\'s '
        + 'input (read it as <code>{{ input.whatever }}</code>). No separate '
        + '<code>roscoe schedule</code> needed for this one.</p>';
    } else {
      html += f('Run this workflow', '<select onchange="set(\'every\',this.value);panel()">'
        + EVERY.map(([v,lbl]) => '<option value="'+v+'"'+(v===n.every?' selected':'')+'>'
            + esc(lbl)+'</option>').join('') + '</select>');
      // A time of day only means anything for a daily run — offering it on a
      // 15-minute interval would just be a field that does nothing.
      if(n.every === '1d')
        html += f('At (24-hour, e.g. 06:00)',
          '<input value="'+esc(n.at||'')+'" placeholder="06:00" oninput="set(\'at\',this.value)">');
      html += '<p class="hint">Start it with <code>roscoe schedule</code>. '
        + 'The workflow still runs on demand with <code>roscoe run</code>.</p>';
    }
  } else if(n.type === 'connector_action'){
    const connNames = Object.keys(methods);
    html += f('Connector (optional)', '<select onchange="set(\'connector\',this.value);panel()">'
      + '<option value=""></option>'
      + connNames.map(c=>'<option'+(c===n.connector?' selected':'')+'>'+esc(c)+'</option>').join('')+'</select>');
    // Wiring an action to a system it hasn't talked to yet used to mean a trip
    // to the Setup tab and back — added, configured and picked without leaving
    // this panel instead.
    const connIdx = conns.findIndex(c => c.name === n.connector);
    if(connIdx >= 0) html += connectorCardHtml(connIdx);
    html += '<details style="margin:8px 0"><summary style="cursor:pointer;color:#2563eb;'
      + 'font-size:11.5px">+ New connector</summary><div class="picker">'
      + pickerGroupsHtml('addConnectorFromNode') + '</div></details>';
    const avail = methods[n.connector] || [].concat(...Object.values(methods));
    html += f('Method', avail.length
      ? '<select onchange="set(\'method\',this.value)"><option value=""></option>'
        + avail.map(m=>'<option'+(m===n.method?' selected':'')+'>'+esc(m)+'</option>').join('')+'</select>'
      : '<input value="'+esc(n.method||'')+'" oninput="set(\'method\',this.value)">');
    html += '<label>Inputs</label>' + inputRows(n);
    html += f('Show instead of the raw result (optional)',
      '<textarea oninput="set(\'output_message\',this.value)">'+esc(n.output_message||'')+'</textarea>')
      + '<p class="hint">A connector\'s own return is API-shaped — an id, a status code.'
      + ' Fill this in to show something readable instead, e.g. "Recap sent."</p>';
    html += '<label style="margin-top:10px"><input type="checkbox"'+(n.requires_approval?' checked':'')
      + ' onchange="set(\'requires_approval\',this.checked);panel()">Needs approval</label>';
    if(n.requires_approval)
      html += f('If rejected, go to', '<select onchange="set(\'on_reject\',this.value)">'+nodeOpts(n.on_reject)+'</select>');
  } else if(n.type === 'condition'){
    html += f('When (expression)', '<input value="'+esc(n.when||'')+'" oninput="set(\'when\',this.value)">');
    html += f('Yes &rarr;', '<select onchange="set(\'then\',this.value)">'+nodeOpts(n.then)+'</select>');
    html += f('No &rarr;', '<select onchange="set(\'else\',this.value)">'+nodeOpts(n['else'])+'</select>');
  } else if(n.type === 'llm_step'){
    html += f('Prompt', '<textarea oninput="set(\'prompt\',this.value)">'+esc(n.prompt||'')+'</textarea>');
    html += f('System (overrides shared)', '<textarea oninput="set(\'system\',this.value)">'+esc(n.system||'')+'</textarea>');
    html += '<label style="margin-top:10px"><input type="checkbox"'+(n.parse==='json'?' checked':'')
      + ' onchange="set(\'parse\',this.checked?\'json\':\'\')">Parse reply as JSON</label>'
      + '<p class="hint">Read fields back with {{ '+esc(n.output||'result')+'.field }} instead of one long string.</p>';
  } else if(n.type === 'agent_step'){
    html += '<p class="hint">Runs inside this same project — no separate '
      + '<code>roscoe run</code>, no port. For calling an agent hosted elsewhere, '
      + 'use an Action node with an "Another agent" connector instead.</p>';
    html += f('Agent', agents.length
      ? '<select onchange="set(\'agent\',this.value);panel()"><option value=""></option>'
        + agents.map(a=>'<option'+(a===n.agent?' selected':'')+'>'+esc(a)+'</option>').join('')+'</select>'
      : '<input value="'+esc(n.agent||'')+'" oninput="set(\'agent\',this.value)">');
    const agentIdx = agentsArr.findIndex(a => a.name === n.agent);
    if(agentIdx >= 0) html += agentCardHtml(agentIdx);
    html += '<button onclick="addAgentFromNode()" style="margin:4px 0 8px">+ New agent</button>';
    html += f('Task', '<textarea oninput="set(\'task\',this.value)">'+esc(n.task||'')+'</textarea>');
  } else if(n.type === 'parallel'){
    html += '<p class="hint">Runs every branch below at the same time, then '
      + 'continues once all of them finish. Each branch is another node on '
      + 'this canvas — pick one that isn\'t already reached some other way, '
      + 'since its own routing is ignored when run as a branch.</p>';
    html += '<label>Branches</label>' + branchRows(n);
    html += '<p class="fhelp">"Save result as" below becomes a dict of '
      + '{branch name: that node\'s own output}.</p>';
  }

  // A trigger produces nothing to save — it only decides when the run starts.
  if(n.type !== 'trigger')
    html += f('Save result as', '<input value="'+esc(n.output||'')+'" oninput="set(\'output\',this.value)" placeholder="state key">');
  if(n.type !== 'condition')
    html += f('Then go to', '<select onchange="set(\'next\',this.value)">'+nodeOpts(n.next)+'</select>');
  html += '<div class="row" style="margin-top:14px">'
    + '<button onclick="duplicateNode()" title="Ctrl+D">Duplicate</button>'
    + '<button class="danger" onclick="removeNode()" title="Delete">Delete node</button></div>';
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

function branchRows(n){
  const entries = Object.entries(n.branches || {});
  const targets = wf.nodes.filter(o => o.id !== n.id && o.type !== 'trigger');
  const targetOpts = (val) => '<option value=""></option>'
    + targets.map(o => '<option'+(o.id===val?' selected':'')+'>'+esc(o.id)+'</option>').join('');
  let html = '<div id="branches">';
  entries.forEach(([name, target], i) => {
    html += '<div class="row" style="margin-bottom:4px">'
      + '<input value="'+esc(name)+'" placeholder="branch name" onchange="renameBranch('+i+',this.value)">'
      + '<select onchange="setBranch('+i+',this.value)">'+targetOpts(target)+'</select>'
      + '<button onclick="removeBranch('+i+')">&times;</button>'
      + '</div>';
  });
  html += '</div><button style="margin-top:4px" onclick="addBranch()">+ branch</button>';
  return html;
}

// --- zoom ---

// A workflow of any size outgrows the viewport quickly, and there was no way to
// see it whole. Scaling the sheet keeps node coordinates untouched, so dragging
// and edge-drawing stay in the same space the layout file records.
let zoom = 1;

function applyZoom(){
  sheet.style.transform = 'scale(' + zoom + ')';
}

function zoomBy(delta){
  zoom = Math.min(1.6, Math.max(0.3, Math.round((zoom + delta) * 100) / 100));
  applyZoom();
}

function zoomFit(){
  const pts = Object.values(layout);
  if(!pts.length){ zoom = 1; return applyZoom(); }
  const maxX = Math.max(...pts.map(p => p.x)) + 220;   // node width + margin
  const maxY = Math.max(...pts.map(p => p.y)) + 140;
  const box = canvas.getBoundingClientRect();
  zoom = Math.min(1, Math.max(0.3,
    Math.min((box.width - 30) / maxX, (box.height - 30) / maxY)));
  applyZoom();
  canvas.scrollTo(0, 0);
}

// --- undo ---

// Snapshots of the whole graph, taken before anything destructive. Deleting a
// node used to be unrecoverable — you rebuilt it by hand — which made the
// canvas feel risky to experiment on.
let history = [];
const HISTORY_LIMIT = 50;

function snapshot(){
  history.push(JSON.stringify({wf, layout, entry: wf.entry}));
  if(history.length > HISTORY_LIMIT) history.shift();
}

function undo(){
  const prev = history.pop();
  if(!prev){ return showIssues([], 'Nothing to undo.'); }
  const s = JSON.parse(prev);
  wf = s.wf; layout = s.layout;
  if(selected && !wf.nodes.some(n => n.id === selected)) selected = null;
  render(); panel();
  document.getElementById('system').value = wf.system || '';
  document.getElementById('wfout').value = wf.output || '';
  showIssues([], 'Undone.');
}

// --- mutations ---

function node(){ return wf.nodes.find(x => x.id === selected); }
function set(field, value){
  const n = node();
  if(value === '' || value === false) delete n[field]; else n[field] = value;
  render();
}
// A trigger switching modes drops the fields the other mode owns, so saving
// never writes a stale `every` onto a webhook trigger or vice versa.
function setTriggerKind(kind){
  const n = node();
  if(kind === 'webhook'){ n.kind = 'webhook'; delete n.every; delete n.at; }
  else { delete n.kind; if(!n.every) n.every = '1d'; }
  render(); panel();
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
function freeId(base){
  let i = 1; while(wf.nodes.some(n => n.id === base+'_'+i)) i++;
  return base+'_'+i;
}

// Rebuilding five near-identical action nodes by hand is the single most
// tedious thing about the canvas.
function duplicateNode(){
  const n = node(); if(!n) return;
  snapshot();
  const copy = JSON.parse(JSON.stringify(n));
  copy.id = freeId(n.type.split('_')[0]);
  delete copy.next; delete copy.then; delete copy['else']; delete copy.on_reject;
  wf.nodes.push(copy);
  const at = layout[n.id] || {x:80, y:60};
  layout[copy.id] = {x: at.x + 40, y: at.y + 60};
  selected = copy.id; render(); panel();
}

function addNode(type){
  snapshot();
  const n = {id: freeId(type.split('_')[0]), type};
  if(type === 'condition'){ n.when = 'true'; n.then = ''; }
  if(type === 'llm_step') n.prompt = '';
  if(type === 'agent_step'){ n.agent = agents[0] || ''; n.task = ''; }
  if(type === 'connector_action') n.method = '';
  if(type === 'parallel') n.branches = {};
  if(type === 'trigger') n.every = '1d';
  wf.nodes.push(n);
  layout[n.id] = {x: 80 + (wf.nodes.length%3)*260, y: 60 + Math.floor(wf.nodes.length/3)*150};
  // A schedule is where the run begins, so adding one makes it the entry —
  // otherwise it sits on the canvas looking connected but never firing.
  if(!wf.entry || type === 'trigger') wf.entry = n.id;
  selected = n.id; render(); panel();
}
function removeNode(){
  const id = selected;
  if(!id) return;
  snapshot();
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
function addBranch(){ const n = node(); n.branches = n.branches || {}; n.branches[''] = ''; panel(); }
function renameBranch(i, key){
  const n = node(), e = Object.entries(n.branches);
  e[i][0] = key;
  n.branches = Object.fromEntries(e.filter(([k])=>k!=='')); panel();
}
function setBranch(i, target){
  const n = node(), e = Object.entries(n.branches);
  e[i][1] = target; n.branches = Object.fromEntries(e); render();
}
function removeBranch(i){
  const n = node(), e = Object.entries(n.branches);
  e.splice(i, 1); n.branches = Object.fromEntries(e); panel(); render();
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
let _toastTimer = null;

// Feedback goes to the always-present toast, never to the property panel: the
// panel is replaced by "Nothing selected" whenever no node is highlighted, and
// anything written into it then is thrown away without a trace.
function showIssues(issues, okMsg){
  const box = document.getElementById('status');
  const list = issues || [];
  let html = okMsg ? '<div class="ok">'+esc(okMsg)+'</div>' : '';
  html += list.map(i =>
    '<div class="'+i.level+'">'+(i.node?'['+esc(i.node)+'] ':'')+esc(i.message)+'</div>').join('');
  box.innerHTML = html;
  box.classList.toggle('show', !!html);
  clearTimeout(_toastTimer);
  // A clean result can fade on its own; problems stay put until dismissed,
  // because they are the ones that still need doing something about.
  if(html && !list.length) _toastTimer = setTimeout(() => box.classList.remove('show'), 3500);
}

function download(blob, filename){
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// Hand back a folder that runs without roscoe: the agent, a .env.example naming
// every secret it wants, requirements.txt and a README. Exports the saved file,
// so what you download is what you validated — not an unsaved canvas.
async function exportPython(){
  let r;
  try{ r = await (await fetch('/api/export-bundle')).json(); }
  catch(e){ return showIssues([{level:'error', message:'Could not build the download.'}]); }

  if(!r.ok) return showIssues([{level:'error', message:r.error}]);

  // Base64 in, bytes out — a zip can't survive being treated as text.
  const bytes = Uint8Array.from(atob(r.data), c => c.charCodeAt(0));
  download(new Blob([bytes], {type:'application/zip'}), r.filename);
  showIssues([], 'Downloaded ' + r.filename + ' — unzip it, fill in .env, and it runs.');
}

// --- Run tab ---

// The form mirrors whatever `ui.inputs` declares in Setup, so testing here asks
// for exactly what the deployed page would ask for. With none declared, a
// workflow still needs a way in, so offer the single free-text `message`.
function renderRun(){
  const fields = (uiInputs || []).filter(f => f.name);
  document.getElementById('runInputs').innerHTML = fields.length
    ? fields.map(f => '<label>'+esc(f.label || f.name)+'</label>'
        + '<input id="ri_'+esc(f.name)+'" placeholder="'+esc(f.placeholder||'')+'">').join('')
    : '<label>Message</label><input id="ri_message" placeholder="anything — this workflow may ignore it">';
}

async function doRun(){
  const btn = document.getElementById('runBtn');
  const out = document.getElementById('runOut'), steps = document.getElementById('runSteps');
  const fields = (uiInputs || []).filter(f => f.name);
  const inputs = {};
  for(const f of fields){
    const el = document.getElementById('ri_'+f.name);
    if(el && el.value) inputs[f.name] = el.value;
  }
  if(!fields.length){
    const el = document.getElementById('ri_message');
    if(el) inputs.message = el.value;
  }

  btn.disabled = true; btn.textContent = 'Running…';
  out.innerHTML = ''; steps.innerHTML = '';
  // Poll the node-by-node progress so a slow run shows where it has got to
  // rather than sitting on a spinner with nothing to say.
  const poll = setInterval(async () => {
    try{
      const p = await (await fetch('/api/progress')).json();
      steps.innerHTML = (p.steps||[]).map((s,i,a) =>
        '<div class="'+(i===a.length-1?'doing':'')+'">'+esc(s)+'</div>').join('');
    }catch(e){}
  }, 500);

  try{
    const r = await (await fetch('/api/run', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({inputs})})).json();
    clearInterval(poll);
    steps.innerHTML = (r.steps||[]).map(s => '<div>'+esc(s)+'</div>').join('');
    const ok = r.status === 'success';
    out.innerHTML = '<div class="answer'+(ok?'':' bad')+'">'
      + esc(ok ? (r.output || '(no output)') : (r.error || r.status)) + '</div>'
      + (ok ? '<div class="meta">'+esc(r.tokens||0)+' tokens · '+esc(r.cost||'')+'</div>' : '');
  }catch(e){
    clearInterval(poll);
    out.innerHTML = '<div class="answer bad">'+esc(e.message || String(e))+'</div>';
  }finally{
    btn.disabled = false; btn.textContent = 'Run';
  }
}

// --- Activity tab ---

async function loadActivity(){
  let d;
  try{ d = await (await fetch('/api/metrics')).json(); }
  catch(e){ return showIssues([{level:'error', message:'Could not read the audit log.'}]); }

  const cards = [
    ['runs', d.total_runs],
    ['errors', (d.error_rate_pct||0) + '%'],
    ['total cost', '$' + (d.total_cost_usd||0).toFixed(4)],
    ['agents seen', Object.keys(d.latency_ms_by_agent||{}).length],
  ];
  document.getElementById('kpis').innerHTML = cards.map(([l,n]) =>
    '<div class="kpi"><div class="n">'+esc(n)+'</div><div class="l">'+esc(l)+'</div></div>').join('');

  const rows = d.recent || [];
  document.getElementById('runsTable').innerHTML = rows.length
    ? '<table><tr><th>when</th><th>agent</th><th>status</th><th>tokens</th><th>cost</th></tr>'
      + rows.map(r => '<tr><td>'+esc((r.start_time||'').replace('T',' ').slice(0,19))+'</td>'
        + '<td>'+esc(r.agent_name||'')+'</td>'
        + '<td><span class="pill '+esc(r.status||'')+'">'+esc(r.status||'')+'</span></td>'
        + '<td>'+esc(r.total_tokens||0)+'</td>'
        + '<td>'+(r.cost_usd ? '$'+Number(r.cost_usd).toFixed(4) : 'free')+'</td></tr>').join('')
      + '</table>'
    : '<p class="hint">No runs yet. Use the Run tab, and they will show up here.</p>';
}

function esc(s){ const d = document.createElement('div'); d.textContent = s==null?'':s; return d.innerHTML; }

// --- keyboard ---

// Skipped while typing: Delete inside a prompt must delete a character, not the
// node you happen to have selected.
function typing(el){
  return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable);
}

document.addEventListener('keydown', e => {
  const mod = e.ctrlKey || e.metaKey;

  if(mod && e.key.toLowerCase() === 's'){ e.preventDefault(); return save(); }
  if(mod && e.key.toLowerCase() === 'z' && !typing(e.target)){ e.preventDefault(); return undo(); }
  if(mod && e.key.toLowerCase() === 'd' && !typing(e.target) && selected){
    e.preventDefault(); return duplicateNode();
  }
  if(typing(e.target)) return;

  if(e.key === 'Escape'){
    selected = null; render(); panel();
    document.getElementById('status').classList.remove('show');
  }
  if((e.key === 'Delete' || e.key === 'Backspace') && selected){
    e.preventDefault(); removeNode();
  }
});

load();
</script>
</body></html>"""
