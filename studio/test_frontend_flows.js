'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(...values) { values.forEach((value) => this.values.add(value)); }
  remove(...values) { values.forEach((value) => this.values.delete(value)); }
  toggle(value, force) { if (force === undefined ? !this.values.has(value) : force) this.values.add(value); else this.values.delete(value); }
  contains(value) { return this.values.has(value); }
}
class FakeStyle {
  constructor() { this.values = {}; }
  setProperty(name, value) { this.values[name] = String(value); }
  getPropertyValue(name) { return this.values[name] || ''; }
}
class FakeElement {
  constructor(id = '') {
    this.id = id; this.value = ''; this.checked = false; this.hidden = false; this.disabled = false; this.files = [];
    this.dataset = {}; this.listeners = {}; this.children = []; this.classList = new FakeClassList(); this.style = new FakeStyle(); this.attributes = {};
  }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  async fire(type, event = { target: this }) { for (const callback of this.listeners[type] || []) await callback(event); }
  append(...children) { for(const child of children){if(child.parentNode)child.parentNode.children=child.parentNode.children.filter(item=>item!==child);child.parentNode=this;this.children.push(child);} }
  replaceChildren(...children) { for(const child of this.children)child.parentNode=null;this.children=[];this.append(...children); }
  remove() { this.removed = true; }
  click() { return this.fire('click'); }
  focus() { this.focused = true; }
  setPointerCapture(pointerId) { this.pointerCapture = pointerId; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] || ''; }
  querySelectorAll(selector) { return this.children.flatMap(child=>[...(selector==='input' && child.tagName==='input' ? [child] : []),...child.querySelectorAll(selector)]); }
  closest() { return null; }
}
function jsonResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => 'application/json' }, json: async () => body, blob: async () => Buffer.from('zip') };
}
function setup(hash = '', absentIds = []) {
  const absent = new Set(absentIds);
  const ids = [
    'claim-original','claim-source','claim-map-editor','claim-source-url','claim-source-excerpt','rights-note',
    'brief-problem','brief-benefit','brief-depth','brief-content-type','brief-provenance','brief-contribution','brief-angle','draft-search','project-list',
    'api-dot', 'api-status', 'source-list', 'source-search', 'source-filters', 'import-scope', 'no-draft', 'draft-form', 'revision-badge', 'editor-message', 'loading-overlay', 'loading-text',
    'draft-mode', 'draft-objective', 'draft-audience', 'plan-date', 'draft-title', 'main-copy', 'reply-copy', 'feedback', 'alt-text', 'voice', 'cloud-consent', 'scene-list',
    'main-count', 'reply-count', 'preview-main', 'preview-reply', 'preview-scope', 'preview-primary-media', 'preview-media-note', 'workflow-status', 'final-reviewed', 'final-save-package', 'final-approve-copy', 'final-preview-status', 'asset-list', 'media-status', 'evidence-drawer', 'evidence-content', 'evidence-toggle', 'generate-draft', 'rewrite-draft',
    'job-status', 'reload-server-draft', 'asset-upload', 'asset-rights', 'asset-alt', 'upload-asset', 'upload-progress', 'upload-note', 'vps-asr-allowed', 'refresh-state', 'manual-import',
    'json-import', 'generate-instruction', 'create-draft', 'save-draft', 'review-draft', 'approve-copy', 'add-scene', 'export-zip', 'evidence-close', 'import-note', 'manual-url', 'manual-title', 'manual-text', 'manual-author', 'native-primary-wrap', 'native-primary',
    'app-shell', 'source-pane', 'editor-pane', 'output-pane', 'source-editor-splitter', 'editor-output-splitter', 'new-source', 'toggle-source-pane', 'toggle-output-pane', 'reset-workspace-layout', 'source-box', 'source-import-panel'
  ];
  const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
  elements['draft-mode'].value = 'research'; elements.voice.value = 'tr-TR-AhmetNeural'; elements['asset-rights'].value = 'owned';
  const layoutButtons = ['research','write','review'].map((layout) => { const button = new FakeElement(`layout-${layout}`); button.dataset.layout = layout; return button; });
  const document = { getElementById: (id) => absent.has(id) ? null : (elements[id] ||= new FakeElement(id)), createElement: (tag) => Object.assign(new FakeElement(),{tagName:tag}), querySelector:(selector) => selector === '.workbench' ? elements['app-shell'] : null, querySelectorAll: (selector) => selector === '[data-layout]' ? layoutButtons : [], addEventListener: () => {}, body: new FakeElement('body') };
  elements['source-box'].open = true; elements['source-import-panel'].open = false;
  const calls = []; let revision = 1;
  const serverDraft = () => ({ id: 'draft-1', source_id: 'source-1', revision, mode: 'research', objective: 'Amaç', audience: 'Okur', main: 'Sunucudaki metin', reply: '', title: '', feedback: '', alt_text: '', scene_texts: [], cloud_tts_allowed: false, voice: 'tr-TR-AhmetNeural', assets: [] });
  const fetch = async (path, options = {}) => {
    calls.push({ path, options });
    if (path === '/api/state') return jsonResponse({ bookmarks: [], drafts: [serverDraft()], jobs: [] });
    if (options.method === 'PUT') { revision += 1; return jsonResponse({ ...serverDraft(), ...JSON.parse(options.body), revision }); }
    if (String(path).endsWith('/generate')) return jsonResponse({ job_id: 'generate-job' }, 202);
    if (String(path).endsWith('/transcribe')) return jsonResponse({ job_id: 'asr-job' }, 202);
    if (String(path).endsWith('/primary-media')) { const request = JSON.parse(options.body); return jsonResponse({ ...serverDraft(), primary_media: request.filename ? { name: request.filename, revision, sha256: 'test-hash' } : null }); }
    if (String(path).endsWith('/upload')) return jsonResponse({ draft: serverDraft() }, 201);
    if (String(path).endsWith('/save-package')) { const request = JSON.parse(options.body); return jsonResponse({ path: 'D:/exports/test.zip', sha256: 'a'.repeat(64), size: 1, verified: true, publication_ready: false, purpose: request.fingerprint ? 'final_local' : 'interim_review' }); }
    return jsonResponse({});
  };
  const storage = new Map();
  const localStorage = { getItem: (key) => storage.has(key) ? storage.get(key) : null, setItem: (key, value) => storage.set(key, String(value)), removeItem: (key) => storage.delete(key) };
  const window = { location:{hash}, __STUDIO_TEST__: true, confirm:()=>true, innerWidth:1440, matchMedia:() => ({matches:false,addEventListener:()=>{}}), addEventListener:()=>{}, setInterval: () => 42, clearInterval: () => {}, XMLHttpRequest: undefined, localStorage };
  const context = { window, document, fetch, console, URL: { createObjectURL: () => 'blob:test', revokeObjectURL: () => {} }, FileReader: class { readAsDataURL(file) { this.result = `data:${file.type};base64,${file.data}`; this.onload(); } }, setTimeout, clearTimeout };
  const appPath = require('node:path').join(__dirname, 'static', 'app.js');
  vm.runInNewContext(fs.readFileSync(appPath, 'utf8'), context, { filename: appPath });
  return { elements, calls, hooks: window.__STUDIO_TEST__, layoutButtons, storage, window };
}
async function flush() { await Promise.resolve(); await Promise.resolve(); }

(async () => {
  // An old cached HTML document can load a new app.js; it must not crash or enable final save.
  const cachedHtml=setup('', ['draft-identity','final-reviewed-row']);
  cachedHtml.hooks.state.data={bookmarks:[],drafts:[],jobs:[],proposals:[],capabilities:{final_package_gate:true}};
  assert.doesNotThrow(()=>cachedHtml.hooks.selectDraft({id:'cached-page',source_id:'source-1',revision:1,main:'Metin',reply:'',assets:[],copy_approved:true,approval_fingerprint:'f'.repeat(64),approvals:{copy:{fingerprint:'f'.repeat(64)}}}));
  assert.equal(cachedHtml.elements['final-save-package'].disabled,true,'eski HTML ile son kayıt kapalı kalmalı');
  assert.match(cachedHtml.elements['final-preview-status'].textContent,/yenile/i);
  const wrongLink=setup('#draft=missing'); await new Promise(setImmediate);
  assert.match(wrongLink.elements['editor-message'].textContent,/bulunamadı/,'yanlış bağlantı: '+wrongLink.elements['editor-message'].textContent);
  assert.equal(wrongLink.hooks.state.draft,null);
  const bareLanding=setup(); await new Promise(setImmediate);
  assert.equal(bareLanding.hooks.state.draft,null,'bare / never infers and opens the first draft');
  assert.equal(bareLanding.elements['app-shell'].hidden,true,'bare / leaves the works landing in control');
  const exactLink=setup('#draft=draft-1'); await new Promise(setImmediate);
  assert.equal(exactLink.hooks.state.draft.id,'draft-1','#draft opens only the requested work');
  assert.equal(exactLink.elements['app-shell'].hidden,false,'an explicit deep link enters its work focus');
  const disabledWorker=setup(); await new Promise(setImmediate);
  disabledWorker.hooks.state.data={capabilities:{job_execution:false}};
  await disabledWorker.elements['generate-draft'].click();
  assert.match(disabledWorker.elements['editor-message'].textContent,/işçi.*kapalı/);
  assert.equal(disabledWorker.calls.some(c=>c.path.endsWith('/generate')),false);
  const snapshotUI=setup();await new Promise(setImmediate);
  snapshotUI.hooks.state.data={bookmarks:[{id:'source-1',url:'https://example.com',text:'İlk kısa kayıt',source_evidence:{full_text_status:'full',full_text:'SONRADAN GELEN KANIT'}}],drafts:[],jobs:[]};
  snapshotUI.hooks.selectDraft({id:'draft-1',source_id:'source-1',revision:1,main:'Eski taslak',source_evidence:null,assets:[]});
  assert.equal(snapshotUI.elements['evidence-content'].children.some(row=>row.children[1]?.textContent==='SONRADAN GELEN KANIT'),false,'taslak çekmecesi canlı kaynağı sessiz devralmaz');
  assert.equal(snapshotUI.elements['evidence-content'].children.some(row=>row.children[1]?.textContent==='İlk kısa kayıt'),false,'eksik eski gövde canlı kaynaktan doldurulmaz');
  assert.equal(snapshotUI.elements['claim-source-url'].children.length,0,'eksik taslak kopyasına canlı kaynak izin listesi eklenmez');
  const detailApp=setup();await new Promise(setImmediate);
  detailApp.hooks.selectDraft({id:'detail',revision:1,main:'Kısa gönderi',scene_texts:['Girdi: örnek dosya','Koşul: geçersiz girdi reddedilir'],assets:[]});
  assert.equal(detailApp.elements['preview-details'].children.length,2,'ayrıntı sahneleri okunabilir önizlemede ayrı görünür');
  assert.equal(detailApp.elements['preview-main'].textContent,'Kısa gönderi','ayrıntı X ana metnine sessiz eklenmez');
  const detailInput=detailApp.elements['scene-list'].querySelectorAll('input')[0];
  detailInput.value='Kaydedilmemiş yeni protokol';await detailInput.fire('input');
  assert.equal(detailApp.elements['preview-details'].children[0].textContent,'Kaydedilmemiş yeni protokol','not önizlemesi düzenlemeyi gecikmeden izler');
  await detailApp.elements['scene-list'].children[0].children[2].click();
  assert.equal(detailApp.elements['preview-details'].children[0].textContent,'Koşul: geçersiz girdi reddedilir','çıkarılan not önizlemede kalmaz');
  const blankApp=setup();await new Promise(setImmediate);
  blankApp.hooks.selectDraft({id:'blank-detail',revision:1,main:'Kısa',scene_texts:['','İkinci silinecek','Üçüncü kalacak'],assets:[]});
  await blankApp.elements['scene-list'].children[1].children[2].click();
  assert.deepEqual(blankApp.elements['scene-list'].querySelectorAll('input').map(input=>input.value),['','Üçüncü kalacak'],'boş satır varken yanlış sahne silinmez');
  const newSourceApp=setup();await new Promise(setImmediate);
  newSourceApp.hooks.state.data={bookmarks:[{id:'new-source',url:'https://example.com',text:'Kaynak',source:'manual_x'}],drafts:[],jobs:[]};
  newSourceApp.hooks.selectSource(newSourceApp.hooks.state.data.bookmarks[0]);
  assert.equal(newSourceApp.elements['brief-depth'].value,'beginner','yeni projede arayüz aktif standardın başlangıç düzeyini göstermeli');
  assert.match(newSourceApp.elements['source-list'].children[0].children[0].children.map(x=>x.textContent).join(' '),/Metin taşındı.*iddialar doğrulanmadı/i,'metin kaydedilmesi iddiaları doğrulanmış gibi göstermeden belirtilmeli');
  const deleteApp=setup();await new Promise(setImmediate);
  const sourceToDelete={id:'unused-x',url:'https://x.com/example/status/2103036464527298763',title:'Benim kaynağım',text:'Gönderi metni',source:'manual_x'};
  deleteApp.hooks.state.data={bookmarks:[sourceToDelete,{...sourceToDelete,id:'linked-x',title:'Bağlı kaynak',url:'https://x.com/example/status/2103036464527298764'}],drafts:[{id:'linked-draft',source_id:'linked-x',revision:1,main:''}],jobs:[],capabilities:{read_only:false}};
  deleteApp.hooks.selectSource(sourceToDelete);
  const unusedEntry=deleteApp.elements['source-list'].children[0];
  const deleteButton=unusedEntry.children.find(child=>child.textContent==='Kaynağı sil');
  assert.ok(deleteButton,'elle eklenen ve boşta duran kaynağın görünür sil düğmesi olmalı');
  const linkedEntry=deleteApp.elements['source-list'].children[1];
  assert.equal(linkedEntry.children.find(child=>child.textContent==='Kaynağı sil')?.disabled,true,'çalışmaya bağlı kaynağın sil düğmesi kapalı kalmalı');
  deleteApp.window.confirm=()=>false;
  await deleteButton.click();
  assert.equal(deleteApp.calls.some(call=>call.options.method==='DELETE'),false,'insan iptali hiçbir yazma yapmamalı');
  deleteApp.window.confirm=()=>true;
  await deleteButton.click();
  const deletion=deleteApp.calls.find(call=>call.options.method==='DELETE');
  assert.equal(deletion.path,'/api/sources/unused-x');
  assert.equal(JSON.parse(deletion.options.body).expected_url,sourceToDelete.url);
  assert.equal(deleteApp.hooks.state.selectedSource,null,'silinen kaynak seçimde kalmamalı');
  const proposalApp=setup(); await new Promise(setImmediate);
  proposalApp.hooks.state.draft={id:'draft-1',source_id:'source-1',revision:1,main:'Önceki metin',assets:[]};
  proposalApp.hooks.state.data={proposals:[{id:'p1',draft_id:'draft-1',revision:1,status:'pending',data:{main:'Yeni öneri',reply:'',title:'Öneri',scene_texts:[]}}]};
  proposalApp.elements['main-copy'].value='Kaydedilmemiş kendi metnim';
  assert.equal(typeof proposalApp.hooks.renderProposals,'function','öneri karşılaştırması görünür olmalı');
  proposalApp.hooks.renderProposals();
  assert.equal(proposalApp.elements['proposal-panel'].hidden,false);
  assert.match(proposalApp.elements['proposal-copy'].textContent,/Yeni öneri/);
  await proposalApp.hooks.resolveProposal('apply');
  assert.equal(proposalApp.elements['main-copy'].value,'Kaydedilmemiş kendi metnim');
  assert.equal(proposalApp.calls.some(c=>c.path.includes('/proposals/') && c.path.endsWith('/apply')),false,'kirli editör öneriyle ezilmez');

  const visualApp=setup(); await new Promise(setImmediate);
  visualApp.hooks.state.data={bookmarks:[{id:'source-1',url:'https://example.com',text:'Kaynak'}],drafts:[],jobs:[],proposals:[]};
  visualApp.hooks.selectDraft({id:'draft-1',source_id:'source-1',revision:3,main:'Kayıtlı metin',reply:'Kayıtlı yanıt',title:'Eski',assets:[]});
  visualApp.hooks.state.data.proposals=[{id:'candidate-1',draft_id:'draft-1',revision:3,status:'pending',data:{title:'Yeni başlık',main:'Önerilen ana gönderi',reply:'Önerilen ilk yanıt',scene_texts:[]}}];
  visualApp.hooks.renderProposals();
  assert.equal(visualApp.elements['preview-main'].textContent,'Önerilen ana gönderi','bekleyen öneri sağ önizlemede görünür');
  assert.equal(visualApp.elements['preview-reply'].textContent,'Önerilen ilk yanıt','ilk yanıt önerisi ayrı gönderi olarak görünür');
  assert.match(visualApp.elements['preview-scope'].textContent,/öneri.*uygulanmadı/i,'önizleme kabul edilmiş taslak gibi sunulmaz');
  assert.match(visualApp.elements['preview-media-note'].textContent,/medya yok/i,'medya uydurulmaz');
  assert.equal(visualApp.elements['main-copy'].value,'Kayıtlı metin','öneri önizlemesi editörü değiştirmez');
  visualApp.hooks.state.data.proposals[0].status='discarded';visualApp.hooks.renderProposals();
  assert.equal(visualApp.elements['preview-main'].textContent,'Kayıtlı metin','bekleyen öneri kalmayınca önizleme geri döner');
  const journey=setup();await new Promise(setImmediate);
  journey.hooks.state.data={bookmarks:[{id:'source-1',title:'AYi kaynağı',url:'https://example.com',text:'Taşınan metin'}],drafts:[],jobs:[],proposals:[]};
  journey.hooks.selectSource(journey.hooks.state.data.bookmarks[0]);
  assert.equal(journey.elements['app-shell'].dataset.stage,'idea','kaynak seçildikten sonra brief/öneri görevi açılır');
  assert.equal(journey.elements['stage-title'].textContent,'İçerik hedefi','yeni projede ilk görev açıktır');
  journey.hooks.state.data.proposals=[{id:'p2',draft_id:'draft-1',revision:1,status:'pending',data:{title:'Paket adı',main:'Bir öneri',reply:''}}];
  journey.hooks.selectDraft({id:'draft-1',source_id:'source-1',revision:1,main:'',assets:[]});
  assert.equal(journey.elements['app-shell'].dataset.stage,'idea','bekleyen öneri editörden ayrı bir görevdir');
  assert.equal(journey.elements['stage-title'].textContent,'Öneriyi değerlendir','önerinin sahibi açık olmalı');
  journey.hooks.setStage('edit');
  assert.equal(journey.elements['app-shell'].dataset.stage,'idea','bekleyen öneri çözülmeden boş editöre geçilmez');
  assert.match(journey.elements['editor-message'].textContent,/öneri/i);
  journey.hooks.state.data.proposals[0].status='discarded';
  journey.hooks.setStage('edit');
  assert.equal(journey.elements['app-shell'].dataset.stage,'edit','düzenleme tek başına seçilebilir');
  journey.hooks.setStage('review');
  assert.equal(journey.elements['app-shell'].dataset.stage,'review','iddia ve metin kontrolü ayrı görevdir');
  journey.hooks.setStage('preview');
  assert.equal(journey.elements['app-shell'].dataset.stage,'preview','son yerel görünüm ayrı görevdir');
  const manualGate=setup();await new Promise(setImmediate);
  manualGate.elements['manual-url'].value='http://example.com';
  assert.throws(()=>manualGate.hooks.manualItem(),/https:\/\//);
  assert.equal(manualGate.elements['manual-url'].getAttribute('aria-invalid'),'true','yanlış URL alanı işaretlenir');
  manualGate.elements['manual-url'].value='https://example.com/story';
  manualGate.elements['manual-title'].value='Kaynak';
  assert.throws(()=>manualGate.hooks.manualItem(),/metin|transkript/i);
  assert.equal(manualGate.elements['manual-text'].focused,true,'metinsiz kaynakta odağı ilgili alan alır');

  const blockedWrites=setup();await new Promise(setImmediate);blockedWrites.hooks.state.data={bookmarks:[],drafts:[],jobs:[],read_only:true};
  const callsBeforeBlock=blockedWrites.calls.length;
  await assert.rejects(blockedWrites.hooks.api('/api/drafts',{method:'POST',body:'{}'}),/salt-okunur|bağlantı/i);
  assert.equal(blockedWrites.calls.length,callsBeforeBlock,'salt-okunur istemci sunucuya mutasyon yollamaz');

  const resumed=setup();await new Promise(setImmediate);
  resumed.hooks.state.data={bookmarks:[{id:'source-1',title:'Kaynak'}],drafts:[],jobs:[{id:'j1',draft_id:'draft-1',kind:'generate',status:'pending',created_at:new Date(Date.now()-5000).toISOString()}],proposals:[]};
  const resumeCalls=resumed.calls.length;
  resumed.hooks.selectDraft({id:'draft-1',source_id:'source-1',revision:1,main:'',assets:[]});
  assert.equal(resumed.hooks.state.activeJob?.id,'j1','yeniden açılan çalışma mevcut işi salt-okunur izler');
  assert.match(resumed.elements['job-status'].textContent,/sırada/i);
  assert.equal(resumed.calls.length,resumeCalls,'işe geri dönmek yeni model çağrısı yapmaz');

  const proposalHTML=fs.readFileSync(require('node:path').join(__dirname,'static','index.html'),'utf8');
  const proposalCSS=fs.readFileSync(require('node:path').join(__dirname,'static','workspace.css'),'utf8');
  assert.ok(proposalHTML.indexOf('id="proposal-panel"')<proposalHTML.indexOf('class="copy-editor"'),'öneri metni uzun editörün en altında kaybolmaz');
  assert.match(proposalCSS,/#proposal-copy[^\{]*\{[^}]*white-space:\s*pre-wrap/,'öneri satırları ekrandan taşmadan kırılır');
  assert.match(proposalCSS,/@media \(min-width:1101px\)[\s\S]*?\.output-rail\s*\{[^}]*position:\s*sticky[^}]*overflow-y:\s*auto/,'uzun editörde masaüstü önizleme rayı görünür kalır ve kendi içinde kayar');
  assert.doesNotMatch(proposalCSS,/\.preview-panel\s*\{[^}]*position:\s*sticky/,'yapışık saydam önizleme paneli alttaki paket/yükleme düğmelerini örtmez');
  assert.match(proposalCSS,/@media \(max-width:1100px\)[^{]*\{[^}]*\.layout-presets[^}]*display:\s*none/,'dar ekranda etkisiz düzen düğmeleri gizlenir');
  assert.match(proposalHTML,/id="package-panel"[\s\S]*?Ara inceleme paketi[\s\S]*?id="open-final-preview"/,'ara paket bölümü kendini ara paket diye adlandırır ve son kayda yönlendirir');
  assert.doesNotMatch(proposalHTML,/X ana yüzeyi: en fazla bir fotoğraf veya video/,'X kuralı yanlış sunulmaz');
  assert.match(proposalHTML,/KarıncaAI kuralı[\s\S]{0,120}X 4 fotoğrafa kadar/,'tek görsel kuralı KarıncaAI kuralı olarak etiketlenir');
  assert.match(proposalHTML,/src="\/static\/app\.js\?v=/,'yeni arayüz eski önbellekle karışmaz');
  assert.doesNotMatch(proposalHTML,/\/static\/app\.js\?v=20260924-gate2/,'tam ürün UX değişimi eski JS önbellek sürümünü kullanmamalı');
  assert.doesNotMatch(proposalHTML,/\/static\/workspace\.css\?v=20260924(?:["&])/,'tam ürün UX değişimi eski CSS önbellek sürümünü kullanmamalı');
  assert.match(proposalHTML,/id="draft-identity"/,'editörde proje/kaynak kimliği alanı var');
  assert.match(proposalHTML,/id="workflow-status"/,'çalışmanın hangi durakta olduğu editör yanında görünür');
  assert.match(proposalHTML,/id="final-reviewed"[\s\S]*?id="final-save-package"/,'son yerel önizleme ve paket kaydı aynı yerde');
  assert.match(proposalHTML,/id="final-approve-copy"/,'metin kabulü büyük önizlemeden de yapılır');
  assert.match(proposalHTML,/class="review-dialog-scroll"[\s\S]*id="review-dialog-content"/,'büyük önizlemenin içeriği kendi alanında kaydırılır');
  assert.match(proposalCSS,/#review-dialog\[open\][^{]*\{[^}]*display:\s*flex/,'onay alanı büyük önizlemede görünür kalır');
  const gateApp=setup();await new Promise(setImmediate);
  const fp='f'.repeat(64);
  gateApp.hooks.state.data={bookmarks:[],drafts:[],jobs:[],proposals:[],capabilities:{final_package_gate:true}};
  gateApp.hooks.selectDraft({id:'approved-draft',source_id:'2090000000000000777',revision:4,main:'Önizlenecek metin',reply:'',assets:[],copy_approved:true,approval_fingerprint:fp,approvals:{copy:{fingerprint:fp,at:'2026-09-24T00:00:00Z',source:'explicit_human_confirmation'}}});
  assert.match(gateApp.elements['draft-identity'].textContent,/approved.*2090000000000000777/,'proje ve kaynak kimliği editörde görünür');
  gateApp.hooks.state.previewFingerprint=fp;
  assert.equal(gateApp.elements['final-approve-copy'].hidden,true,'geçerli onaydan sonra diyalogda yalnız sıradaki eylem görünür');
  assert.equal(gateApp.elements['final-save-package'].hidden,false);
  gateApp.elements['final-reviewed'].checked=true;gateApp.hooks.updateFinalGate();
  assert.equal(gateApp.elements['final-save-package'].disabled,false,'onaylı ve incelenmiş revizyon kaydedilebilir');
  gateApp.elements['main-copy'].value='Kaydedilmemiş değişiklik';gateApp.hooks.updateFinalGate();
  assert.equal(gateApp.elements['final-save-package'].disabled,true,'kirli revizyon son paket diye kaydedilemez');
  gateApp.elements['main-copy'].value='Önizlenecek metin';gateApp.hooks.state.draft.copy_approved=false;gateApp.hooks.updateFinalGate();
  assert.equal(gateApp.elements['final-save-package'].disabled,true,'metin onayı olmayan paket son kayıt olamaz');
  gateApp.hooks.state.draft.copy_approved=true;gateApp.hooks.state.data.proposals=[{id:'p2',draft_id:'approved-draft',status:'pending',revision:4}];gateApp.hooks.updateFinalGate();
  assert.equal(gateApp.elements['final-save-package'].disabled,true,'bekleyen öneri varken yanlış sürüm son kayıt olamaz');
  await gateApp.elements['final-save-package'].click();
  assert.equal(gateApp.calls.some(c=>c.path.endsWith('/save-package')),false,'kapalı son kayıt API isteği yapmaz');
  gateApp.hooks.state.data.proposals=[];gateApp.hooks.updateFinalGate();
  gateApp.hooks.state.previewFingerprint='e'.repeat(64);gateApp.hooks.updateFinalGate();
  assert.equal(gateApp.elements['final-save-package'].disabled,true,'önizleme açıldıktan sonra değişen sürüm son paket olamaz');
  assert.match(gateApp.elements['final-preview-status'].textContent,/önizlemeyi yeniden aç/i);
  gateApp.hooks.state.previewFingerprint=fp;gateApp.hooks.state.data.capabilities={};gateApp.hooks.updateFinalGate();
  assert.equal(gateApp.elements['final-save-package'].disabled,true,'sunucu son kayıt kapısını bildirmiyorsa son düğme kapalı');
  assert.match(gateApp.elements['final-preview-status'].textContent,/yeniden (başlat|aç)/i,'eski sunucu açıkça söylenir');
  gateApp.hooks.state.data.capabilities={final_package_gate:true};gateApp.hooks.updateFinalGate();
  const beforeFinal=gateApp.calls.length;
  await gateApp.elements['final-save-package'].click();await flush();
  const finalCalls=gateApp.calls.slice(beforeFinal);
  assert.equal(finalCalls.filter(c=>c.path.endsWith('/save-package')).length,1,'son önizleme onayı gerçek paket ucunu bir kez çağırır');
  assert.deepEqual(JSON.parse(finalCalls.find(c=>c.path.endsWith('/save-package')).options.body),{revision:4,fingerprint:fp,confirmed:true},'son paket önizlenen parmak izini ve açık beyanı yollar');
  assert.equal(finalCalls.some(c=>c.options.method==='PUT'),false,'son paket yolu taslağı otomatik kaydetmez (onay silinmez)');
  assert.match(gateApp.elements['editor-message'].textContent,/^Metin onaylı yerel paket kaydedildi ve doğrulandı: /);
  const beforeInterim=gateApp.calls.length;
  await gateApp.elements['save-package'].click();await flush();
  const interimCall=gateApp.calls.slice(beforeInterim).find(c=>c.path.endsWith('/save-package'));
  assert.deepEqual(JSON.parse(interimCall.options.body),{revision:4},'ara paket geriye uyumlu {revision} gövdesini korur');
  assert.equal(gateApp.elements['editor-message'].textContent,'Ara inceleme paketi kaydedildi ve doğrulandı: D:/exports/test.zip','ara paket amacını açıkça adlandırır');
  gateApp.hooks.selectDraft({id:'stale-approval',revision:2,main:'Bayat onaylı metin',reply:'',assets:[],reader_brief:{selected_angle:'Seçili açı'},claim_map:{claims:[],evidence:[]},copy_approved:true,approval_fingerprint:fp,approvals:{copy:{fingerprint:'d'.repeat(64)}}});
  assert.equal(gateApp.elements['approve-copy'].disabled,false,'bayat onay editörde tek kanonik onay yolunu açar');
  assert.equal(gateApp.elements['final-approve-copy'].hidden,true,'önizleme ikinci insan onayı sunmaz');
  assert.equal(gateApp.elements['final-save-package'].hidden,true,'onaysızken kayıt adımı gizli');
  assert.equal(gateApp.elements['final-reviewed-row'].hidden,true);
  gateApp.hooks.selectDraft({id:'next-draft',revision:1,main:'Başka içerik',assets:[],copy_approved:true});
  assert.equal(gateApp.elements['final-reviewed'].checked,false,'son bakış onayı taslaklar arasında taşınmaz');
  const approveGate=setup();await new Promise(setImmediate);
  approveGate.hooks.state.data={bookmarks:[],drafts:[],jobs:[],proposals:[],capabilities:{final_package_gate:true}};
  approveGate.hooks.selectDraft({id:'empty-draft',revision:3,main:'',reply:'',assets:[]});
  assert.equal(approveGate.elements['approve-copy'].disabled,true,'metin yokken inceleme panelindeki onay da kapalı');
  assert.equal(approveGate.elements['save-package'].disabled,true,'ana metin yokken ara paket düğmesi kapalı');
  approveGate.hooks.selectDraft({id:'text-draft',revision:3,main:'Onaylanacak metin',reply:'',assets:[]});
  assert.equal(approveGate.elements['approve-copy'].disabled,true,'kaynak/iddia eşlemesi yoksa onay kapalı');
  assert.match(approveGate.elements['approval-blockers'].textContent,/açı|iddia/i,'eksik onay önkoşulu görünür');
  approveGate.hooks.selectDraft({id:'text-draft',revision:3,main:'Onaylanacak metin',reply:'',assets:[],reader_brief:{selected_angle:'İnsan seçimi'},claim_map:{claims:[],evidence:[]}});
  assert.equal(approveGate.elements['approve-copy'].disabled,false,'temel önkoşullar hazırsa sunucu son kapıyı değerlendirir');
  approveGate.hooks.state.data.proposals=[{id:'px',draft_id:'text-draft',status:'pending',revision:3}];approveGate.hooks.updateFinalGate();
  assert.equal(approveGate.elements['approve-copy'].disabled,true,'bekleyen öneri varken iki onay düğmesi de aynı kapıda');
  approveGate.hooks.state.data.proposals=[];approveGate.elements['main-copy'].value='Kaydedilmemiş';approveGate.hooks.updateFinalGate();
  assert.equal(approveGate.elements['approve-copy'].disabled,true,'kaydedilmemiş metin önizlenmeden onaylanmaz');

  const claimApp=setup(); await new Promise(setImmediate);
  claimApp.hooks.state.draft={id:'draft-1',revision:1,source_id:'source-1',assets:[],claim_map:null};
  claimApp.elements['main-copy'].value='Bizim özgün yöntemimiz.';claimApp.elements['draft-title'].value='Özgün başlık';
  claimApp.elements['reply-copy'].value='Sınırı ayrıca açıklıyoruz.';
  claimApp.elements['rights-note'].value='Kaynak medya kullanılmadı.';
  await claimApp.elements['claim-original'].click();
  const mapped=claimApp.hooks.editorPayload();
  assert.equal(mapped.claim_map.claims.length,3,'başlık, ana ve yanıt ayrı eşleştirilir');
  assert.equal(mapped.claim_map.claims[0].text,'Özgün başlık');
  assert.equal(mapped.claim_map.evidence[0].kind,'original','özgün öneri dış kaynağa kanıt diye bağlanmaz');
  assert.equal(mapped.rights_note,'Kaynak medya kullanılmadı.');
  claimApp.elements['claim-map-editor'].value='{bozuk';
  assert.equal(claimApp.hooks.editorPayload().claim_map,'{bozuk','geçersiz kanıt JSON sessizce düşürülmez');

  const app = setup(); await new Promise(setImmediate);
  const { elements, calls, hooks } = app;
  assert.ok(hooks.state.data,'test düzenleyicisi önce API durumu yüklemeli');
  hooks.state.selectedSource = { id: 'source-1' };
  hooks.state.draft = { id: 'draft-1', revision: 1, source_id: 'source-1', assets: [] };
  elements['draft-objective'].value = 'Amaç'; elements['draft-audience'].value = 'Okur'; elements['main-copy'].value = 'Yerel değişiklik';
  hooks.state.cleanSignature = '{}';
  await elements['generate-draft'].click();
  const put = calls.find((call) => call.options.method === 'PUT');
  const generate = calls.find((call) => String(call.path).endsWith('/generate'));
  assert.ok(put, 'gerçek üret düğmesi önce taslağı kaydetmelidir');
  assert.equal(JSON.parse(put.options.body).main, 'Yerel değişiklik');
  assert.ok(generate, 'gerçek üret düğmesi generate API çağrısını yapmalıdır');
  assert.equal(elements['generate-draft'].disabled, true, 'iş sürerken üret düğmesi devre dışı kalmalıdır');
  assert.match(elements['job-status'].textContent, /sırada|durumu/i, 'iş bekleme durumu kullanıcıya görünmelidir');
  hooks.renderJobProgress({status:'pending',created_at:new Date(Date.now()-6000).toISOString()});
  assert.match(elements['job-status'].textContent,/sırada/i,'bekleme gerçek sunucu durumudur');
  assert.match(elements['job-elapsed'].textContent,/\d+ sn/i,'iş kaydından geçen süre görünür');
  hooks.renderJobProgress({status:'running',created_at:new Date(Date.now()-6000).toISOString()});
  assert.match(elements['job-status'].textContent,/çalışıyor/i,'sırada ve çalışan iş ayrıdır');

  elements['asset-alt'].value = 'Türkçe ses açıklaması';
  elements['asset-upload'].files = [{ name: 'ses.mp3', type: 'audio/mpeg', size: 16, data: 'c2Vz' }];
  hooks.state.draft = { id: 'draft-1', revision: 2, source_id: 'source-1', assets: [] };
  await elements['upload-asset'].click();
  const upload = calls.find((call) => String(call.path).endsWith('/upload'));
  assert.ok(upload, 'yükle düğmesi gerçek upload API çağrısını yapmalıdır');
  const uploadBody = JSON.parse(upload.options.body);
  assert.deepEqual(Object.keys(uploadBody).sort(), ['alt_text', 'data_base64', 'filename', 'kind', 'revision', 'rights']);
  assert.equal(uploadBody.kind, 'audio'); assert.equal(uploadBody.rights, 'owned'); assert.equal(uploadBody.data_base64, 'c2Vz');

  elements['vps-asr-allowed'].checked = true;
  hooks.state.activeJob = null;
  await hooks.transcribeAsset({ name: 'ses.mp3', kind: 'audio', provenance: 'local_upload' });
  const transcribe = calls.find((call) => String(call.path).endsWith('/transcribe'));
  assert.ok(transcribe, 'yüklenmiş ses için altyazı eylemi ASR API çağrısını yapmalıdır');
  assert.deepEqual(JSON.parse(transcribe.options.body), { revision: 2, filename: 'ses.mp3', vps_allowed: true });

  const one = { name: 'one.mp4', kind: 'video', mime: 'video/mp4' };
  const two = { name: 'two.mp4', kind: 'video', mime: 'video/mp4' };
  const captions = [{ name: 'one.tr.vtt', kind: 'subtitle', mime: 'text/vtt', for_asset: 'one.mp4' }, { name: 'two.tr.vtt', kind: 'subtitle', mime: 'text/vtt', for_asset: 'two.mp4' }];
  assert.equal(hooks.subtitleAsset(one, [one, two, ...captions]).name, 'one.tr.vtt', 'her video yalnız kendi açık bağlı VTT dosyasını seçmelidir');
  assert.equal(hooks.subtitleAsset(two, [one, two, ...captions]).name, 'two.tr.vtt', 'ilk global VTT ikinci videoya bağlanmamalıdır');
  assert.equal(hooks.subtitleAsset({ name: 'uploaded.mp4', kind: 'video', mime: 'video/mp4' }, [{ name: 'random.vtt', kind: 'subtitle', mime: 'text/vtt' }]), null, 'rastgele yüklenen medya için altyazı tahmin edilmemelidir');

  hooks.state.draft = { id: 'draft-1', revision: 2, source_id: 'source-1', assets: [one, two], primary_media: null };
  elements['main-copy'].value = 'Kaydedilmemiş seçim metni'; hooks.state.cleanSignature = '{}';
  elements['native-primary'].value = 'two.mp4';
  await elements['native-primary'].fire('change');
  const primary = calls.find((call) => String(call.path).endsWith('/primary-media'));
  assert.ok(primary, 'birincil medya seçimi kalıcı API çağrısı yapmalıdır');
  assert.deepEqual(JSON.parse(primary.options.body), { revision: 2, filename: 'two.mp4' });
  assert.equal(elements['main-copy'].value, 'Kaydedilmemiş seçim metni', 'birincil medya seçimi kaydedilmemiş editör metnini ezmemelidir');

  hooks.state.draft = { id: 'draft-1', revision: 2, source_id: 'source-1', assets: [] };
  hooks.state.cleanSignature = '{}'; elements['main-copy'].value = 'Kaydedilmemiş yerel metin';
  hooks.applyServerState({ bookmarks: [], jobs: [], drafts: [{ id: 'draft-1', revision: 3, source_id: 'source-1', assets: [] }] }, { preserveEditor: true });
  assert.equal(elements['main-copy'].value, 'Kaydedilmemiş yerel metin', 'poll/senkronizasyon yerel editör tamponunu ezmemelidir');
  assert.equal(hooks.state.remoteDraft.revision, 3, 'çakışan sunucu revizyonu açıkça saklanmalıdır');
  assert.equal(elements['reload-server-draft'].hidden, false, 'sunucu sürümünü bilinçli yükleme yolu görünmelidir');

  assert.equal(typeof hooks.restoreRevision, 'function', 'geri alma gerçek bir frontend handler olarak dışa açılmalıdır');
  const beforeDirtyRestore = calls.filter((call) => String(call.path).endsWith('/restore')).length;
  await hooks.restoreRevision(1);
  assert.equal(calls.filter((call) => String(call.path).endsWith('/restore')).length, beforeDirtyRestore, 'kaydedilmemiş editör tamponu varken geri alma sunucuya gönderilmemelidir');
  assert.match(elements['editor-message'].textContent, /kaydedilmemiş/i, 'geri alma engeli kullanıcıya görünmelidir');

  const cleanApp = setup(); await new Promise(setImmediate);
  cleanApp.hooks.state.draft = { id: 'draft-1', revision: 2, source_id: 'source-1', assets: [] };
  const cleanRestorePayload=cleanApp.hooks.editorPayload(); delete cleanRestorePayload.revision;
  cleanApp.hooks.state.cleanSignature=JSON.stringify(cleanRestorePayload);
  await cleanApp.hooks.restoreRevision(1);
  const restore = cleanApp.calls.find((call) => String(call.path).endsWith('/restore'));
  assert.ok(restore, 'geri dön handlerı restore API çağrısını yapmalıdır');
  assert.deepEqual(JSON.parse(restore.options.body), { revision: 2, restore_revision: 1 });
  const cancelRestore=setup();await new Promise(setImmediate);
  cancelRestore.hooks.state.draft={id:'draft-1',revision:2,source_id:'source-1',assets:[]};
  const cancelPayload=cancelRestore.hooks.editorPayload();delete cancelPayload.revision;
  cancelRestore.hooks.state.cleanSignature=JSON.stringify(cancelPayload);
  cancelRestore.window.confirm=()=>false;
  await cancelRestore.hooks.restoreRevision(1);
  assert.equal(cancelRestore.calls.filter(c=>String(c.path).endsWith('/restore')).length,0);
  const evidenceApp=setup();await new Promise(setImmediate);
  const evidenceSource = {
    id: '2096455601215729824', title: 'Araştırılan kaynak', text: 'Kısmi ilk metin',
    url: 'https://x.com/example/status/2096455601215729824',
    source_evidence: {
      full_text_status: 'full', full_text: 'Tam kaynak metni', checked_at: '2026-09-06',
      verified_claims: ['Birincil belgede iş akışı ayrımı anlatılıyor.'],
      unsupported_claims: ['Uyurken bütün fikirleri bulduğu doğrulanmadı.'],
      linked_primary_sources: [{ url: 'https://example.org/source', relation: 'Yöntem açıklaması' }],
      rights_media_note: 'Kaynak medya yeniden kullanılmaz.'
    }
  };
  evidenceApp.hooks.applyServerState({ bookmarks: [evidenceSource], drafts: [], jobs: [], import_complete: false });
  assert.match(evidenceApp.elements['import-scope'].textContent || '', /seçili kayıtlar/i, 'kısmi aktarım kaynağın yanında görünür olmalıdır');
  const sourceCard = evidenceApp.elements['source-list'].children[0];
  assert.match(sourceCard.children[0].children.map((child) => child.textContent).join(' '), /Tam metin okundu/, 'kartta erişim kapsamı görünmelidir');
  evidenceApp.hooks.selectSource(evidenceSource);
  const definitions = evidenceApp.elements['evidence-content'].children.map((row) => row.children.map((part) => part.textContent).join(': ')).join('\n');
  assert.match(definitions, /Tam kaynak metni/, 'kanıt çekmecesi kalıcı tam metni kullanmalıdır');
  assert.match(definitions, /Uyurken bütün fikirleri bulduğu doğrulanmadı/, 'doğrulanmamış iddia gizlenmemelidir');
  assert.match(definitions, /Kaynak medya yeniden kullanılmaz/, 'hak notu görünmelidir');
  assert.match(definitions, /https:\/\/example.org\/source/, 'birincil belge bağlantısı görünmelidir');
  evidenceApp.hooks.applyServerState({ bookmarks: [{ id: 'unknown', title: '<img onerror=evil()>' }], drafts: [], jobs: [], import_complete: true });
  assert.match(evidenceApp.elements['import-scope'].textContent, /bildirildi/, 'tam aktarım beyanı bağımsız doğrulama gibi gösterilmemelidir');
  const unknownCard = evidenceApp.elements['source-list'].children[0];
  assert.match(unknownCard.children[0].children.map((child) => child.textContent).join(' '), /Metin kapsamı doğrulanmadı/, 'kanıtsız kaynak yeşil onay almamalıdır');
  assert.equal(unknownCard.children[0].children[0].textContent, '<img onerror=evil()>', 'kaynak başlığı HTML olarak yürütülmemelidir');
  const previewApp = setup(); await new Promise(setImmediate);
  const pd = { id: 'draft-1', source_id: 'source-1', revision: 4, assets: [
    {name:'same.jpg',revision:3,sha256:'old',kind:'photo',stale:true,alt_text:'Eski'},
    {name:'same.jpg',revision:4,sha256:'current',kind:'photo',stale:false,alt_text:'Güncel açıklama'}
  ], primary_media:{name:'same.jpg',revision:4,sha256:'current'} };
  previewApp.hooks.state.draft = pd;
  const cleanPayload = previewApp.hooks.editorPayload(); delete cleanPayload.revision;
  previewApp.hooks.state.cleanSignature = JSON.stringify(cleanPayload);
  previewApp.elements['preview-primary-media'] ||= new FakeElement('preview-primary-media');
  previewApp.hooks.renderAssets();
  const slot = previewApp.elements['preview-primary-media'];
  assert.equal(slot.children.length, 1, 'ana gönderi seçilmiş tek güncel medyayı göstermeli');
  assert.match(slot.children[0].src, /4.*current/, 'medya adresi sürüm ve hash taşımalı');
  assert.equal(slot.children[0].alt, 'Güncel açıklama');
  previewApp.elements['main-copy'].value = 'Kaydedilmemiş yeni metin';
  previewApp.hooks.renderAssets();
  assert.equal(slot.children.length, 0, 'kaydedilmemiş metinde eski medya güncelmiş gibi sunulmamalı');
  previewApp.hooks.state.selectedSource = {id:'source-1'};
  previewApp.hooks.selectSource({id:'source-1'});
  assert.equal(previewApp.elements['main-copy'].value, 'Kaydedilmemiş yeni metin', 'aynı kaynağa yeniden tıklamak da dirty tamponu silmemeli');
  const workspaceApp = setup(); await new Promise(setImmediate);
  workspaceApp.elements['brief-problem'].value='Veriyi tekrar bulamıyor';
  workspaceApp.elements['brief-benefit'].value='Kaynaklı not çıkarabilir';
  workspaceApp.elements['brief-depth'].value='advanced';
  workspaceApp.elements['brief-content-type'].value='method';
  workspaceApp.elements['brief-provenance'].value='original_contribution';
  workspaceApp.elements['brief-contribution'].value='Özgün kontrol adımları';
  const brief = workspaceApp.hooks.editorPayload().reader_brief;
  assert.equal(brief?.depth,'advanced','teknik derinlik kalıcı payload olmalı');
  assert.equal(brief?.benefit,'Kaynaklı not çıkarabilir');
  workspaceApp.hooks.applyServerState({bookmarks:[{id:'source-1',title:'Kaynak'}],drafts:[{id:'d1',source_id:'source-1',revision:2,title:'İNCELEME ADAYI · Araç',main:'mekanizma',assets:[],copy_approved:false},{id:'d2',source_id:'source-1',revision:3,title:'Diğer taslak',main:'yazı',assets:[],copy_approved:false}],jobs:[]});
  workspaceApp.elements['draft-search'].value='mekanizma';
  await workspaceApp.elements['draft-search'].fire('input');
  assert.equal(workspaceApp.elements['works-list'].children.length,1,'arama görünür Çalışmalar ekranında sonucu göstermeli');
  assert.match(workspaceApp.elements['works-list'].children[0].children[0].textContent,/Araç/);

  // Focused layout deliberately removes saved three-panel presets; mobile behavior is covered by the CSS contract below.


  // Editorial-focus redesign contract: bare / is a deliberate Çalışmalar landing, not an inferred draft.
  const redesignedHTML=fs.readFileSync(require('node:path').join(__dirname,'static','index.html'),'utf8');
  const redesignedCSS=fs.readFileSync(require('node:path').join(__dirname,'static','workspace.css'),'utf8');
  assert.match(redesignedHTML,/id="works-landing"[^>]*aria-labelledby="works-title"/,'bare route has an explicit Çalışmalar landing');
  assert.match(redesignedHTML,/id="works-list"/,'landing owns the selectable works list');
  assert.match(redesignedHTML,/id="start-first-work"[\s\S]*İlk kaynağını ekle/,'empty state has a truthful first-start action');
  assert.doesNotMatch(redesignedHTML,/data-layout="research"|>Araştır<|>Yaz<|>İncele</,'misleading layout presets are not primary UI');
  assert.doesNotMatch(redesignedHTML,/QA kaydı/,'primary UI never invents a QA classification');
  assert.match(redesignedHTML,/id="connection-banner"/,'offline/read-only state has a visible honest banner');
  assert.match(redesignedHTML,/id="editorial-focus"/,'selected work uses a coherent single focus surface');
  assert.match(redesignedHTML,/id="selected-source-summary"/,'selected source summary remains in the editing context');
  assert.match(redesignedHTML,/id="human-copy-approval"/,'there is one named human copy approval control');
  assert.match(redesignedCSS,/@media \(max-width:420px\)[\s\S]*\.editorial-focus/,'320/390 layout has an explicit focused surface rule');
  console.log('frontend flow handlers: ok');
})().catch((error) => { console.error(error.stack || error); process.exitCode = 1; });
