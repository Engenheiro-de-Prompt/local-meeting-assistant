/* Real local HTTP + persistence contract. Only run against the temporary fixture started by test_browser_roundtrip.py. */
const {chromium}=require(process.env.MEETING_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const url=process.env.MEETING_TEST_URL;assert.ok(url,'Explicit temporary fixture URL required');
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1280,height:900}});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url,{waitUntil:'networkidle'});
  await page.waitForFunction(()=>document.querySelector('#manualScope').textContent.includes('synthetic-persistence'));
  await page.locator('#manualText').fill('Anotação sintética: persistência real, sem IA.');
  await page.locator('#addManualItem').click();
  await page.getByLabel('Texto do item do checklist',{exact:true}).fill('Conferir arquivo da reunião');
  await page.getByLabel('Concluir item do checklist',{exact:true}).check();
  await page.waitForFunction(()=>document.querySelector('#manualStatus').textContent.includes('salvas junto'));
  const status=await page.evaluate(async()=>await(await fetch('/api/status')).json());
  assert.equal(status.manual_notes.text,'Anotação sintética: persistência real, sem IA.');
  assert.equal(status.manual_notes.items[0].checked,true);
  await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('#manualText').inputValue(),status.manual_notes.text);
  assert.equal(await page.getByLabel('Concluir item do checklist',{exact:true}).isChecked(),true);
  await page.locator('#resetLayout').click();
  assert.equal(await page.locator('#manualText').inputValue(),status.manual_notes.text);
  await page.evaluate(()=>MeetingNotes.beginStart());
  assert.equal(await page.locator('#manualText').isEditable(),false);
  assert.equal(await page.getByLabel('Concluir item do checklist',{exact:true}).isDisabled(),true);
  assert.equal(await page.getByLabel('Texto do item do checklist',{exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole('button',{name:'Remover item do checklist',exact:true}).isDisabled(),true);
  await page.evaluate(()=>MeetingNotes.cancelStart());
  assert.equal(await page.locator('#manualText').isEditable(),true);
  assert.equal(await page.getByLabel('Concluir item do checklist',{exact:true}).isDisabled(),false);
  // Two real tabs share localStorage, but pending edits must survive another tab's save.
  const b=await page.context().newPage();
  b.on('pageerror',e=>errors.push(e.message));
  await b.route('**/api/manual-notes',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Falha de rede simulada apenas nesta aba'})}));
  await b.goto(url,{waitUntil:'networkidle'});
  await b.waitForFunction(()=>document.querySelector('#manualScope').textContent.includes('synthetic-persistence'));
  await b.locator('#manualText').fill('Edição da aba B: precisa sobreviver ao reload.');
  await page.locator('#manualText').fill('Versão A confirmada; B continua local.');
  await page.evaluate(()=>MeetingNotes.flush());
  await b.evaluate(async()=>MeetingNotes.sync(await(await fetch('/api/status')).json()));
  await b.locator('#manualConflict').waitFor({state:'visible'});
  await b.reload({waitUntil:'networkidle'});
  await b.waitForFunction(()=>document.querySelector('#manualText').value==='Edição da aba B: precisa sobreviver ao reload.');
  await b.locator('#manualConflict').waitFor({state:'visible'});
  const final=await page.evaluate(async()=>await(await fetch('/api/status')).json());
  assert.equal(final.manual_notes.text,'Versão A confirmada; B continua local.');
  await b.close();
  assert.deepEqual(errors,[]);
  console.log('Real browser -> local HTTP -> session.json -> reload + isolated conflicting tab recovery: OK');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
