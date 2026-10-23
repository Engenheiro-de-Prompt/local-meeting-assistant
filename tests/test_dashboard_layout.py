"""Offline behavior tests: real layout script in Node VM, DOM boundary fixture only."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element {
 constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.parentElement=null;this.dataset={};this.style={};this.attrs={};this.events={};this.className='';this.textContent='';this.id='';this.rect={width:1200,height:320,left:0,top:0};this.classList={add:(...names)=>{this.className=[...new Set([...this.className.split(' '),...names])].join(' ')},contains:n=>this.className.split(' ').includes(n)};}
 append(...nodes){for(const n of nodes){if(n.parentElement)n.parentElement.children.splice(n.parentElement.children.indexOf(n),1);this.children.push(n);n.parentElement=this;}}
 prepend(n){this.append(n);this.children.splice(this.children.indexOf(n),1);this.children.unshift(n);}
 insertBefore(n,b){if(n===b)return;this.append(n);this.children.pop();this.children.splice(this.children.indexOf(b),0,n);}
 matches(s){return s.startsWith('.')?this.classList.contains(s.slice(1)):s.startsWith('#')?this.id===s.slice(1):s==='[data-layout-id]'?!!this.dataset.layoutId:['h2','h3','legend'].includes(s)&&this.tagName===s.toUpperCase();}
 querySelectorAll(s){return this.children.flatMap(c=>[...(s.split(',').some(x=>c.matches(x.trim()))?[c]:[]),...c.querySelectorAll(s)]);}
 querySelector(s){return this.querySelectorAll(s)[0]||null;}
 closest(s){return this.matches(s)?this:this.parentElement?.closest(s)||null;}
 setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k]??null;}
 addEventListener(t,f){(this.events[t]??=[]).push(f);} removeEventListener(t,f){this.events[t]=(this.events[t]||[]).filter(x=>x!==f);}
 emit(t,extra={}){const e={target:this,currentTarget:this,preventDefault(){this.prevented=true;},stopPropagation(){},...extra};for(const f of [...(this.events[t]||[])])f(e);return e;}
 getBoundingClientRect(){return this.rect;} focus(){document.activeElement=this;} setPointerCapture(){} releasePointerCapture(){}
}
const document=new Element();document.createElement=t=>new Element(t);document.getElementById=id=>document.querySelector('#'+id);
const root=id=>{const n=new Element();n.id=id;document.append(n);return n;};
const dashboard=root('dashboard'),analyses=root('analyses'),finalGrid=root('finalGrid');finalGrid.className='final-grid';const custom=root('finalCustomResults'),status=root('layoutStatus'),resetButton=root('resetLayout');
const card=(host,id)=>{const n=new Element('article');n.dataset.layoutId=id;const h=new Element('h2');h.textContent=id;const input=new Element('textarea');input.value='preserve text';n.append(h,input);host.append(n);return n;};
const control=card(dashboard,'controlPanel'),notes=card(dashboard,'notesPanel'),analysis=card(dashboard,'analysisPanel'),liveA=card(analyses,'live:a'),liveB=card(analyses,'live:b');
const store=new Map();let brokenRead=false,brokenWrite=false,mobile=false;
const window={document,innerWidth:1200,localStorage:{getItem(k){if(brokenRead)throw Error('denied');return store.get(k)??null;},setItem(k,v){if(brokenWrite)throw Error('quota');store.set(k,v);},removeItem(k){if(brokenWrite)throw Error('denied');store.delete(k);}},matchMedia:()=>({matches:mobile})};
const context={window,document,console,Map,Set,WeakMap,WeakSet,JSON,Number,Math,Array,Object,String,getComputedStyle:()=>({columnGap:'12px',gap:'12px'}),localStorage:window.localStorage};
vm.runInNewContext(fs.readFileSync('web/dashboard-layout.js','utf8'),context);
const api=window.MeetingLayout,ids=host=>host.children.filter(n=>n.dataset.layoutId).map(n=>n.dataset.layoutId),grip=n=>n.querySelector('.layout-grip'),resize=n=>n.querySelector('.layout-resize');
"""


class DashboardLayoutTests(unittest.TestCase):
    def run_js(self, code):
        result = subprocess.run(["node", "-e", HARNESS + code], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_keyboard_order_persists_restores_and_preserves_content(self):
        self.run_js(r"""
assert.deepEqual(Object.keys(api).sort(),['init','refresh','reset']);
api.init();api.init();
assert.equal(ids(dashboard)[0],'notesPanel');
const input=notes.children.find(n=>n.tagName==='TEXTAREA');
grip(notes).emit('keydown',{key:'ArrowDown'});
assert.deepEqual(ids(dashboard),['controlPanel','notesPanel','analysisPanel']);
assert.equal(input.value,'preserve text');assert.equal(input.parentElement,notes);
assert.equal(notes.querySelectorAll('.layout-tools').length,1);
const saved=JSON.parse([...store.values()][0]);assert.equal(saved.version,1);
assert(!JSON.stringify(saved).includes('preserve text'));
const replacement=new Element('article');replacement.dataset.layoutId='notesPanel';
dashboard.children.splice(dashboard.children.indexOf(notes),1);notes.parentElement=null;dashboard.append(replacement);
api.refresh();assert.deepEqual(ids(dashboard),['controlPanel','notesPanel','analysisPanel']);
resetButton.emit('click');assert.equal(ids(dashboard)[0],'notesPanel');assert.equal(store.size,0);
""")

    def test_native_drag_requires_grip_and_cannot_cross_groups(self):
        self.run_js(r"""
api.init();assert.equal(grip(liveA).draggable,true);assert.notEqual(liveA.draggable,true);
const dataTransfer={effectAllowed:'',setData(){},getData(){return ''}};
grip(liveA).emit('dragstart',{dataTransfer});
dashboard.emit('drop',{target:control,dataTransfer,clientY:300});
assert.equal(liveA.parentElement,analyses);
analyses.emit('drop',{target:liveB,dataTransfer,clientY:300});
assert.deepEqual(ids(analyses),['live:b','live:a']);
grip(liveA).emit('dragend');
analyses.emit('drop',{target:liveB,dataTransfer,clientY:0});
assert.deepEqual(ids(analyses),['live:b','live:a']);
""")

    def test_resize_keyboard_pointer_bounds_refresh_and_reset(self):
        self.run_js(r"""
api.init();const handle=resize(liveA);assert(handle);assert(handle.getAttribute('aria-label').includes('live:a'));
handle.emit('keydown',{key:'ArrowLeft'});assert.equal(liveA.style.gridColumn,'span 3');
handle.emit('keydown',{key:'ArrowDown'});assert.equal(liveA.style.height,'340px');
const persisted=JSON.parse([...store.values()][0]);assert.equal(persisted.groups.live[0].height,340);
handle.emit('pointerdown',{button:0,pointerId:1,clientX:0,clientY:0});
handle.emit('pointermove',{pointerId:1,clientX:2000,clientY:3000});
handle.emit('pointerup',{pointerId:1});assert.equal(liveA.style.gridColumn,'span 12');assert.equal(liveA.style.height,'1600px');
handle.emit('pointerdown',{button:0,pointerId:2,clientX:0,clientY:0});
handle.emit('pointermove',{pointerId:2,clientX:-3000,clientY:-3000});
handle.emit('pointerup',{pointerId:2});assert.equal(liveA.style.gridColumn,'span 3');assert.equal(liveA.style.height,'160px');
api.refresh();assert.equal(liveA.style.height,'160px');assert.equal(liveA.querySelectorAll('.layout-resize').length,1);
handle.emit('keydown',{key:'Home'});assert.equal(liveA.style.height,'auto');
mobile=true;handle.emit('keydown',{key:'ArrowRight'});assert.equal(liveA.style.gridColumn,'span 3');
mobile=false;api.reset();assert.equal(liveA.style.gridColumn,'span 4');assert.equal(liveA.style.height,'auto');
""")

    def test_saved_layout_validation_and_storage_failures_are_safe(self):
        invalid_values = [
            'null', '{version:2,groups:{}}', '{version:1,groups:null}',
            "{version:1,groups:{dashboard:[{id:'notesPanel',span:99,height:160}]}}",
            "{version:1,groups:{live:[{id:'notesPanel',span:3,height:'auto'}]}}",
            "{version:1,groups:{live:[{id:'live:a',span:3,height:159}]}}",
            "{version:1,groups:{live:[{id:'live:a',span:3,height:'auto'},{id:'live:a',span:4,height:'auto'}]}}",
            "JSON.parse('{\"version\":1,\"groups\":{\"__proto__\":[]}}')",
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                self.run_js("store.set('local-meeting-layout-v1',JSON.stringify(" + value + r"""));
api.init();assert.equal(ids(dashboard)[0],'notesPanel');assert(status.textContent.length>0);
assert.equal(liveA.style.gridColumn,'span 4');
""")
        self.run_js(r"""
store.set('local-meeting-layout-v1',JSON.stringify({version:1,groups:{live:[{id:'live:b',span:7,height:480},{id:'live:a',span:5,height:'auto'}]}}));
api.init();assert.deepEqual(ids(analyses),['live:b','live:a']);assert.equal(liveB.style.height,'480px');
brokenWrite=true;grip(liveB).emit('keydown',{key:'ArrowDown'});assert(status.textContent.includes('temporárias'));
api.reset();assert(status.textContent.includes('não foi possível'));
""")
        self.run_js(r"""
brokenRead=true;api.init();assert(status.textContent.includes('ler'));
""")


    def test_polling_during_resize_keeps_preview_and_cancel_restores_saved_size(self):
        self.run_js(r"""
api.init();const h=resize(liveA);
h.emit('pointerdown',{button:0,pointerId:9,clientX:0,clientY:0});
h.emit('pointermove',{pointerId:9,clientX:220,clientY:80});
assert.equal(liveA.style.height,'400px');api.refresh();assert.equal(liveA.style.height,'400px');
h.emit('pointercancel',{pointerId:9});assert.equal(liveA.style.height,'auto');assert.equal(store.size,0);
h.emit('pointerdown',{button:0,pointerId:10,clientX:0,clientY:0});
h.emit('pointermove',{pointerId:11,clientX:200,clientY:200});assert.equal(liveA.style.height,'auto');
h.emit('lostpointercapture',{pointerId:10});assert.equal(liveA.style.height,'auto');
assert.equal(store.size,0);
""")

    def test_temporarily_absent_card_keeps_saved_position_when_another_is_resized(self):
        self.run_js(r"""
api.init();grip(liveB).emit('keydown',{key:'ArrowUp'});
analyses.children.splice(analyses.children.indexOf(liveB),1);liveB.parentElement=null;
api.refresh();resize(liveA).emit('keydown',{key:'ArrowRight'});
analyses.append(liveB);api.refresh();assert.deepEqual(ids(analyses),['live:b','live:a']);
""")

    def test_history_is_bounded_without_discarding_present_cards(self):
        self.run_js(r"""
api.init();for(let i=0;i<260;i++){
 const n=card(custom,'final:temporary_'+i);api.refresh();
 resize(n).emit('keydown',{key:'ArrowRight'});
 custom.children.splice(custom.children.indexOf(n),1);n.parentElement=null;
}
const n=card(custom,'final:current');api.refresh();resize(n).emit('keydown',{key:'ArrowRight'});
const saved=JSON.parse(store.get('local-meeting-layout-v1'));
assert(saved.groups.custom.length<=256);assert(saved.groups.custom.some(x=>x.id==='final:current'));
""")

    def test_css_stacks_mobile_without_saved_height_and_contains_overflow(self):
        css = (ROOT / 'web' / 'dashboard-layout.css').read_text()
        self.assertIn('repeat(12, minmax(0, 1fr))', css)
        self.assertIn('min-width: 0', css)
        self.assertIn('overflow: auto', css)
        mobile = css.split('@media (max-width: 760px)', 1)[1]
        self.assertIn('grid-template-columns: minmax(0, 1fr) !important', mobile)
        self.assertIn('height: auto !important', mobile)
        self.assertIn('grid-column: 1 / -1 !important', mobile)
        self.assertIn('.layout-resize', mobile)


if __name__ == '__main__':
    unittest.main()
