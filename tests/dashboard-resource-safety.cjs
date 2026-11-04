/* Synthetic, loopback-only UI fixture: no production API, devices or models. */
const {chromium}=require(process.env.MEETING_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=path.resolve(__dirname,'../web');
 const fixture=http.createServer((req,res)=>{const file=path.resolve(root,'.'+(req.url==='/'?'/index.html':req.url.split('?')[0]));if(!file.startsWith(root+path.sep)){res.writeHead(404).end();return}fs.readFile(file,(err,data)=>{res.writeHead(err?404:200,{'Content-Type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});res.end(err?'':data)})});
 await new Promise(r=>fixture.listen(0,'127.0.0.1',r));
 let server;
 try{
 server=await chromium.launchServer({channel:'chrome',headless:true});
 if(process.platform==='darwin')server.process().once('exit',()=>server.process().stderr?.destroy());
 const browser=await chromium.connect(server.wsEndpoint()),page=await browser.newPage(),errors=[],requests=[];
 let state='idle',guard={level:0,level_name:'normal',reason:'synthetic healthy',llm_allowed:true,transcription_allowed:true,requested_analysis_interval_s:127,requested_transcription_interval_s:73,effective_analysis_interval_s:127,effective_transcription_interval_s:73,capture_alive:true,deferred_chunks:0},failGuard=false;
 const status=()=>({state,session_id:state==='idle'?null:'synthetic',analysis_interval_s:127,transcription_interval_s:73,transcript:state==='idle'?'':'synthetic transcript',transcript_file:state==='idle'?null:'synthetic.txt',transcription_mode:'live',live_analysis_enabled:true,cards:[],analyses:{},manual_notes:{text:'',items:[]},manual_notes_revision:0,resource_guard:guard});
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{const req=route.request(),url=new URL(req.url()),isPost=req.method()==='POST';requests.push({path:url.pathname,method:req.method(),body:isPost&&req.headers()['content-type']==='application/json'?req.postDataJSON():null});let body;
 if(url.pathname==='/api/status')body=status();
 else if(url.pathname==='/api/devices')body={microphones:[],monitors:[]};
 else if(url.pathname==='/api/models')body={'11435':['synthetic']};
 else if(url.pathname==='/api/start'){state='recording';body=status()}
 else if(url.pathname==='/api/upload-config')body={token:'synthetic-token'};
 else if(url.pathname==='/api/transcribe-file'){state='processing';body=status()}
 else if(url.pathname==='/api/analyze')body=status();
 else if(url.pathname==='/api/resource-guard'){if(failGuard){await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'synthetic unavailable'})});return}guard={...guard,level:4,level_name:'recording_only',recording_only:true,reason:'user_recording_only',llm_allowed:false,transcription_allowed:false,effective_analysis_interval_s:null,effective_transcription_interval_s:null};body=status()}
 else throw Error('Unexpected API: '+url.pathname);
 await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)})});
 await page.goto(`http://127.0.0.1:${fixture.address().port}/`,{waitUntil:'networkidle'});
 assert.equal(await page.locator('#interval').getAttribute('type'),'number');assert.equal(await page.locator('#transcription_interval_s').inputValue(),'45');
 const posts=()=>requests.filter(r=>r.method==='POST');
 for(const [id,values] of [['interval',['','59','601','60.5']],['transcription_interval_s',['','9','3601','10.5']]])for(const value of values){await page.locator('#interval').fill('180');await page.locator('#transcription_interval_s').fill('45');await page.locator('#'+id).fill(value);assert.equal(await page.locator('#'+id).evaluate(e=>e.checkValidity()),false);await page.locator('#start').click();await page.evaluate(()=>importAudio(new File(['synthetic bytes'],'synthetic.wav',{type:'audio/wav'})));assert.equal(posts().length,0)}
 await page.locator('#interval').fill('127');await page.locator('#interval').dispatchEvent('change');await page.locator('#transcription_interval_s').fill('73');await page.locator('#transcription_interval_s').dispatchEvent('change');
 await page.reload({waitUntil:'networkidle'});assert.equal(await page.locator('#interval').inputValue(),'127');assert.equal(await page.locator('#transcription_interval_s').inputValue(),'73');
 await page.locator('#start').click();await page.waitForFunction(()=>document.querySelector('#state').textContent.includes('Gravando'));
 const start=posts().find(p=>p.path==='/api/start');assert.equal(start.body.analysis_interval_s,127);assert.equal(start.body.transcription_interval_s,73);
 await page.locator('#analyze').click();assert.equal(posts().filter(p=>p.path==='/api/analyze').length,1);
 guard={...guard,level:3,level_name:'transcription_deferred',llm_allowed:false,transcription_allowed:false,reason:'<img src=x onerror=alert(1)> synthetic pressure',effective_analysis_interval_s:null,effective_transcription_interval_s:null,deferred_chunks:19};
 await page.evaluate(s=>paint(s),status());assert.equal(await page.locator('#analyze').isDisabled(),true);await page.evaluate(()=>analyze());assert.equal(posts().filter(p=>p.path==='/api/analyze').length,1);
 assert.match(await page.locator('#resourceGuardIntervals').textContent(),/127 s.*suspenso.*73 s.*suspenso/);assert.match(await page.locator('#resourceGuardBacklog').textContent(),/19/);assert.equal(await page.locator('#resourceGuardReason img').count(),0);
 // Failed writes must not claim success, and the action remains available.
 failGuard=true;await page.locator('#recordingOnly').click();await page.waitForFunction(()=>document.querySelector('#resourceGuardError').textContent.includes('synthetic unavailable'));assert.equal(await page.locator('#recordingOnly').isDisabled(),false);failGuard=false;
 await page.locator('#recordingOnly').click();await page.waitForFunction(()=>document.querySelector('#resourceGuardLevel').textContent.includes('recording_only'));assert.equal(await page.locator('#recordingOnly').isDisabled(),true);assert.equal(await page.locator('#stop').isDisabled(),false);
 const mutation=requests.findLastIndex(r=>r.path==='/api/resource-guard');assert.deepEqual(requests[mutation].body,{recording_only:true});assert.ok(requests.slice(mutation+1).some(r=>r.path==='/api/status'&&r.method==='GET'));
 guard=undefined;state='idle';await page.evaluate(s=>paint(s),status());assert.match(await page.locator('#resourceGuardLevel').textContent(),/indisponível/);assert.equal(await page.locator('#analyze').isDisabled(),true);
 // Import uses the same two configured values, not hard-coded defaults.
 await page.locator('#audioUpload').setInputFiles({name:'synthetic.wav',mimeType:'audio/wav',buffer:Buffer.from('synthetic bytes')});await page.waitForFunction(()=>document.querySelector('#fileStatus').textContent.includes('Arquivo recebido'));
 const upload=posts().find(p=>p.path==='/api/upload-config');assert.equal(upload.body.analysis_interval_s,127);assert.equal(upload.body.transcription_interval_s,73);
 assert.deepEqual(errors,[]);console.log('Chrome resource safety: numeric bounds, prefs reload, start/import payloads, requested/effective/backlog, fail-closed analyze, recording-only POST/readback and errors OK');
 }finally{if(server)await server.close();await new Promise(r=>fixture.close(r))}
})().catch(e=>{console.error(e);process.exitCode=1});
