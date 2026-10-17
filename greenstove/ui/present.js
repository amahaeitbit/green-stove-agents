/* Green-Stove presenter layer: presenter mode, hero banner, guide strip, flashes and keyboard shortcuts.
   Self-contained and defensive: every entry point is wrapped in try/catch so it can never break the page.
   All text goes through textContent. The only global it adds is window.GSPresenter. */
(function(){
'use strict';
try{

var STORE_KEY='gs-presenter';
var STEPS=[
  {label:'Run with memory off',sel:'#btn-run',memory:'off',tab:'agent'},
  {label:'Run another day with curated memory',sel:'#btn-run',memory:'curated',tab:'agent'},
  {label:'Try a fabricated note',sel:'#btn-fake',tab:'brain'},
  {label:'Ask a household agent',sel:'#hh-list button[aria-label^="Ask the household agent"]',tab:'households'},
  {label:'Fetch a You.com forecast',sel:'#btn-forecast',tab:'tools'},
  {label:'NemoClaw low-battery event',sel:'#btn-ev-low',tab:'tools'}
];
var done=[false,false,false,false,false,false];
var S={run:null,runError:null,memory:null,cost:null,note:''};
var hero=null,heroStatus=null,heroSub=null,heroTiles=null,heroTrail=null,heroNote=null,stepsBox=null,toggleBtn=null;
var flashTimers=(typeof WeakMap==='function')?new WeakMap():null;

/* ---------- tiny helpers ---------- */
function q(sel,root){try{return (root||document).querySelector(sel);}catch(e){return null;}}
function mk(tag,cls,text){
  var n=document.createElement(tag);
  if(cls)n.className=cls;
  if(text!=null)n.textContent=String(text);
  return n;
}
function list(v){return Array.isArray(v)?v:[];}
function num(v){
  if(typeof v!=='number'||!isFinite(v))return '—';
  if(Math.round(v)===v){try{return v.toLocaleString('en-US');}catch(e){return String(v);}}
  return String(Math.round(v*100)/100);
}
function visible(n){try{return !!(n&&n.getClientRects&&n.getClientRects().length);}catch(e){return false;}}

/* ---------- presenter mode ---------- */
function readPref(){try{return window.localStorage.getItem(STORE_KEY)!=='0';}catch(e){return true;}}
function writePref(on){try{window.localStorage.setItem(STORE_KEY,on?'1':'0');}catch(e){}}
function isPresenter(){try{return document.body.classList.contains('presenter');}catch(e){return false;}}
function setPresenter(on,save){
  try{
    document.body.classList.toggle('presenter',!!on);
    if(toggleBtn){
      toggleBtn.textContent=on?'Normal view':'Presenter mode';
      toggleBtn.setAttribute('aria-pressed',on?'true':'false');
    }
    if(save)writePref(!!on);
  }catch(e){}
}
function togglePresenter(){setPresenter(!isPresenter(),true);}

/* ---------- hero banner ---------- */
function statusKind(status){
  var s=String(status||'').toUpperCase();
  if(s==='PLAN_VERIFIED')return 'ok';
  if(s==='INFEASIBLE')return 'purple';
  if(s==='NO_VERIFIED_PLAN'||s==='ERROR'||s.indexOf('ERROR')>=0||s.indexOf('FAIL')>=0)return 'bad';
  return 'warn';
}
function tile(value,key){
  var t=mk('div','gs-tile');
  t.appendChild(mk('div','v',value));
  t.appendChild(mk('div','k',key));
  return t;
}
function bookedCount(bookings){
  var n=0;
  list(bookings).forEach(function(b){
    try{if(b&&/^(accepted|completed|booked)/i.test(String(b.status||'')))n++;}catch(e){}
  });
  return n;
}
function renderHero(){
  try{
    if(!hero)return;
    hero.classList.remove('ok','bad','warn','purple');
    heroTiles.replaceChildren();
    heroTrail.textContent='';
    heroSub.textContent='';
    heroNote.textContent=S.note||'';
    heroNote.hidden=!S.note;
    if(S.runError!=null){
      hero.classList.add('bad');
      heroStatus.textContent='ERROR';
      heroSub.textContent=String(S.runError);
      return;
    }
    var R=S.run;
    if(!R){
      heroStatus.textContent='Ready · pick a scenario and press Run';
      return;
    }
    var run=(R&&R.run)||{};
    var brief=run.brief||{};
    var status=String(run.status||'UNKNOWN').toUpperCase();
    hero.classList.add(statusKind(status));
    heroStatus.textContent=status;
    if(run.reason)heroSub.textContent=String(run.reason);
    var bookings=list(R.bookings);
    var total=Math.max(list(R.messages).length,bookings.length);
    var memory=S.memory||brief.mode||'—';
    var entries=list(brief.entry_ids).length;
    heroTiles.appendChild(tile(String(list(run.attempts).length),'attempts'));
    heroTiles.appendChild(tile(num(run.calls),'model calls'));
    heroTiles.appendChild(tile(num(run.tokens),'tokens'));
    heroTiles.appendChild(tile(typeof S.cost==='number'?'$'+S.cost.toFixed(4):'—','cost (session)'));
    heroTiles.appendChild(tile(String(memory),'memory · '+entries+' brief '+(entries===1?'entry':'entries')));
    heroTiles.appendChild(tile(bookedCount(bookings)+' / '+total,'households booked'));
    var trail=list(run.decisions).map(function(d){return String((d&&d.decision)||'?');});
    heroTrail.textContent='Decisions: '+(trail.length?trail.join(' → '):'—');
  }catch(e){}
}
function buildHero(){
  hero=mk('section');
  hero.id='gs-hero';
  hero.setAttribute('aria-live','polite');
  hero.setAttribute('aria-label','Last run summary');
  heroStatus=mk('div','gs-hero-status');
  heroSub=mk('div','gs-hero-sub');
  heroTiles=mk('div','gs-tiles');
  heroTrail=mk('div','gs-trail');
  heroNote=mk('div','gs-hero-note');
  hero.appendChild(heroStatus);
  hero.appendChild(heroSub);
  hero.appendChild(heroTiles);
  hero.appendChild(heroTrail);
  hero.appendChild(heroNote);
  return hero;
}

/* ---------- guide strip ---------- */
function currentStep(){for(var i=0;i<done.length;i++){if(!done[i])return i;}return -1;}
function renderSteps(){
  try{
    if(!stepsBox)return;
    var cur=currentStep();
    var buttons=stepsBox.querySelectorAll('.gs-step');
    for(var i=0;i<buttons.length;i++){
      var b=buttons[i];
      b.classList.toggle('done',!!done[i]);
      b.classList.toggle('current',i===cur);
      if(i===cur)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');
      b.setAttribute('aria-label','Step '+(i+1)+': '+STEPS[i].label+(done[i]?' (done)':''));
    }
  }catch(e){}
}
function markDone(n){
  try{if(n>=1&&n<=STEPS.length&&!done[n-1]){done[n-1]=true;renderSteps();}}catch(e){}
}
function goStep(i){
  try{
    var st=STEPS[i];
    if(!st)return;
    var target=q(st.sel);
    if(target&&!visible(target)){
      var tab=q('#tab-'+st.tab);
      if(tab&&visible(tab))tab.click();
    }
    if(st.memory){
      var ms=q('#sel-memory');
      if(ms&&ms.value!==st.memory){
        var has=false;
        for(var k=0;k<ms.options.length;k++){if(ms.options[k].value===st.memory)has=true;}
        if(has){
          ms.value=st.memory;
          try{ms.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}
        }
      }
    }
    if(!target)target=q('#panel-'+st.tab);
    if(!target)return;
    try{target.scrollIntoView({behavior:'smooth',block:'center'});}catch(e){try{target.scrollIntoView();}catch(e2){}}
    try{target.focus({preventScroll:true});}catch(e){}
    flash(target);
  }catch(e){}
}
function kbd(text){return mk('kbd','gs-kbd',text);}
function buildSteps(){
  stepsBox=mk('nav');
  stepsBox.id='gs-steps';
  stepsBox.setAttribute('aria-label','Demo guide');
  STEPS.forEach(function(st,i){
    var b=mk('button','gs-step');
    b.type='button';
    b.appendChild(mk('span','gs-step-n n',String(i+1)));
    b.appendChild(document.createTextNode(' '+st.label));
    b.addEventListener('click',function(){goStep(i);});
    stepsBox.appendChild(b);
  });
  var help=mk('div','gs-help');
  help.appendChild(document.createTextNode('Keys: '));
  help.appendChild(kbd('R'));
  help.appendChild(document.createTextNode(' run · '));
  help.appendChild(kbd('P'));
  help.appendChild(document.createTextNode(' presenter · '));
  help.appendChild(kbd('1–6'));
  help.appendChild(document.createTextNode(' jump to step'));
  stepsBox.appendChild(help);
  return stepsBox;
}

/* ---------- flash ---------- */
function flash(node){
  try{
    if(!node||!node.classList)return;
    node.classList.remove('gs-flash');
    void node.offsetWidth;                       /* restart the animation */
    node.classList.add('gs-flash');
    var old=flashTimers?flashTimers.get(node):null;
    if(old)clearTimeout(old);
    var t=setTimeout(function(){try{node.classList.remove('gs-flash');}catch(e){}},1600);
    if(flashTimers)flashTimers.set(node,t);
  }catch(e){}
}
function flashLater(find){
  /* the page re-renders after its own copy of the response; wait so the flash lands on the new nodes */
  setTimeout(function(){try{flash(find());}catch(e){}},120);
}
function cardOf(sel){var n=q(sel);try{return (n&&n.closest&&n.closest('.card'))||q('#panel-tools');}catch(e){return q('#panel-tools');}}
function householdCard(id){
  try{
    var cards=document.querySelectorAll('#hh-list article');
    for(var i=0;i<cards.length;i++){if(cards[i].getAttribute('aria-label')==='Household '+id)return cards[i];}
  }catch(e){}
  return q('#panel-households');
}

/* ---------- API observers ---------- */
function bodyOf(init){
  try{if(init&&typeof init.body==='string')return JSON.parse(init.body)||{};}catch(e){}
  return {};
}
function describe(input,init){
  try{
    var url=typeof input==='string'?input:(input&&input.url)||String(input||'');
    var path;
    try{path=new URL(url,window.location.href).pathname;}catch(e){path=String(url).split('?')[0];}
    if(path.indexOf('/api/')!==0)return null;
    return {path:path,body:bodyOf(init)};
  }catch(e){return null;}
}
function sumCost(tools){
  var rows=list(tools&&tools.crusoe&&tools.crusoe.usage);
  var total=0,any=false;
  rows.forEach(function(u){if(u&&typeof u.cost_usd==='number'&&isFinite(u.cost_usd)){total+=u.cost_usd;any=true;}});
  return any?total:null;
}
function forecastNote(f){
  var st=String((f&&f.status)||'');
  var pv=(f&&typeof f.pv_kwh==='number')?String(Math.round(f.pv_kwh*10)/10):String((f&&f.pv_kwh)||'—');
  return 'Forecast: '+String((f&&f.weather_class)||'—')+', '+pv+' kWh · '+
    (/^verified/i.test(st)?'VERIFIED':(st?st.toUpperCase():'NO_VERIFIED_FORECAST'))+' · '+String((f&&f.search_mode)||'—');
}
function keeperNote(v){
  var a=String((v&&v.action)||'?').toLowerCase();
  if(a==='reject')a='rejected';else if(a==='admit')a='admitted';
  return 'Keeper: '+a+(v&&v.code?' ('+String(v.code)+')':'');
}
function onOk(info,data){
  var p=info.path;
  if(p==='/api/run'){
    S.run=data||{};S.runError=null;
    S.memory=info.body.memory_mode||(S.run.run&&S.run.run.brief&&S.run.run.brief.mode)||'curated';
    renderHero();
    if(S.memory==='off')markDone(1);
    if(S.memory==='curated')markDone(2);
    flashLater(function(){return q('#panel-agent');});
  }else if(p==='/api/tools'){
    S.cost=sumCost(data);
    renderHero();
  }else if(p==='/api/forecast'){
    S.note=forecastNote(data);renderHero();markDone(5);
    flashLater(function(){return cardOf('#fc-result');});
  }else if(p==='/api/brain/note'){
    S.note=keeperNote(data);renderHero();markDone(3);
    flashLater(function(){return q('#panel-brain');});
  }else if(p==='/api/household/ask'){
    markDone(4);
    var id=info.body.household_id;
    flashLater(function(){return householdCard(id);});
  }else if(p==='/api/runner/drain'){
    markDone(6);
    flashLater(function(){return cardOf('#nemo-handled');});
  }else if(p==='/api/runner/event'){
    flashLater(function(){return cardOf('#nemo-handled');});
  }else if(p==='/api/reset'){
    S.run=null;S.runError=null;S.memory=null;S.cost=null;S.note='';
    for(var i=0;i<done.length;i++)done[i]=false;
    renderHero();renderSteps();
  }
}
function onRunError(message){
  S.runError=message||'the run failed';
  renderHero();
  flashLater(function(){return hero;});
}
function observe(info,res){
  try{
    if(!info||!res||typeof res.clone!=='function')return;
    var copy=res.clone();                        /* never touch the page's own body */
    var ok=!!res.ok,status=res.status;
    copy.json().then(function(data){
      try{
        if(ok)onOk(info,data);
        else if(info.path==='/api/run')onRunError((data&&data.error)?String(data.error):'HTTP '+status);
      }catch(e){}
    },function(){
      try{if(!ok&&info.path==='/api/run')onRunError('HTTP '+status);}catch(e){}
    });
  }catch(e){}
}
function wrapFetch(){
  var orig=window.fetch;
  if(typeof orig!=='function'||orig.__gsPresenter)return;
  var wrapped=function(input,init){
    var p=orig.apply(window,arguments);
    var info=null;
    try{info=describe(input,init);}catch(e){info=null;}
    if(!info||!p||typeof p.then!=='function')return p;
    return p.then(function(res){
      observe(info,res);                         /* clones before the caller can read the body */
      return res;
    },function(err){
      try{if(info.path==='/api/run')onRunError('network error');}catch(e){}
      throw err;
    });
  };
  try{wrapped.__gsPresenter=true;}catch(e){}
  window.fetch=wrapped;
}

/* ---------- keyboard ---------- */
function isTyping(t){
  try{
    if(!t)return false;
    if(t.isContentEditable)return true;
    var tag=String(t.tagName||'').toUpperCase();
    return tag==='INPUT'||tag==='SELECT'||tag==='TEXTAREA';
  }catch(e){return false;}
}
function onKey(e){
  try{
    if(!e||e.defaultPrevented||e.repeat||e.ctrlKey||e.metaKey||e.altKey)return;
    if(isTyping(e.target)||isTyping(document.activeElement))return;
    if(q('dialog[open]'))return;
    var k=String(e.key||'');
    if(k==='r'||k==='R'){
      var b=q('#btn-run');
      if(b&&!b.disabled){e.preventDefault();b.click();}
    }else if(k==='p'||k==='P'){
      e.preventDefault();togglePresenter();
    }else if(k.length===1&&k>='1'&&k<='6'){
      e.preventDefault();goStep(Number(k)-1);
    }
  }catch(err){}
}

/* ---------- mount ---------- */
function mount(){
  try{
    var top=q('header.top');
    var h=buildHero(),s=buildSteps();
    if(top&&top.parentNode){
      top.parentNode.insertBefore(h,top.nextSibling);
      h.parentNode.insertBefore(s,h.nextSibling);
    }else{
      document.body.insertBefore(s,document.body.firstChild);
      document.body.insertBefore(h,s);
    }
    toggleBtn=mk('button','','Presenter mode');
    toggleBtn.id='gs-presenter-toggle';
    toggleBtn.type='button';
    toggleBtn.title='Toggle presenter mode (P)';
    toggleBtn.addEventListener('click',function(){togglePresenter();});
    document.body.appendChild(toggleBtn);
    setPresenter(readPref(),false);
    renderHero();
    renderSteps();
    document.addEventListener('keydown',onKey);
  }catch(e){}
}

try{wrapFetch();}catch(e){}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);
else mount();

window.GSPresenter={
  toggle:togglePresenter,
  setPresenter:function(on){setPresenter(!!on,true);},
  goStep:function(n){goStep(Number(n)-1);},
  markDone:markDone,
  steps:function(){return done.slice();}
};

}catch(e){}
})();
