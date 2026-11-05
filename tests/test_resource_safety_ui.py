"""Execute dashboard JavaScript with synthetic DOM/API; never touch a meeting."""
import json
import os
import pathlib
import re
import subprocess
import unittest
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Inputs(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.fields = {}
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.fields[attrs['id']] = {'tag': tag, **attrs}


class ResourceSafetyUiTests(unittest.TestCase):
    def setUp(self):
        self.page = (ROOT / 'web/index.html').read_text()
        self.fields = Inputs(self.page).fields

    def run_js(self, code):
        script = re.findall(r'<script>(.*?)</script>', self.page, re.S)[0]
        # Run real declarations/event bindings, but not device discovery or polling.
        script = script[:script.rfind('(async()=>{boxes=defaultBoxes();')]
        harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const fields=FIELDS,nodes={},storage={},posts=[];
for(const [id,a] of Object.entries(fields))nodes[id]={value:a.value||'',checked:'checked' in a,disabled:'disabled' in a,textContent:'',dataset:{},classList:{remove(){},toggle(){}},setCustomValidity(s){this.validationMessage=s},reportValidity(){return this.checkValidity()},checkValidity(){if(this.validationMessage)return false;if(a.type!=='number')return true;const n=Number(this.value);return this.value!==''&&Number.isFinite(n)&&Number.isInteger(n)&&n>=Number(a.min)&&n<=Number(a.max)}};
const context=vm.createContext({console,assert,posts,nodes,storage,setTimeout,clearTimeout,URL,document:{getElementById:id=>nodes[id]},localStorage:{getItem:k=>storage[k]||null,setItem:(k,v)=>storage[k]=v},fetch:async(path,opts={})=>{posts.push({path,body:opts.headers?.['Content-Type']==='application/json'?JSON.parse(opts.body):opts.body});return {ok:true,json:async()=>path==='/api/upload-config'?{token:'synthetic'}:{state:'recording',session_id:'synthetic'}}}});
vm.runInContext(SCRIPT,context);
vm.runInContext("paint=s=>{latestStatus=s};getFinalConfig=()=>({});renderBoxes=()=>{};restoreMeetingContext=()=>{};",context);
(async()=>{await vm.runInContext('(async()=>{'+CODE+'})()',context)})().catch(e=>{console.error(e);process.exitCode=1});
'''
        harness = harness.replace('FIELDS', json.dumps(self.fields)).replace('SCRIPT', json.dumps(script)).replace('CODE', json.dumps(code))
        result = subprocess.run(['node', '-e', harness], text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_numeric_intervals_have_bounds_and_defaults(self):
        for field, low, high, default in [('interval', '60', '600', '180'), ('transcription_interval_s', '10', '3600', '45')]:
            with self.subTest(field=field):
                attrs = self.fields.get(field, {})
                self.assertEqual(attrs.get('tag'), 'input')
                self.assertEqual(attrs.get('type'), 'number')
                self.assertEqual((attrs.get('min'), attrs.get('max'), attrs.get('step'), attrs.get('value')), (low, high, '1', default))
                self.assertIn('required', attrs)


    def test_start_and_import_send_configured_seconds(self):
        self.run_js('''
nodes.interval.value='127';nodes.transcription_interval_s.value='73';
await start();assert.equal(posts[0].path,'/api/start');
assert.equal(posts[0].body.analysis_interval_s,127);assert.equal(posts[0].body.transcription_interval_s,73);
latestStatus={state:'idle'};await importAudio({name:'synthetic.wav',size:20});
assert.equal(posts[1].path,'/api/upload-config');assert.equal(posts[1].body.analysis_interval_s,127);assert.equal(posts[1].body.transcription_interval_s,73);
assert.equal(posts[2].path,'/api/transcribe-file');
''')

    def test_invalid_intervals_block_start_and_import(self):
        self.run_js('''
for(const [id,invalid] of [['interval',['','59','601','60.5','NaN','Infinity']],['transcription_interval_s',['','9','3601','10.5','NaN','Infinity']]]){
 for(const value of invalid){nodes.interval.value='180';nodes.transcription_interval_s.value='45';nodes[id].value=value;latestStatus={state:'idle'};await start();await importAudio({name:'synthetic.wav',size:20});assert.equal(posts.length,0,id+'='+value)}
}
''')

    def test_preferences_restore_arbitrary_valid_seconds_and_reject_corruption(self):
        self.run_js('''
nodes.interval.value='127';nodes.transcription_interval_s.value='73';persistPrefs();
nodes.interval.value='180';nodes.transcription_interval_s.value='45';restorePrefs();assert.equal(Number(nodes.interval.value),127);assert.equal(Number(nodes.transcription_interval_s.value),73);
for(const invalid of ['',null,true,{},'Infinity',-1,1.5,9999]){storage[PREF_KEY]=JSON.stringify({version:3,interval:invalid,transcription_interval_s:invalid});nodes.interval.value='180';nodes.transcription_interval_s.value='45';restorePrefs();assert.equal(Number(nodes.interval.value),180);assert.equal(Number(nodes.transcription_interval_s.value),45)}
''')


    def test_resource_status_shows_effective_requested_backlog_and_unknowns(self):
        self.run_js('''
renderResourceGuard({state:'recording',analysis_interval_s:127,transcription_interval_s:73,resource_guard:{level:3,level_name:'transcription_deferred',reason:'synthetic pressure',requested_analysis_interval_s:127,requested_transcription_interval_s:73,effective_analysis_interval_s:null,effective_transcription_interval_s:null,deferred_chunks:12,capture_alive:true,llm_allowed:false}});
assert.match(nodes.resourceGuardLevel.textContent,/3.*transcription_deferred/);assert.match(nodes.resourceGuardReason.textContent,/synthetic pressure/);
assert.match(nodes.resourceGuardIntervals.textContent,/127 s/);assert.match(nodes.resourceGuardIntervals.textContent,/73 s/);assert.match(nodes.resourceGuardIntervals.textContent,/suspenso/);assert.match(nodes.resourceGuardBacklog.textContent,/12/);assert.match(nodes.resourceGuardCapture.textContent,/ativa/);
renderResourceGuard({resource_guard:{level:2,level_name:'llm_paused',effective_analysis_interval_s:null,effective_transcription_interval_s:146,inference_kind:'whisper',llm_release_state:'unverified',capture_warning:'synthetic capture warning',processing_error:'synthetic processing error'}});assert.match(nodes.resourceGuardIntervals.textContent,/146 s/);assert.match(nodes.resourceGuardWork.textContent,/whisper.*unverified/);assert.match(nodes.resourceGuardCapture.textContent,/synthetic capture warning/);assert.match(nodes.resourceGuardWork.textContent,/synthetic processing error/);
renderResourceGuard({state:'idle'});assert.match(nodes.resourceGuardLevel.textContent,/indisponível/);assert.match(nodes.resourceGuardIntervals.textContent,/indisponível/);assert.match(nodes.resourceGuardBacklog.textContent,/desconhecido/);
''')

    def test_audio_health_is_not_inferred_from_live_process(self):
        self.run_js('''
renderResourceGuard({resource_guard:{level:4,capture_alive:true,audio_safety:{state:'stalled',duration_s:45,lag_s:17,last_growth_age_s:16}}});
assert.match(nodes.resourceGuardCapture.textContent,/PCM: sem avanço/);
assert.match(nodes.resourceGuardCapture.textContent,/45 s gravados/);
assert.match(nodes.resourceGuardCapture.textContent,/atraso 17 s/);
renderResourceGuard({resource_guard:{level:0,capture_alive:true}});
assert.match(nodes.resourceGuardCapture.textContent,/PCM: não verificado/);
''')

    def test_analyze_now_is_fail_closed_and_never_posts_when_protected(self):
        self.run_js('''
const base={state:'recording',transcript:'synthetic',live_analysis_enabled:true,transcription_mode:'live'};
for(const guard of [undefined,{}, {level:4,llm_allowed:false,recording_only:true},{level:1,llm_allowed:true},{level:0,llm_allowed:true,telemetry_stale:true}]){latestStatus={...base,resource_guard:guard};await analyze();assert.equal(posts.length,0)}
latestStatus={...base,resource_guard:{level:0,llm_allowed:true}};await analyze();assert.equal(posts.length,1);assert.equal(posts[0].path,'/api/analyze');
''')

    def test_failed_status_refresh_blocks_stale_manual_analysis(self):
        self.run_js('''
latestStatus={state:'recording',transcript:'synthetic',live_analysis_enabled:true,transcription_mode:'live',resource_guard:{level:0,llm_allowed:true}};
assert.equal(canAnalyze(latestStatus),true);api=async()=>{throw Error('synthetic disconnected')};await refresh();assert.equal(canAnalyze(latestStatus),false);assert.equal(nodes.analyze.disabled,true);
''')

    def test_recording_only_request_blocks_manual_analysis_while_pending(self):
        self.run_js('''
latestStatus={state:'recording',session_id:'synthetic',transcript:'synthetic',live_analysis_enabled:true,transcription_mode:'live',resource_guard:{level:0,llm_allowed:true}};
let release;const originalApi=api;api=(...args)=>args[0]==='/api/resource-guard'?new Promise(r=>release=r):originalApi(...args);const pending=recordingOnly();assert.equal(canAnalyze(latestStatus),false);await analyze();assert.equal(posts.length,0);release({});await pending;
''')

    def test_recording_only_posts_explicit_boolean_and_reads_back_status(self):
        self.run_js('''
latestStatus={state:'recording',session_id:'synthetic'};await recordingOnly();
assert.equal(posts[0].path,'/api/resource-guard');assert.equal(posts[0].body.recording_only,true);
assert.equal(posts[1].path,'/api/status');
''')


@unittest.skipUnless(os.environ.get('MEETING_PLAYWRIGHT_MODULE'), 'Set MEETING_PLAYWRIGHT_MODULE to run Chrome integration')
class ResourceSafetyBrowserTests(unittest.TestCase):
    def test_dashboard_with_isolated_api_fixtures(self):
        result = subprocess.run(['node', 'tests/dashboard-resource-safety.cjs'], cwd=ROOT, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
