"""Execute the shipped dashboard JavaScript; no audio, inference or private sessions."""
import json
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class SharedMeetingContextTests(unittest.TestCase):
    def run_dashboard(self, scenario):
        script = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync('web/index.html','utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1].split('(async()=>{boxes=defaultBoxes();')[0];
const elements=new Map(), storage=new Map(), requests=[], timers=[];
function element(){return {value:'',checked:true,children:[],dataset:{},style:{},classList:{add(){},remove(){},toggle(){}},
 set textContent(v){this.children=[];this.text=v},get textContent(){return this.text||''},
 append(...children){for(const child of children){child.parentElement=this;this.children.push(child)}} ,remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(x=>x!==this)},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},
 setCustomValidity(v){this.validationMessage=v},reportValidity(){return !this.validationMessage}}}
const sandbox={console,crypto:require('node:crypto').webcrypto,
 document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id)},createElement:element},
 localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
 setTimeout(fn){timers.push(fn);return timers.length},clearTimeout(){},
 fetch:async(path,options)=>{requests.push({path,body:options?.body?JSON.parse(options.body):undefined});return {ok:true,json:async()=>({state:'idle',cards:[],analyses:{}})}},
 assert,requests,elements,storage,timers};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
vm.runInContext("boxes=defaultBoxes();finalConfig=defaultFinalConfig();",sandbox);
(async()=>{await vm.runInContext(process.argv[1],sandbox); console.log('PASS')})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(["node", "-e", script, scenario], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_one_editor_applies_context_to_all_cards_including_new_cards(self):
        self.run_dashboard("""
$('meetingContext').value='Projeto sintético: revisão do lançamento';
renderBoxes();
assert.equal($('boxes').children.flatMap(b=>b.children).filter(c=>c['aria-label']==='Observações para o LLM').length,0);
assert.equal(getBoxConfig().length,3);
assert.ok(getBoxConfig().every(b=>b.notes===$('meetingContext').value));
const originalPrompts=getBoxConfig().map(b=>b.prompt);
addBox();assert.equal(getBoxConfig()[3].notes,$('meetingContext').value);
assert.equal(JSON.stringify(getBoxConfig().slice(0,3).map(b=>b.prompt)),JSON.stringify(originalPrompts));
""")

    def test_context_survives_reload_and_explicit_clear(self):
        self.run_dashboard("""
$('meetingContext').value='Contexto sintético comum';persistPrefs();
$('meetingContext').value='';restorePrefs();
assert.equal($('meetingContext').value,'Contexto sintético comum');
$('meetingContext').value='';persistPrefs();
$('meetingContext').value='outro';restorePrefs();
assert.equal($('meetingContext').value,'');
assert.ok(getBoxConfig().every(b=>b.notes===''));
""")

    def test_legacy_contexts_are_deduplicated_and_preserved_without_truncation(self):
        self.run_dashboard("""
const cards=defaultBoxes();cards[0].notes='Contexto A';cards[1].notes='Contexto A';cards[2].notes='Contexto B';
storage.set(BOX_KEY,JSON.stringify(cards));restorePrefs();
assert.equal($('meetingContext').value,'Contexto A\\n\\nContexto B');
persistPrefs();$('meetingContext').value='';restorePrefs();
assert.equal($('meetingContext').value,'Contexto A\\n\\nContexto B');
""")

    def test_edit_during_session_posts_same_context_and_keeps_prompts(self):
        self.run_dashboard("""(async()=>{
latestStatus={session_id:'synthetic-only',state:'recording'};
$('meetingContext').value='Contexto atualizado';
assert.equal(typeof $('meetingContext').oninput,'function');
$('meetingContext').oninput();await timers.at(-1)();await liveBoxSaveQueue;
assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/live-boxes');
assert.ok(requests[0].body.boxes.every(b=>b.notes==='Contexto atualizado'));
assert.equal(requests[0].body.boxes[0].prompt,defaultBoxes()[0].prompt);
})()""")

    def test_oversized_legacy_context_is_not_truncated_or_submitted(self):
        self.run_dashboard("""(async()=>{
const cards=defaultBoxes();cards[0].notes='A'.repeat(1500);cards[1].notes='B'.repeat(1500);
storage.set(BOX_KEY,JSON.stringify(cards));restorePrefs();
assert.equal($('meetingContext').value.length,3002);
assert.equal(validateMeetingContext(),false);
await start();assert.equal(requests.length,0);
latestStatus={session_id:'synthetic',state:'recording'};
scheduleLiveBoxSave();assert.equal(timers.length,0);
persistPrefs();assert.equal(JSON.parse(storage.get(PREF_KEY)).meetingContext.length,3002);
})()""")

    def test_active_session_restores_context_and_edits_are_not_reset_by_polling(self):
        self.run_dashboard("""
const cards=defaultBoxes().map(b=>({...b,notes:'Contexto da sessão sintética'}));
paint({session_id:'synthetic',state:'recording',cards});
assert.equal($('meetingContext').value,'Contexto da sessão sintética');
$('meetingContext').value='Edição em andamento';
paint({session_id:'synthetic',state:'recording',cards});
assert.equal($('meetingContext').value,'Edição em andamento');
""")

    def test_start_and_upload_send_shared_context(self):
        self.run_dashboard("""(async()=>{
$('meetingContext').value='Contexto comum para as perspectivas';
await start();
assert.ok(requests[0].body.boxes.every(b=>b.notes==='Contexto comum para as perspectivas'));
requests.length=0;
await importAudio({name:'synthetic.wav',size:1});
assert.equal(requests[0].path,'/api/upload-config');
assert.ok(requests[0].body.boxes.every(b=>b.notes==='Contexto comum para as perspectivas'));
})()""")


if __name__ == '__main__':
    unittest.main()
