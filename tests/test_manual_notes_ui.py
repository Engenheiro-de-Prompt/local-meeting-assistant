"""Run the real notes controller with synthetic DOM/storage/HTTP boundaries."""
import json
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

HARNESS = r"""
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
class Element {
 constructor(){this.value='';this.checked=false;this.children=[];this.style={};this.dataset={};this.disabled=false;this.classList={toggle(){}};}
 set textContent(v){this.text=v;this.children=[]} get textContent(){return this.text||''}
 append(...nodes){this.children.push(...nodes)} replaceChildren(...nodes){this.children=nodes}
 setAttribute(k,v){this[k]=v} focus(){} addEventListener(){}
}
const nodes=new Map(),storage=new Map(),tabStorage=new Map(),requests=[],timers=[];
const document={getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id)},createElement(){return new Element()}};
let server={session_id:'meeting-a',state:'completed',manual_notes:{text:'',items:[]},manual_notes_revision:0};
const env={document,console,crypto:require('node:crypto').webcrypto,JSON,Map,Set,Number,Promise,
 sessionStorage:{getItem:k=>tabStorage.get(k)??null,setItem:(k,v)=>tabStorage.set(k,v)},
 localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
 setTimeout:fn=>{timers.push(fn);return timers.length},clearTimeout(){},
 fetch:async(path,opt)=>{if(!opt||opt.method!=='POST')return {ok:true,status:200,json:async()=>server};const body=JSON.parse(opt.body);requests.push({path,body});
 if(body.session_id!==server.session_id||body.expected_revision!==server.manual_notes_revision)return {ok:false,status:409,json:async()=>({error:'Conflict'})};
 server={...server,manual_notes:body.notes,manual_notes_revision:server.manual_notes_revision+1};return {ok:true,status:200,json:async()=>server};}};
env.window=env;vm.createContext(env);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),env);
const $=document.getElementById.bind(document);
"""

class ManualNotesUITests(unittest.TestCase):
    def run_ui(self, script):
        path = ROOT / 'web/manual-notes.js'
        self.assertTrue(path.is_file(), 'Manual notes controller is not implemented')
        result = subprocess.run(['node', '-e', HARNESS + '\n' + script, str(path)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_session_edits_persist_with_revision_without_poll_overwriting_typing(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();env.MeetingNotes.sync(server);
$('manualText').value='Observação manual da reunião A';$('manualText').oninput();
env.MeetingNotes.sync({...server,manual_notes:{text:'Texto antigo',items:[]}});
assert.equal($('manualText').value,'Observação manual da reunião A');
await env.MeetingNotes.flush();
assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/manual-notes');
assert.equal(requests[0].body.session_id,'meeting-a');assert.equal(requests[0].body.expected_revision,0);
assert.equal(server.manual_notes.text,'Observação manual da reunião A');
assert.equal(env.MeetingNotes.forStart().text,'');
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_draft_is_preserved_until_successful_start_and_not_carried_to_another_meeting(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();$('manualText').value='Preparação';$('manualText').oninput();
env.MeetingNotes.sync(server);assert.equal($('manualText').value,'Preparação');
const initial=env.MeetingNotes.forStart();
server={...server,session_id:'meeting-b',state:'recording',manual_notes:initial};
$('manualText').value='Editado durante o início';$('manualText').oninput();
env.MeetingNotes.started(server);await env.MeetingNotes.flush();
assert.equal(server.manual_notes.text,'Editado durante o início');assert.equal(env.MeetingNotes.forStart().text,'');
server={...server,state:'completed'};env.MeetingNotes.sync(server);
await $('newManualDraft').onclick();assert.equal($('manualText').value,'');
$('manualText').value='Preparar próxima reunião';$('manualText').oninput();
env.MeetingNotes.sync(server);assert.equal($('manualText').value,'Preparar próxima reunião');
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_concurrent_tab_conflict_requires_explicit_resolution(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();env.MeetingNotes.sync(server);
$('manualText').value='Minha versão';$('manualText').oninput();
server={...server,manual_notes:{text:'Versão da outra aba',items:[]},manual_notes_revision:1};
await assert.rejects(env.MeetingNotes.flush());env.MeetingNotes.sync(server);
assert.equal($('manualText').value,'Minha versão');assert.equal($('manualConflict').hidden,false);
await $('manualKeepMine').onclick();await env.MeetingNotes.flush();
assert.equal(server.manual_notes.text,'Minha versão');assert.equal(server.manual_notes_revision,2);
$('manualText').value='Outra edição local';$('manualText').oninput();
server={...server,manual_notes:{text:'Versão no disco',items:[]},manual_notes_revision:3};
await assert.rejects(env.MeetingNotes.flush());env.MeetingNotes.sync(server);
$('manualUseSaved').onclick();assert.equal($('manualText').value,'Versão no disco');
assert.ok([...storage.values()].some(x=>x.includes('Outra edição local')));
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_another_tabs_save_does_not_erase_pending_edits_on_reload(self):
        self.run_ui(r"""(async()=>{
const source=fs.readFileSync('web/manual-notes.js','utf8');
function otherTab(previousSessionStorage){
 const el=new Map(),ss=previousSessionStorage||new Map();
 const tab={...env,document:{getElementById(id){if(!el.has(id))el.set(id,new Element());return el.get(id)},createElement:()=>new Element()},sessionStorage:{getItem:k=>ss.get(k)||null,setItem:(k,v)=>ss.set(k,v)}};
 tab.globalThis=tab;vm.runInNewContext(source,tab);return {tab,ss,$:id=>tab.document.getElementById(id)};
}
env.MeetingNotes.init();env.MeetingNotes.sync(server);
const b=otherTab();b.tab.MeetingNotes.init();b.tab.MeetingNotes.sync(server);
b.$('manualText').value='Edição pendente da aba B';b.$('manualText').oninput();
$('manualText').value='Versão salva pela aba A';$('manualText').oninput();await env.MeetingNotes.flush();
b.tab.MeetingNotes.sync(server);assert.equal(b.$('manualConflict').hidden,false);
const reload=otherTab(b.ss);reload.tab.MeetingNotes.init();reload.tab.MeetingNotes.sync(server);
assert.equal(reload.$('manualText').value,'Edição pendente da aba B');assert.equal(reload.$('manualConflict').hidden,false);
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_flush_drains_edits_made_while_previous_save_is_pending(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();env.MeetingNotes.sync(server);
$('manualText').value='Primeiro texto';$('manualText').oninput();
const original=env.fetch;let once=false;
env.fetch=async(...args)=>{const response=await original(...args);if(!once){once=true;$('manualText').value='Última edição antes da troca';$('manualText').oninput()}return response};
await env.MeetingNotes.flush();assert.equal(server.manual_notes.text,'Última edição antes da troca');
assert.equal(requests.length,2);assert.equal(requests[1].body.expected_revision,1);
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_quota_and_network_failure_keep_warning_and_prevent_abandoning_notes(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();env.MeetingNotes.sync(server);
env.localStorage.setItem=()=>{throw new Error('Quota exceeded')};
$('manualText').value='Só existe nesta página';$('manualText').oninput();
env.fetch=async()=>{throw new Error('Offline')};await assert.rejects(env.MeetingNotes.flush());
env.MeetingNotes.sync(server);
assert.match($('manualStatus').textContent,/copie.*anotações/i);
assert.doesNotMatch($('manualStatus').textContent,/preservad[ao].*neste navegador/i);
await $('newManualDraft').onclick();assert.equal($('manualText').value,'Só existe nesta página');
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_draft_survives_repeated_reload_without_intervening_edits(self):
        self.run_ui(r"""
env.MeetingNotes.init();$('manualText').value='Preparação persistente';$('manualText').oninput();
$('addManualItem').onclick();const row=$('manualItems').children[0];
row.children[1].value='Levar pauta';row.children[1].oninput();row.children[0].checked=true;row.children[0].onchange();
const expected=JSON.stringify(env.MeetingNotes.forStart());
for(let i=0;i<4;i++){
 const reloaded={...env};reloaded.window=reloaded;vm.createContext(reloaded);
 vm.runInContext(fs.readFileSync('web/manual-notes.js','utf8'),reloaded);reloaded.MeetingNotes.init();
 reloaded.MeetingNotes.sync(server);
 assert.equal($('manualText').value,'Preparação persistente',`reload ${i+1}`);
 assert.equal(JSON.stringify(reloaded.MeetingNotes.forStart()),expected,`checklist reload ${i+1}`);
}
assert.equal(requests.length,0);
""")

    def test_recovered_draft_copy_failure_warns_and_retains_content(self):
        self.run_ui(r"""
env.MeetingNotes.init();$('manualText').value='Rascunho recuperado sem espaço';$('manualText').oninput();
const reloaded={...env,localStorage:{...env.localStorage,setItem(){throw new Error('Quota exceeded')}}};
reloaded.window=reloaded;vm.createContext(reloaded);
vm.runInContext(fs.readFileSync('web/manual-notes.js','utf8'),reloaded);reloaded.MeetingNotes.init();
assert.equal($('manualText').value,'Rascunho recuperado sem espaço');
assert.equal(reloaded.MeetingNotes.forStart().text,'Rascunho recuperado sem espaço');
assert.match($('manualStatus').textContent,/copie.*anotações/i);
assert.doesNotMatch($('manualStatus').textContent,/rascunho salvo/i);
assert.ok([...storage.values()].some(v=>v.includes('Rascunho recuperado sem espaço')));
""")

    def test_existing_meeting_editor_is_locked_during_start_handoff(self):
        self.run_ui(r"""
env.MeetingNotes.init();env.MeetingNotes.beginStart();assert.notEqual($('manualText').readOnly,true);
env.MeetingNotes.cancelStart();env.MeetingNotes.sync(server);
env.MeetingNotes.beginStart();assert.equal($('manualText').readOnly,true);assert.equal($('addManualItem').disabled,true);
env.MeetingNotes.cancelStart();assert.equal($('manualText').readOnly,false);assert.equal($('addManualItem').disabled,false);
env.MeetingNotes.beginStart();server={...server,session_id:'meeting-b'};env.MeetingNotes.started(server);
assert.equal($('manualText').readOnly,false);assert.equal($('addManualItem').disabled,false);
""")

    def test_missing_tab_storage_warns_that_reload_recovery_is_unavailable(self):
        self.run_ui(r"""
const tab={...env,sessionStorage:{getItem(){throw new Error('Blocked')},setItem(){throw new Error('Blocked')}}};
tab.globalThis=tab;vm.runInNewContext(fs.readFileSync('web/manual-notes.js','utf8'),tab);
tab.MeetingNotes.init();$('manualText').value='Manter enquanto a aba está aberta';$('manualText').oninput();
assert.match($('manualStatus').textContent,/copie.*anotações/i);
assert.doesNotMatch($('manualStatus').textContent,/rascunho salvo/i);
""")

    def test_new_draft_first_flushes_pending_notes_of_previous_meeting(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();env.MeetingNotes.sync(server);
$('manualText').value='Última observação da reunião anterior';$('manualText').oninput();
await $('newManualDraft').onclick();
assert.equal(server.manual_notes.text,'Última observação da reunião anterior');
assert.equal($('manualText').value,'');assert.equal(env.MeetingNotes.forStart().text,'');
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_poll_during_start_does_not_consume_or_replace_pending_draft(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();$('manualText').value='Enviado ao iniciar';$('manualText').oninput();
env.MeetingNotes.beginStart();
$('manualText').value='Editado enquanto iniciava';$('manualText').oninput();
server={...server,session_id:'session-start-race',state:'recording',manual_notes:{text:'Enviado ao iniciar',items:[]}};
env.MeetingNotes.sync(server);assert.equal($('manualText').value,'Editado enquanto iniciava');
env.MeetingNotes.started(server);await env.MeetingNotes.flush();
assert.equal(server.manual_notes.text,'Editado enquanto iniciava');
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_stale_poll_and_own_save_ack_do_not_create_false_conflicts(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();env.MeetingNotes.sync(server);
const original=env.fetch;env.fetch=async(...args)=>{const response=await original(...args);env.MeetingNotes.sync(server);return response};
$('manualText').value='Primeira edição';$('manualText').oninput();await env.MeetingNotes.flush();
assert.equal($('manualConflict').hidden,true);
$('manualText').value='Segunda edição';$('manualText').oninput();
env.MeetingNotes.sync({...server,manual_notes_revision:0,manual_notes:{text:'',items:[]}});
assert.equal($('manualConflict').hidden,true);await env.MeetingNotes.flush();
assert.equal(server.manual_notes.text,'Segunda edição');
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_offline_save_preserves_edits_and_reports_failure(self):
        self.run_ui(r"""(async()=>{
env.MeetingNotes.init();env.MeetingNotes.sync(server);
$('manualText').value='Não perder esta observação';$('manualText').oninput();
const online=env.fetch;env.fetch=async()=>{throw new Error('Network unavailable')};
await assert.rejects(env.MeetingNotes.flush());
assert.match($('manualStatus').textContent,/indisponível|não foi possível/i);
assert.equal($('manualText').value,'Não perder esta observação');
env.fetch=online;env.MeetingNotes.init();env.MeetingNotes.sync(server);await env.MeetingNotes.flush();
assert.equal(server.manual_notes.text,'Não perder esta observação');
})().catch(e=>{console.error(e);process.exitCode=1});""")

    def test_draft_text_and_checklist_survive_reload_without_network(self):
        self.run_ui(r"""
env.MeetingNotes.init();$('manualText').value='Minha anotação local';$('manualText').oninput();
$('addManualItem').onclick();const row=$('manualItems').children[0];
row.children[1].value='Confirmar orçamento';row.children[1].oninput();row.children[0].checked=true;row.children[0].onchange();
assert.equal(env.MeetingNotes.forStart().text,'Minha anotação local');
assert.equal(env.MeetingNotes.forStart().items[0].checked,true);
assert.equal(requests.length,0);
env.MeetingNotes.init();assert.equal($('manualText').value,'Minha anotação local');
assert.equal(env.MeetingNotes.forStart().items[0].text,'Confirmar orçamento');
""")

if __name__ == '__main__':
    unittest.main()
