/* Optional browser contract runner: MEETING_PLAYWRIGHT_MODULE=/path/to/playwright node tests/dashboard-workspace.cjs */
const {chromium}=require(process.env.MEETING_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const base=process.env.MEETING_TEST_URL||'http://127.0.0.1:18766';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let state={state:'idle',cards:[],analyses:{},manual_notes:{text:'',items:[]},manual_notes_revision:0};
  const writes=[];
  await page.route('**/api/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname;let data={};
   if(path==='/api/models')data={'11435':['synthetic-model'],'11434':[]};
   else if(path==='/api/devices')data={microphones:[{id:'default',desc:'Synthetic input'}],monitors:[]};
   else if(path==='/api/status')data=state;
   else if(path==='/api/start'){
    const body=request.postDataJSON();writes.push({path,body});state={...state,state:'recording',session_id:'synthetic-meeting',cards:body.boxes,manual_notes:body.manual_notes};data=state;
   }else if(path==='/api/manual-notes'){
    const body=request.postDataJSON();writes.push({path,body});assert.equal(body.session_id,state.session_id);assert.equal(body.expected_revision,state.manual_notes_revision);
    state={...state,manual_notes:body.notes,manual_notes_revision:state.manual_notes_revision+1};data=state;
   }else if(path==='/api/live-boxes'){
    const body=request.postDataJSON();writes.push({path,body});state={...state,cards:body.boxes};data=state;
   }else if(path==='/api/stop'){state={...state,state:'completed'};data=state;}
   else throw new Error('Unexpected fixture API: '+path);
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(base,{waitUntil:'networkidle'});
  assert.equal(await page.locator('#dashboard > [data-layout-id]').first().getAttribute('id'),'notesPanel');
  await page.locator('#manualText').fill('MANUAL_ONLY_SYNTHETIC_NOTE');
  await page.locator('#addManualItem').click();
  await page.getByLabel('Texto do item do checklist',{exact:true}).fill('Confirmar decisão');
  await page.getByLabel('Concluir item do checklist',{exact:true}).check();
  for(let reload=0;reload<3;reload++){
    await page.reload({waitUntil:'networkidle'});
    assert.equal(await page.locator('#manualText').inputValue(),'MANUAL_ONLY_SYNTHETIC_NOTE',`draft reload ${reload+1}`);
    assert.equal(await page.getByLabel('Texto do item do checklist',{exact:true}).inputValue(),'Confirmar decisão');
    assert.equal(await page.getByLabel('Concluir item do checklist',{exact:true}).isChecked(),true);
  }
  await page.locator('#meetingContext').fill('Contexto comum sintético');
  await page.locator('#start').click();
  await page.waitForFunction(()=>document.querySelector('#state').textContent.includes('Gravando'));
  const started=writes.find(x=>x.path==='/api/start').body;
  assert.equal(started.manual_notes.text,'MANUAL_ONLY_SYNTHETIC_NOTE');
  assert.ok(!JSON.stringify(started.boxes).includes('MANUAL_ONLY_SYNTHETIC_NOTE'));
  assert.ok(!JSON.stringify(started.final_analysis_config).includes('MANUAL_ONLY_SYNTHETIC_NOTE'));
  await page.locator('#manualText').fill('Observação atualizada');
  await page.waitForFunction(()=>document.querySelector('#manualStatus').textContent.includes('salvas junto'));
  assert.equal(state.manual_notes.text,'Observação atualizada');
  const grip=page.locator('#notesPanel > .layout-tools .layout-grip');
  await grip.focus();await grip.press('ArrowDown');
  assert.equal(await page.locator('#dashboard > [data-layout-id]').first().getAttribute('id'),'controlPanel');
  const resize=page.locator('#notesPanel > .layout-resize');
  await resize.focus();await resize.press('ArrowLeft');await resize.press('ArrowDown');
  assert.equal(await page.locator('#notesPanel').evaluate(e=>e.style.gridColumn),'span 11');
  const height=await page.locator('#notesPanel').evaluate(e=>e.style.height);assert.ok(height.endsWith('px'));
  const live=page.locator('#analyses > [data-layout-id]');assert.equal(await live.count(),3);
  await live.first().locator(':scope > .layout-tools .layout-grip').focus();
  await page.keyboard.press('ArrowDown');
  const firstLive=await live.first().getAttribute('data-layout-id');assert.equal(firstLive,'live:actions');
  await page.waitForTimeout(2200);
  assert.equal(await live.first().getAttribute('data-layout-id'),firstLive);
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('#manualText').inputValue(),'Observação atualizada');
  assert.equal(await page.locator('#dashboard > [data-layout-id]').first().getAttribute('id'),'controlPanel');
  assert.equal(await page.locator('#notesPanel').evaluate(e=>e.style.gridColumn),'span 11');
  assert.equal(await live.first().getAttribute('data-layout-id'),firstLive);
  await page.locator('#addBox').click();
  await page.waitForFunction(()=>document.querySelectorAll('#analyses > [data-layout-id]').length===4);
  assert.equal(await live.first().getAttribute('data-layout-id'),firstLive);
  await page.locator('#boxes .boxedit').last().getByRole('button',{name:'Remover',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('#analyses > [data-layout-id]').length===3);
  assert.equal(await live.first().getAttribute('data-layout-id'),firstLive);
  await page.locator('#manualText').focus();
  await page.locator('#manualText').evaluate(e=>e.setSelectionRange(3,3));
  await page.waitForTimeout(2200);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'manualText');
  assert.equal(await page.locator('#manualText').evaluate(e=>e.selectionStart),3);
  await page.locator('#resetLayout').click();
  assert.equal(await page.locator('#dashboard > [data-layout-id]').first().getAttribute('id'),'notesPanel');
  assert.equal(await page.locator('#notesPanel').evaluate(e=>e.style.gridColumn),'span 12');
  assert.equal(await page.locator('#manualText').inputValue(),'Observação atualizada');
  const target=await page.locator('#notesPanel').boundingBox();
  await page.locator('#controlPanel > .layout-tools .layout-grip').dragTo(page.locator('#notesPanel'),{targetPosition:{x:90,y:40}});
  assert.equal(await page.locator('#dashboard > [data-layout-id]').first().getAttribute('id'),'controlPanel');
  await page.locator('#resetLayout').click();
  const corner=page.locator('#notesPanel > .layout-resize');await corner.scrollIntoViewIfNeeded();
  const beforeSize=await page.locator('#notesPanel').boundingBox(),r=await corner.boundingBox();
  await page.mouse.move(r.x+r.width/2,r.y+r.height/2);await page.mouse.down();
  await page.mouse.move(r.x+r.width/2-240,r.y+r.height/2+70,{steps:8});await page.mouse.up();
  assert.ok(parseInt((await page.locator('#notesPanel').evaluate(e=>e.style.gridColumn)).replace('span ',''))<12);
  assert.ok((await page.locator('#notesPanel').boundingBox()).height>beforeSize.height);
  await page.locator('#resetLayout').click();
  await page.locator('#notesPanel').scrollIntoViewIfNeeded();
  await page.screenshot({path:'/tmp/meeting-workspace-dashboard.png'});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),true);
  assert.equal(await page.locator('#notesPanel > .layout-resize').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({notes:true,checklist:true,draftReload:true,sessionSave:true,notInModelConfig:true,keyboardReorder:true,resize:true,dynamicCardsAndPolling:true,layoutReload:true,resetPreservesNotes:true,mobile:true,api:'synthetic intercepted, no audio or models'}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
