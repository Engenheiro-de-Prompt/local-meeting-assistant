/* Synthetic browser-only uploads; never contacts production or captures audio. */
const {chromium}=require(process.env.MEETING_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=path.join(__dirname,'../web');
 const httpServer=http.createServer((req,res)=>{const file=path.join(root,req.url==='/'?'index.html':req.url.split('?')[0]);if(!file.startsWith(root+path.sep)){res.writeHead(404).end();return}fs.readFile(file,(err,data)=>{res.writeHead(err?404:200,{'Content-Type':file.endsWith('.js')?'text/javascript':'text/html'});res.end(err?'':data)})});
 await new Promise(r=>httpServer.listen(0,'127.0.0.1',r));
 const server=await chromium.launchServer({channel:'chrome',headless:true});
 if(process.platform==='darwin')server.process().once('exit',()=>server.process().stderr?.destroy());
 try{
 const browser=await chromium.connect(server.wsEndpoint()),page=await browser.newPage();
 const errors=[],posts=[];let state='idle',release;
 page.on('pageerror',e=>errors.push(e.message));
 const status=()=>({state,session_id:state==='idle'?null:'synthetic',cards:[],analyses:{},manual_notes:{text:'',items:[]},manual_notes_revision:0});
 await page.route('**/api/**',async route=>{const req=route.request(),url=new URL(req.url());let body={};if(req.method()==='POST')posts.push({path:url.pathname,headers:req.headers(),body:req.postDataBuffer()?.toString()});
 if(url.pathname==='/api/status')body=status();else if(url.pathname==='/api/devices')body={microphones:[],monitors:[]};else if(url.pathname==='/api/models')body={'11435':['synthetic']};else if(url.pathname==='/api/upload-config'){if(release)await new Promise(r=>release=r);body={token:'synthetic-token'}}else if(url.pathname==='/api/transcribe-file')body=status();else if(req.method()==='POST')throw Error('Unexpected mutation '+url.pathname);
 await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)})});
 await page.goto(`http://127.0.0.1:${httpServer.address().port}/`,{waitUntil:'networkidle'});
 const button=page.locator('#transcribeOther'),message=page.locator('#fileStatus');
 async function drag(type,files=[['synthetic.wav','audio/wav'] ],target='#transcribeOther') {return page.evaluate(({type,files,target})=>{const dt=new DataTransfer();for(const [name,mime] of files)dt.items.add(new File(['synthetic bytes'],name,{type:mime}));const e=new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:dt});document.querySelector(target).dispatchEvent(e);return e.defaultPrevented},{type,files,target})}
 assert.equal(await drag('dragenter'),true);assert.equal(await button.evaluate(e=>e.classList.contains('drag-over')),true);
 await page.waitForFunction(()=>getComputedStyle(document.querySelector('#transcribeOther')).backgroundColor==='rgb(36, 76, 66)');const normal=await button.evaluate(e=>getComputedStyle(e).backgroundColor);await drag('dragleave');assert.equal(await button.evaluate(e=>e.classList.contains('drag-over')),false);await page.waitForFunction(color=>getComputedStyle(document.querySelector('#transcribeOther')).backgroundColor!==color,normal);
 await drag('dragenter');await drag('dragend',[],'body');assert.equal(await button.evaluate(e=>e.classList.contains('drag-over')),false);
 assert.equal(await drag('drop',[['wrong.txt','text/plain']]),true);assert.match(await message.textContent(),/áudio/i);assert.equal(posts.length,0);
 await drag('drop',[['one.wav','audio/wav'],['two.mp3','audio/mpeg']]);assert.match(await message.textContent(),/um arquivo/i);assert.equal(posts.length,0);
 await page.evaluate(()=>selectAudio([{name:'huge.wav',type:'audio/wav',size:251*1024*1024}]));assert.match(await message.textContent(),/250 MiB/);assert.equal(posts.length,0);
 await page.locator('#meetingContext').evaluate(e=>{e.value='x'.repeat(2001);e.dispatchEvent(new Event('input',{bubbles:true}))});await drag('drop');assert.equal(posts.length,0);assert.equal(await page.locator('#meetingContext').evaluate(e=>e.validity.valid),false);await page.locator('#meetingContext').fill('');
 assert.equal(await drag('drop',[['outside.wav','audio/wav']],'body'),true);assert.equal(posts.length,0);
 const originalUrl=page.url();await drag('drop');await page.waitForFunction(()=>document.querySelector('#fileStatus').textContent.includes('Arquivo recebido'));
 assert.deepEqual(posts.map(p=>p.path),['/api/upload-config','/api/transcribe-file']);assert.equal(posts[1].headers['x-filename'],'synthetic.wav');assert.equal(posts[1].body,'synthetic bytes');assert.equal(posts[1].headers['x-upload-token'],'synthetic-token');assert.equal(page.url(),originalUrl);assert.equal(await button.evaluate(e=>e.classList.contains('drag-over')),false);
 posts.length=0;
 // Click still opens the native chooser; no dialog or native foreground focus.
 const chooserPromise=page.waitForEvent('filechooser');await button.click();const chooser=await chooserPromise;
 await chooser.setFiles({name:'picker.mp3',mimeType:'audio/mpeg',buffer:Buffer.from('picker bytes')});await page.waitForFunction(()=>document.querySelector('#fileStatus').textContent.includes('Arquivo recebido'));assert.equal(posts[1].headers['x-filename'],'picker.mp3');
 posts.length=0;await page.locator('#audioUpload').setInputFiles({name:'bad.txt',mimeType:'text/plain',buffer:Buffer.from('bad')});assert.match(await message.textContent(),/áudio/i);assert.equal(posts.length,0);
 for(const busy of ['recording','processing','final_analysis']){state=busy;await page.evaluate(s=>paint(s),status());assert.equal(await button.isDisabled(),true);await drag('dragenter');assert.equal(await button.evaluate(e=>e.classList.contains('drag-over')),false);await drag('drop');assert.match(await message.textContent(),/aguarde/i);assert.equal(posts.length,0)}
 state='idle';await page.evaluate(s=>paint(s),status());release=true;await drag('drop');await page.waitForFunction(()=>document.querySelector('#transcribeOther').disabled);await page.evaluate(s=>paint(s),status());assert.equal(await button.isDisabled(),true);await drag('drop');assert.match(await message.textContent(),/aguarde/i);await page.waitForTimeout(100);assert.equal(posts.length,1);release();release=null;await page.waitForFunction(()=>!document.querySelector('#transcribeOther').disabled);
 assert.deepEqual(errors,[]);console.log('Chrome upload drop: highlight/clear, validation, picker parity, busy/reentrant guards, navigation prevention OK');
 }finally{await server.close();await new Promise(r=>httpServer.close(r))}
})().catch(e=>{console.error(e);process.exitCode=1});
