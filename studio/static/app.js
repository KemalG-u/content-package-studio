(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = {
    data: null, selectedSource: null, draft: null, remoteDraft: null,
    filter: 'all', polling: null, busy: 0, activeJob: null, cleanSignature: '', primaryAsset: '', history: [], selectedAngle: null, angles: [], openedInitial: false, previewFingerprint: ''
  };
  const els = {
    apiDot: $('api-dot'), apiStatus: $('api-status'), sourceList: $('source-list'), sourceSearch: $('source-search'), sourceFilters: $('source-filters'), importScope: $('import-scope'),
    noDraft: $('no-draft'), form: $('draft-form'), revision: $('revision-badge'), message: $('editor-message'), loading: $('loading-overlay'), loadingText: $('loading-text'),
    mode: $('draft-mode'), objective: $('draft-objective'), audience: $('draft-audience'), planDate: $('plan-date'), title: $('draft-title'), main: $('main-copy'), reply: $('reply-copy'), feedback: $('feedback'), alt: $('alt-text'), voice: $('voice'), consent: $('cloud-consent'), scenes: $('scene-list'),
    mainCount: $('main-count'), replyCount: $('reply-count'), previewMain: $('preview-main'), previewReply: $('preview-reply'), assetList: $('asset-list'), mediaStatus: $('media-status'),
    drawer: $('evidence-drawer'), drawerContent: $('evidence-content'), drawerToggle: $('evidence-toggle'),
    generate: $('generate-draft'), rewrite: $('rewrite-draft'), jobStatus: $('job-status'), reloadServer: $('reload-server-draft'),
    uploadFile: $('asset-upload'), uploadRights: $('asset-rights'), uploadAlt: $('asset-alt'), uploadButton: $('upload-asset'), uploadProgress: $('upload-progress'), uploadNote: $('upload-note'), vpsAsr: $('vps-asr-allowed'),
    primaryWrap: $('native-primary-wrap'), primary: $('native-primary'),
    draftChooser: $('draft-chooser'), draftChoices: $('draft-choices'), historyPanel: $('history-panel'), historyList: $('history-list'), showHistory: $('show-history'),
    projectSourcesPanel: $('project-sources-panel'), projectSourceList: $('project-source-list'), projectSourceCount: $('project-source-count'), attachToProject: $('attach-to-project')
  };

  function safeText(value) { return value == null ? '' : String(value); }
  function formatError(error) { return error && error.message ? error.message : 'İşlem tamamlanamadı.'; }
  function message(text = '', kind = '') { els.message.textContent = text; els.message.className = `message${kind ? ` is-${kind}` : ''}`; }
  function setBusy(on, text = 'İşleniyor…') { state.busy = Math.max(0, state.busy + (on ? 1 : -1)); els.loading.hidden = state.busy === 0; els.loadingText.textContent = text; }
  function setUploadNote(text = '', kind = '') { els.uploadNote.textContent = text; els.uploadNote.className = `inline-note${kind ? ` is-${kind}` : ''}`; }
  function canWrite(){return Boolean(state.data && state.data.read_only!==true && state.data.capabilities?.read_only!==true);}
  function syncWriteControls(){const allowed=canWrite();for(const id of ['manual-import','create-draft','save-draft','editorial-review','model-review','upload-asset','export-zip','suggest-angles','claim-original','claim-source','add-scene']){const element=$(id);if(element)element.disabled=!allowed;}els.generate.disabled=!allowed||Boolean(state.activeJob);els.rewrite.disabled=!allowed||Boolean(state.activeJob);updateFinalGate();}
  async function api(path, options = {}) {
    if((options.method||'GET').toUpperCase()!=='GET' && path!=='/api/measure' && !canWrite())throw new Error('Studio bağlantısı yok veya salt-okunur; değişiklik gönderilmedi.');
    const response = await fetch(path, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
      ...options
    });
    const type = response.headers.get('content-type') || '';
    const body = type.includes('application/json') ? await response.json().catch(() => ({})) : null;
    if (!response.ok) { const error = new Error((body && body.error) || `Sunucu isteği kabul etmedi (${response.status}).`); error.status = response.status; error.body = body; throw error; }
    return { body, response };
  }
  function draftBySource(sourceId) { return (state.data?.drafts || []).find((draft) => String(draft.source_id) === String(sourceId)) || null; }
  function draftsBySource(sourceId) { return (state.data?.drafts || []).filter((draft) => String(draft.source_id) === String(sourceId)); }
  function sourceById(id) { return (state.data?.bookmarks || []).find((source) => String(source.id) === String(id)) || null; }
  function assetUrl(asset) {
    const name = safeText(asset.name || asset.filename);
    return name && state.draft ? `/api/assets/${encodeURIComponent(state.draft.id)}/${encodeURIComponent(name)}?revision=${encodeURIComponent(asset.revision)}&sha256=${encodeURIComponent(asset.sha256)}` : '#';
  }
  function assetName(asset) { return safeText(asset.name || asset.filename); }
  function assetType(asset) {
    const kind = safeText(asset.kind).toLowerCase(); const mime = safeText(asset.mime).toLowerCase(); const name = assetName(asset).toLowerCase();
    if (kind === 'photo' || kind === 'image' || mime.startsWith('image/')) return 'image';
    if (kind === 'video' || mime.startsWith('video/')) return 'video';
    if (kind === 'audio' || mime.startsWith('audio/')) return 'audio';
    if (kind === 'subtitle' || mime === 'text/vtt' || /\.(vtt|srt)$/.test(name)) return 'subtitle';
    return kind || 'file';
  }
  function isUploadedAsset(asset) { return asset.uploaded === true || asset.user_uploaded === true || asset.source === 'upload' || asset.origin === 'upload' || asset.provenance === 'local_upload'; }
  function editorPayload(extra = {}) {
    return { revision: state.draft?.revision, mode: els.mode.value, objective: els.objective.value.trim(), audience: els.audience.value.trim(), plan_date: els.planDate.value, title: els.title.value.trim(), main: els.main.value, reply: els.reply.value, feedback: els.feedback.value.trim(), alt_text: els.alt.value.trim(), scene_texts: sceneValues(), cloud_tts_allowed: els.consent.checked, voice: els.voice.value, reader_brief: readBrief(), rights_note:$('rights-note').value.trim(), ...claimPatch(), ...extra };
  }
  function claimPatch() {
    const raw=$('claim-map-editor').value.trim();let value=null;
    if(raw){try{value=JSON.parse(raw);}catch(_){return {claim_map:raw};}}
    return JSON.stringify(value)===JSON.stringify(state.draft?.claim_map || null) ? {} : {claim_map:value};
  }
  function mapCurrentCopy(original) {
    if(!state.draft || !els.main.value.trim()){message('İddia eşleştirmek için önce bir metin yaz.','error');return;}
    if($('brief-provenance').value==='tested_external'){message('Deney için yüklenmiş gerçek kanıtın adını, SHA256 özetini ve sınırlamasını gelişmiş haritaya bağla. Bu düğme deney kanıtı üretmez.','error');return;}
    const url=$('claim-source-url').value.trim();
    if(!original && !url.startsWith('https://')){message('Kaynak iddiası için listeden doğrulanmış bir kaynak seç.','error');return;}
    const evidence=original ? {id:'e1',kind:'original',limitation:'Özgün öneri; dış deney veya doğrulanmış performans sonucu değildir.'} : {id:'e1',kind:'source',url,excerpt:$('claim-source-excerpt').value.trim()};
    const surfaces=[['title',els.title.value],['main',els.main.value],['reply',els.reply.value],...sceneValues().map((text,i)=>['scene-'+i,text])];
    const claims=surfaces.filter(([,text])=>text.trim()).map(([id,text])=>({id,text,kind:(original||id==='title')?'original':'fact',evidence_refs:[id==='title'&&!original?'title-note':'e1']}));
    const evidenceItems=[evidence];if(!original && els.title.value.trim())evidenceItems.push({id:'title-note',kind:'original',limitation:'Editoryal paket başlığı; deney sonucu değildir.'});
    $('claim-map-editor').value=JSON.stringify({claims,evidence:evidenceItems},null,2);updatePreview();
    message('Ana metin ve yanıt eşleştirildi; kaydetmeden önce olgu/yorum ayrımını kontrol et. Kaynak bağlantısı tek başına doğruluk kanıtı değildir.');
  }
  function readBrief() { return { reader_problem:$('brief-problem').value.trim(), benefit:$('brief-benefit').value.trim(), depth:$('brief-depth').value || 'beginner', tone:$('brief-tone').value || 'neutral', content_type:$('brief-content-type').value || 'explainer', provenance:$('brief-provenance').value || 'researched_external', original_contribution:$('brief-contribution').value.trim(), selected_angle:state.selectedAngle }; }
  function populateBrief(draft) {
    const brief = draft.reader_brief || {};
    $('brief-tone').value=brief.tone || 'neutral';
    $('brief-problem').value=brief.reader_problem || draft.objective || ''; $('brief-benefit').value=brief.benefit || '';
    $('brief-depth').value=brief.depth || 'beginner'; $('brief-content-type').value=brief.content_type || 'explainer'; $('brief-provenance').value=brief.provenance || 'researched_external'; $('brief-contribution').value=brief.original_contribution || '';
    state.selectedAngle=brief.selected_angle || null; $('brief-angle').value=state.selectedAngle?.title || ''; $('brief-angle').readOnly=true; $('angle-options').replaceChildren();
    $('brief-summary').textContent=brief.benefit || 'Okur faydasını ve özgün katkıyı belirle';
    $('brief-panel').open=!draft.main;
    if(window.innerWidth<=768 && draft.main)$('source-box').open=false;
    $('claim-map-editor').value=draft.claim_map ? JSON.stringify(draft.claim_map,null,2) : '';
    $('rights-note').value=draft.rights_note || '';
    const fallback=draft.source_snapshot || {url:draft.source_url,verified_urls:[...(draft.source_evidence?.linked_primary_sources || []).map(e=>e.url),...(draft.claim_map?.evidence || []).filter(e=>e.kind==='source').map(e=>e.url)]};
    const projectSources=Array.isArray(draft.project_sources) && draft.project_sources.length ? draft.project_sources : [fallback];
    const urls=[...new Set(projectSources.flatMap(source=>[source.url,...(source.verified_urls || [])]).filter(Boolean))];
    $('claim-source-url').replaceChildren();for(const url of urls){const option=document.createElement('option');option.value=url;option.textContent=url;$('claim-source-url').append(option);}
    $('claim-source-excerpt').value=draft.claim_map?.evidence?.find(e=>e.kind==='source')?.excerpt || '';
  }
  function signature() { const payload = editorPayload(); delete payload.revision; return JSON.stringify(payload); }
  function isDirty() { return Boolean(state.draft) && signature() !== state.cleanSignature; }
  // Approval counts only when it belongs to the current fingerprint (server rule: app.py final_gate_for).
  function hasValidApproval(draft) { const fingerprint=draft?.approvals?.copy?.fingerprint; return Boolean(draft?.copy_approved && fingerprint && fingerprint===draft.approval_fingerprint); }
  function updateFinalGate() {
    const proposal=pendingProposal(); const dirty=isDirty(); const approved=hasValidApproval(state.draft); const hasText=Boolean(state.draft && els.main.value.trim());
    const gateCapable=state.data?.capabilities?.final_package_gate===true;
    const pageCurrent=Boolean($('final-reviewed-row') && $('draft-identity'));
    const previewCurrent=Boolean(state.previewFingerprint) && state.previewFingerprint===state.draft?.approval_fingerprint;
    const writable=canWrite();
    const blockers=[];
    if(state.draft && !state.draft.reader_brief?.selected_angle)blockers.push('İçerik hedefinden anlatım açısı seç.');
    if(state.draft && !state.draft.claim_map)blockers.push('İddiaları kaynak olgusu veya özgün öneri olarak işaretleyip kaydet.');
    if(dirty)blockers.push('Metin değişikliklerini kaydet.');
    if(proposal)blockers.push('Bekleyen model önerisine karar ver.');
    setStatusText('approval-blockers',blockers.length?'Onaydan önce: '+blockers.join(' '):'Temel alanlar hazır. Kural kontrolü ve sunucu doğrulaması ayrıca gerekir.');
    const approveBlocked=!writable || !hasText || dirty || Boolean(proposal) || approved || blockers.length>0;
    $('final-approve-copy').disabled=true; $('approve-copy').disabled=approveBlocked;
    $('final-approve-copy').hidden=true; const reviewedRow=$('final-reviewed-row'); if(reviewedRow)reviewedRow.hidden=!approved; $('final-save-package').hidden=!approved;
    $('save-package').disabled=!writable || !hasText;
    const ready=writable && hasText && approved && !dirty && !proposal && gateCapable && pageCurrent && previewCurrent;
    $('final-save-package').disabled=!ready || !$('final-reviewed').checked;
    const previewStatus=!writable?'Studio bağlantısı yok veya salt-okunur; yeni kayıt oluşturulamaz.':proposal?'Öneri henüz karara bağlanmadı; son paket bu metni içermez.':dirty?'Önce taslak değişikliklerini kaydet; medya ve onay yeniden incelenir.':!hasText?'Önce içerik metnini hazırlayın.':!approved?'Önce bu metni insan olarak onaylayın.':!pageCurrent?'Sayfanın eski sürümü açık; son kayıt için sayfayı yenileyin. Ara paket kullanılabilir.':!gateCapable?'Sunucu eski sürümle çalışıyor; son kayıt kapısı için Studio’yu kapatıp yeniden başlatın. Ara paket kullanılabilir.':!previewCurrent?'Önizleme açıldıktan sonra taslak değişti; önizlemeyi yeniden açın.':!$('final-reviewed').checked?'Yerel önizlemeyi kontrol edip işaretleyin.':'Metin onaylı yerel paket D: klasörüne kaydedilebilir; X’e gönderilmez.';
    setStatusText('final-preview-status',previewStatus);
    $('workflow-status').classList.toggle('is-dirty',dirty);
    const workflowText=!state.selectedSource && !state.draft?'01 Kaynak seç veya ekle.':!state.draft?'01 Kaynak seçildi → hedefi girip içerik projesi oluştur.':proposal?'02 Model önerisi hazır → önizle, düzenlemeye al veya vazgeç. Henüz onay değil.':dirty?'02 Kaydedilmemiş metin var → taslağı kaydet, sonra incele.':!approved?'03 Metin + varsa medyayı incele → düzelt veya insan olarak onayla.':'04 Metin onaylı → son yerel önizlemeyi aç, kontrol et ve paketi D:’ye kaydet.';
    setStatusText('workflow-status',workflowText);
  }
  function syncDirty() {
    const dirty = isDirty();
    els.reloadServer.hidden = !state.remoteDraft;
    if (dirty && !state.remoteDraft) els.revision.textContent = `REV. ${state.draft.revision} · kaydedilmedi`;
    updateFinalGate();
  }
  let countTimer = null;
  let countVersion = 0;
  function updateCounts() {
    const version = ++countVersion;
    clearTimeout(countTimer);
    els.mainCount.textContent = '…'; els.replyCount.textContent = '…';
    countTimer = setTimeout(async () => {
      try {
        const { body } = await api('/api/measure', { method: 'POST', body: JSON.stringify({ texts: [els.main.value, els.reply.value] }) });
        if (version !== countVersion) return;
        [els.mainCount, els.replyCount].forEach((label, i) => { label.textContent = `${body.results[i].weighted_length}/280`; label.title = 'twitter-text ağırlıklı X sayacı'; });
      } catch (_) { if (version === countVersion) { els.mainCount.textContent = '?'; els.replyCount.textContent = '?'; } }
    }, 350);
  }
  function updatePreview() { const proposal=pendingProposal(); const showingProposal=Boolean(proposal && proposal.revision===state.draft?.revision && !isDirty()); $('preview-scope').textContent=showingProposal?'Model önerisi · uygulanmadı · insan onayı değil':'Kayıtlı/yerel taslak · yayınlanmadı'; els.previewMain.textContent=(showingProposal?proposal.data.main:els.main.value).trim() || 'Ana metin henüz yazılmadı.'; els.previewReply.textContent=(showingProposal?proposal.data.reply:els.reply.value).trim(); const notes=showingProposal?(proposal.data.scene_texts || []):sceneValues(); $('preview-details-panel').hidden=!notes.length; $('preview-details').replaceChildren(); for(const note of notes){const p=document.createElement('p');p.textContent=note;$('preview-details').append(p);} renderPrimaryPreview(showingProposal); updateCounts(); syncDirty(); }
  function renderPrimaryPreview(showingProposal=false) {
    const slot = $('preview-primary-media'); slot.replaceChildren();
    if(showingProposal){$('preview-media-note').textContent='Öneride ekli medya yok · mevcut revizyonun medyası öneriye otomatik taşınmaz.';return;}
    const draft = state.draft; const primary = draft?.primary_media;
    const asset = primary && !isDirty() && (draft.assets || []).find(a => !a.stale && a.revision === draft.revision && a.revision === primary.revision && a.name === primary.name && a.sha256 === primary.sha256 && ['image','video'].includes(assetType(a)));
    $('preview-media-note').textContent = isDirty() ? 'Kaydedilmemiş metin: medya yeniden incelenene kadar gizli.' : asset ? `Seçili medya · REV. ${asset.revision} · ${asset.alt_text || 'Alt metin eksik'}` : 'Yalnız metin · güncel ana medya seçilmedi.';
    if (!asset) return;
    const media = document.createElement(assetType(asset) === 'image' ? 'img' : 'video');
    media.src = assetUrl(asset); media.className = 'post-media';
    if (assetType(asset) === 'image') { media.alt = safeText(asset.alt_text); }
    else { media.controls = true; media.preload = 'metadata'; const vtt = subtitleAsset(asset, draft.assets.filter(a => a.revision === draft.revision && !a.stale)); if (vtt) { const track = document.createElement('track'); track.kind='subtitles'; track.src=assetUrl(vtt); track.srclang='tr'; track.label='Türkçe'; track.default=true; media.append(track); } }
    slot.append(media);
  }
  function sceneValues() { return [...els.scenes.querySelectorAll('input')].map((input) => input.value.trim()).filter(Boolean); }
  function renderScenes(items = []) {
    els.scenes.replaceChildren();
    items.forEach((text, index) => {
      const row = document.createElement('div'); row.className = 'scene-row';
      const number = document.createElement('span'); number.className = 'scene-index'; number.textContent = String(index + 1).padStart(2, '0');
      const input = document.createElement('input'); input.value = safeText(text); input.dataset.scene = String(index); input.setAttribute('aria-label', `${index + 1}. sahne metni`); input.placeholder = 'Kısa sahne metni';
      input.addEventListener('input',updatePreview);
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'scene-remove'; remove.textContent = '×'; remove.setAttribute('aria-label', `${index + 1}. sahneyi kaldır`);
      remove.addEventListener('click', () => { const values = [...els.scenes.querySelectorAll('input')].map(input=>input.value); values.splice(index, 1); renderScenes(values); updatePreview(); });
      row.append(number, input, remove); els.scenes.append(row);
    });
  }
  function renderSources() {
    const query = els.sourceSearch.value.trim().toLocaleLowerCase('tr'); const all = state.data?.bookmarks || [];
    els.importScope.textContent = !state.data ? 'Kaynak kapsamı henüz alınmadı.' : state.data.import_complete === true
      ? `${all.length} kaynak · Tam aktarım olarak bildirildi; bağımsız arşiv doğrulaması değil.`
      : `Seçili kayıtlar: ${all.length} kaynak · Linkler otomatik okunmaz, projelere yalnız açık seçimle eklenir.`;
    const sources = all.filter((source) => {
      const haystack = [source.title, source.text, source.author, source.url].map(safeText).join(' ').toLocaleLowerCase('tr');
      const filterMatches=state.filter === 'all' || state.filter === 'manual' && safeText(source.source).startsWith('manual_') || source.source === state.filter || source.import_source === state.filter;
      return filterMatches && (!query || haystack.includes(query));
    });
    setStatusText('source-result-count',state.data ? `${sources.length} kaynak bulundu` : '');
    els.sourceList.replaceChildren();
    if (!state.data) return appendTo(els.sourceList, 'API’den kaynaklar bekleniyor.');
    if (!sources.length) return appendTo(els.sourceList, all.length ? 'Bu filtrede kaynak yok.' : 'Henüz aktarılmış yer işareti yok.');
    sources.forEach((source) => {
      const entry = document.createElement('div'); entry.className = 'source-entry';
      const card = document.createElement('button'); card.type = 'button'; card.className = 'source-card'; card.dataset.sourceId = safeText(source.id); card.setAttribute('aria-current', String(state.selectedSource?.id) === String(source.id));
      const title = document.createElement('strong'); title.textContent = safeText(source.title) || 'Başlıksız kaynak';
      const meta = document.createElement('span'); meta.textContent = [safeText(source.kind || 'x').replace('_',' '), safeText(source.author), safeText(source.observed_at)].filter(Boolean).join(' · ') || 'Kaynak kaydı';
      const evidenceStatus = document.createElement('span'); evidenceStatus.className = 'source-evidence-status';
      evidenceStatus.textContent = source.source_evidence?.full_text_status === 'full' ? 'Tam metin okundu · İddialar ayrı incelenir' : safeText(source.source).startsWith('manual_') && safeText(source.text).trim() ? 'Metin taşındı · iddialar doğrulanmadı (kapsam belirsiz)' : 'Metin kapsamı doğrulanmadı';
      card.append(title, meta, evidenceStatus); card.addEventListener('click', () => selectSource(source)); entry.append(card);
      if (safeText(source.source).startsWith('manual_')) {
        const linked = (state.data?.drafts || []).some(draft => String(draft.source_id) === String(source.id) || (draft.project_sources || []).some(item => String(item?.id) === String(source.id)));
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'source-delete text-button'; remove.textContent = 'Kaynağı sil';
        remove.disabled = !canWrite() || linked;
        remove.setAttribute('aria-label', `${safeText(source.title) || 'Başlıksız kaynak'} kaynağını sil`);
        if (linked) remove.title = 'Bu kaynak bir çalışmada kullanılıyor; silinemez.';
        remove.addEventListener('click', () => deleteSource(source)); entry.append(remove);
      }
      els.sourceList.append(entry);
    });
  }
  async function deleteSource(source) {
    if (!canWrite()) { message('Studio bağlantısı yok veya salt-okunur; kaynak silinmedi.', 'error'); return; }
    if (isDirty()) { message('Kaydedilmemiş editör değişikliklerini önce kaydet; kaynak silinmedi.', 'error'); return; }
    if (!window.confirm(`“${safeText(source.title) || 'Başlıksız kaynak'}” kaynağı silinsin mi?\n${safeText(source.url)}\nBu işlem geri alınamaz. Bir çalışmada kullanılan kaynak silinmez.`)) return;
    setBusy(true, 'Kaynak siliniyor…');
    let deleted = false;
    try {
      await api(`/api/sources/${encodeURIComponent(source.id)}`, { method: 'DELETE', body: JSON.stringify({ expected_url: source.url }) });
      deleted = true;
      const { body } = await api('/api/state');
      if ((body.bookmarks || []).some(item => String(item.id) === String(source.id))) throw new Error('Kaynak listede hâlâ görünüyor; sayfayı yenile.');
      if (String(state.selectedSource?.id) === String(source.id)) { state.selectedSource = null; state.draft = null; state.remoteDraft = null; }
      applyServerState(body, { preserveEditor: false });
      showFocus(); setStage('source'); message('Kaynak silindi. Yeni bağlantı ekleyebilirsin.', 'success');
    } catch (error) { message(`${deleted ? 'Silme isteği kabul edildi ama sonuç doğrulanamadı' : 'Kaynak silinemedi'}: ${formatError(error)}`, 'error'); }
    finally { setBusy(false); }
  }
  function appendTo(parent, text) { const element = document.createElement('p'); element.className = 'empty-state'; element.textContent = text; parent.append(element); }
  function setStatusText(id,text){const node=$(id);if(node && node.textContent!==text)node.textContent=text;}
  function renderDraftChooser() {
    const drafts = state.selectedSource ? draftsBySource(state.selectedSource.id) : [];
    els.draftChooser.hidden = !state.selectedSource || drafts.length < 2;
    els.draftChoices.replaceChildren();
    drafts.forEach((draft) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'draft-choice';
      button.setAttribute('aria-current', String(state.draft?.id) === String(draft.id));
      const title = document.createElement('strong'); title.textContent = workTitle(draft);
      const meta = document.createElement('span'); meta.textContent = `REV. ${draft.revision}`;
      button.append(title, meta); button.addEventListener('click', () => selectDraft(draft)); els.draftChoices.append(button);
    });
  }
  function subtitleAsset(media, assets) {
    const name = assetName(media); const vtt = assets.filter((asset) => !asset.stale && (safeText(asset.mime).toLowerCase() === 'text/vtt' || /\.vtt$/i.test(assetName(asset))));
    return vtt.find((asset) => safeText(asset.for_asset) === name) || (name === 'video.mp4' ? vtt.find((asset) => assetName(asset) === 'captions.tr.vtt') : null) || (name === 'voice.mp3' ? vtt.find((asset) => assetName(asset) === 'voice.tr.vtt') : null) || null;
  }
  function renderAssets() {
    renderPrimaryPreview();
    els.assetList.replaceChildren(); const assets = state.draft?.assets || [];
    const candidates = assets.filter((asset) => !asset.stale && ['image', 'video'].includes(assetType(asset)));
    els.primaryWrap.hidden = candidates.length === 0; els.primary.replaceChildren();
    const emptyOption = document.createElement('option'); emptyOption.value = ''; emptyOption.textContent = 'Birincil medya seçilmedi'; els.primary.append(emptyOption);
    candidates.forEach((asset) => { const option = document.createElement('option'); option.value = assetName(asset); option.textContent = `${assetType(asset) === 'video' ? 'Video' : 'Fotoğraf'} · ${assetName(asset)}`; els.primary.append(option); });
    if (!candidates.some((asset) => assetName(asset) === state.primaryAsset)) state.primaryAsset = safeText(state.draft?.primary_media?.name);
    if (!candidates.some((asset) => assetName(asset) === state.primaryAsset)) state.primaryAsset = '';
    els.primary.value = state.primaryAsset;
    if (!state.draft) return appendTo(els.assetList, 'Medya üretimi için bir taslak açın.');
    if (!assets.length) return appendTo(els.assetList, 'Bu revizyon için üretilmiş veya yüklenmiş medya yok.');
    assets.forEach((asset) => {
      const type = assetType(asset); const name = assetName(asset); const card = document.createElement('div'); card.className = 'asset-card';
      const link = document.createElement('a'); link.textContent = name || 'Medya dosyası'; link.href = assetUrl(asset); link.target = '_blank'; link.rel = 'noopener';
      const meta = document.createElement('span'); meta.textContent = [type, safeText(asset.mime), asset.stale ? 'ESKİ REVİZYON — ZIP’e dahil edilmez' : 'geçerli', isUploadedAsset(asset) ? 'yüklenen dosya' : ''].filter(Boolean).join(' · '); card.append(link, meta);
      if (!asset.stale && ['image', 'video', 'audio'].includes(type)) {
        let preview;
        if (type === 'image') preview = document.createElement('img');
        else if (type === 'video') preview = document.createElement('video');
        else preview = document.createElement('audio');
        preview.src = link.href; preview.className = 'asset-preview';
        if (type === 'image') { preview.alt = safeText(asset.alt_text || state.draft.alt_text || state.draft.title || 'Yüklenen medya'); preview.loading = 'lazy'; }
        else { preview.controls = true; preview.preload = 'metadata'; }
        const vtt = subtitleAsset(asset, assets);
        if (['video', 'audio'].includes(type) && vtt) { const track = document.createElement('track'); track.kind = 'subtitles'; track.src = assetUrl(vtt); track.srclang = 'tr'; track.label = 'Türkçe'; track.default = true; preview.append(track); }
        card.append(preview);
      }
      if (!asset.stale && type === 'subtitle') { const note = document.createElement('span'); note.className = 'caption-note'; note.textContent = 'Hazır altyazı dosyasıdır; indirilip seçtiğiniz editörde düzenlenebilir.'; card.append(note); }
      if (!asset.stale && isUploadedAsset(asset) && ['audio', 'video'].includes(type)) {
        const action = document.createElement('button'); action.type = 'button'; action.className = 'text-button'; action.textContent = 'Türkçe altyazı çıkar'; action.addEventListener('click', () => transcribeAsset(asset)); card.append(action);
      }
      els.assetList.append(card);
    });
  }
  function renderEvidence() {
    const draft = state.draft;
    const source = draft ? (draft.source_snapshot || {url:draft.source_url,observed_at:draft.observed_at,evidence_note:draft.evidence_note,verified_urls:[]}) : state.selectedSource;
    const evidence = draft ? draft.source_evidence : source?.source_evidence;
    const rows = [['Kaynak URL’si', source?.url || draft?.source_url || 'Kayıt yok'], ['Kaynak metni', (evidence?.full_text_status === 'full' ? evidence.full_text : source?.text) || 'Kaynak metni bu yanıtta yok.'], ['Yazar', source?.author || 'Belirtilmemiş'], ['Görülme kaydı', source?.observed_at || 'Belirtilmemiş'], ['Kanıt notu', source?.evidence_note || draft?.evidence_note || 'Not yok'], ['Doğrulanan bağlantılar', (source?.verified_urls || draft?.verified_urls || []).join('\n') || 'Yok'], ['Araştırma kaydı', evidence?.checked_at || 'Kayıtlı araştırma yok.'], ['Erişim kapsamı', evidence?.full_text_status === 'full' ? 'Tam metin okundu. Bu, tüm iddiaların doğru olduğu anlamına gelmez.' : 'Tam metin erişimi doğrulanmadı.'], ['Doğrulanan bilgiler', (evidence?.verified_claims || []).join('\n') || 'Yok'], ['Doğrulanmamış iddialar', (evidence?.unsupported_claims || []).join('\n') || 'Kayıt yok; doğrulanmış kabul etmeyin.'], ['Birincil belgeler ve ilişkisi', (evidence?.linked_primary_sources || []).map((item) => `${item.url}\n${item.relation}`).join('\n\n') || 'Yok'], ['Medya hak notu', evidence?.rights_media_note || 'Hak değerlendirmesi kaydedilmedi.'], ['Taslak oluşturulurken kaynak kaydı', draft?.source_evidence?.checked_at || 'Bu taslakta kayıtlı araştırma kopyası yok.'], ['İnceleme durumu', draft ? (draft.copy_approved ? 'Metin insan tarafından onaylandı. Otomatik editoryal denetim değildir.' : 'İnsan metin onayı bekliyor.') : 'Taslak yok']];
    els.drawerContent.replaceChildren(); rows.forEach(([term, definition]) => { const wrap = document.createElement('div'); const dt = document.createElement('dt'); const dd = document.createElement('dd'); dt.textContent = term; dd.textContent = safeText(definition); wrap.append(dt, dd); els.drawerContent.append(wrap); });
  }
  function renderProjectSources() {
    const sources=state.draft?.project_sources || [];
    els.projectSourcesPanel.hidden=!state.draft;
    els.projectSourceCount.textContent=`${sources.length} kaynak`;
    els.projectSourceList.replaceChildren();
    if(!sources.length)return appendTo(els.projectSourceList,'Bu içerik projesine kaynak bağlanmamış.');
    const labels={x:'X gönderisi',web:'Web sayfası',instagram:'Instagram',youtube_transcript:'YouTube transkripti'};
    for(const source of sources){const row=document.createElement('article');row.className='project-source-item';const title=document.createElement('strong');title.textContent=source.title || source.url || 'Başlıksız kaynak';const meta=document.createElement('span');meta.textContent=`${labels[source.kind] || source.kind || 'Kaynak'} · ${source.author || 'Yazar belirtilmedi'}`;const link=document.createElement('a');link.href=source.url;link.target='_blank';link.rel='noopener';link.textContent='Kaynağı aç ↗';row.append(title,meta,link);els.projectSourceList.append(row);}
  }
  function populateDraft(draft) {
    state.draft = draft || null; state.remoteDraft = null; els.reloadServer.hidden = true; els.form.hidden = !state.selectedSource; els.noDraft.hidden = Boolean(state.selectedSource);
    $('create-draft').hidden=Boolean(draft);
    els.attachToProject.disabled=!draft; if(!draft)els.attachToProject.checked=false;
    const value = draft || {}; els.revision.textContent = draft ? `REV. ${draft.revision}` : 'Yeni taslak'; els.mediaStatus.textContent = draft ? `REV. ${draft.revision}` : 'Taslak yok';
    els.mode.value = value.mode || 'research'; els.objective.value = safeText(value.objective); els.audience.value = safeText(value.audience); els.planDate.value = safeText(value.plan_date); els.title.value = safeText(value.title); els.main.value = safeText(value.main); els.reply.value = safeText(value.reply); els.feedback.value = safeText(value.feedback); els.alt.value = safeText(value.alt_text); els.voice.value = value.voice || 'tr-TR-AhmetNeural'; els.consent.checked = Boolean(value.cloud_tts_allowed); renderScenes(Array.isArray(value.scene_texts) ? value.scene_texts : []);
    const identity=$('draft-identity'); if(identity){identity.textContent = draft ? `Proje ${safeText(draft.id).slice(0, 8)} · Kaynak ${safeText(draft.source_id)}` : ''; identity.title = draft ? `Proje ${safeText(draft.id)} · Kaynak ${safeText(draft.source_id)}` : '';}
    populateBrief(value); state.primaryAsset = safeText(value.primary_media?.name); state.cleanSignature = signature(); $('final-reviewed').checked=false; state.previewFingerprint=''; state.history = []; els.historyPanel.hidden = !draft; updatePreview(); renderAssets(); renderEvidence(); renderProjectSources(); renderDraftChooser(); renderProjects(); renderReviewState();
    if (draft && window.history?.replaceState) window.history.replaceState(null,'',`#draft=${encodeURIComponent(draft.id)}`);
  }
  function renderHistory() {
    els.historyList.replaceChildren();
    if (!state.history.length) return appendTo(els.historyList, 'Kaydedilmiş revizyon yok.');
    state.history.forEach((entry) => {
      const row = document.createElement('div'); row.className = 'history-row';
      const label = document.createElement('span'); label.textContent = 'Sürüm '+entry.revision+' · '+(safeText(entry.title).trim() || safeText(entry.main).trim().slice(0,72) || 'Başlıksız çalışma');
      const action = document.createElement('button'); action.type = 'button'; action.className = 'text-button';
      const current = entry.revision === state.draft?.revision; action.textContent = current ? 'Geçerli sürüm' : 'Bu sürümden yeni taslak oluştur'; action.disabled = current;
      action.addEventListener('click', () => restoreRevision(entry.revision)); row.append(label, action); els.historyList.append(row);
    });
  }
  function selectSource(source) {
    if (isDirty()) { message('Kaydedilmemiş editör değişiklikleri korunuyor. Kaynak açmadan önce kaydedin.', 'error'); return; }
    state.selectedSource = source; showFocus(); populateDraft(draftBySource(source.id)); renderSources(); renderDraftChooser(); setStage(state.draft ? (pendingProposal() ? 'idea' : state.draft.main ? 'edit' : 'idea') : 'idea'); resumeVisibleJob(); message(state.draft ? 'Çalışma açıldı.' : 'Kaynak seçildi. Hedefi girip çalışma oluşturun.');
  }
  function selectDraft(draft) {
    if (isDirty()) { message('Kaydedilmemiş editör değişiklikleri korunuyor. Taslak açmadan önce kaydedin.', 'error'); return; }
    state.selectedSource=sourceById(draft.source_id); showFocus(); populateDraft(draft); renderSources(); renderDraftChooser(); setStage(pendingProposal() ? 'idea' : hasValidApproval(draft) ? 'preview' : draft.main ? 'edit' : 'idea'); resumeVisibleJob(); message('Seçilen çalışma açıldı.');
  }
  function applyServerState(body, { preserveEditor = true } = {}) {
    state.data = body || { bookmarks: [], drafts: [], jobs: [] };
    const latest = state.draft && (state.data.drafts || []).find((item) => String(item.id) === String(state.draft.id));
    if (latest && preserveEditor && isDirty() && latest.revision !== state.draft.revision) {
      state.remoteDraft = latest; els.reloadServer.hidden = false; els.revision.textContent = `REV. ${state.draft.revision} · sunucuda REV. ${latest.revision}`;
    } else if (latest) state.draft = latest;
    if (state.selectedSource) state.selectedSource = sourceById(state.selectedSource.id) || state.selectedSource;
    renderSources(); renderProjects(); renderJobLog(); renderProposals();
    if (state.selectedSource && (!preserveEditor || !isDirty())) populateDraft(state.draft || draftBySource(state.selectedSource.id));
    syncWriteControls();
  }
  async function refreshState({ quiet = false, preserveEditor = true } = {}) {
    try { const { body } = await api('/api/state'); applyServerState(body, { preserveEditor }); els.apiStatus.textContent = 'Yerel API bağlı'; els.apiDot.className = 'status-dot is-ok'; const banner=$('connection-banner'); const readOnly=body?.read_only===true || body?.capabilities?.read_only===true; banner.hidden=!readOnly; if(readOnly) banner.textContent='Salt-okunur oturum: güncel çalışmaları görebilirsin; kayıt, onay, model ve paket işlemleri sunucu tarafından kabul edilmez.'; if (!state.openedInitial) { state.openedInitial=true; const wanted=window.location?.hash?.replace(/^#draft=/,''); const initial=wanted ? (body.drafts || []).find(d=>d.id===wanted) : null; if(initial) selectDraft(initial); else { showWorks(); if(wanted) message('Bağlantıdaki çalışma bulunamadı. Başka çalışma açılmadı; listeden seçim yapın.','error'); } } }
    catch (error) { state.data = null; els.apiStatus.textContent = 'Yerel API başlatılmadı veya erişilemiyor'; els.apiDot.className = 'status-dot is-error'; const banner=$('connection-banner'); banner.hidden=false; banner.textContent='Çevrimdışı: kayıtlar güncel olmayabilir; değiştiren işlemler kapalıdır. Yerel Studio’yu başlatıp yeniden bağlanın.'; showWorks(); renderSources(); renderProjects(); syncWriteControls(); if (!quiet) message(`API’ye bağlanılamadı: ${formatError(error)}`, 'error'); }
  }
  async function createDraft() {
    if (!state.selectedSource) { message('Önce bir kaynak seçin.', 'error'); return null; }
    if (state.draft && isDirty()) { message('Yeni taslak açmadan önce mevcut düzenlemeyi kaydet.','error'); return null; }
    if (!els.objective.value.trim() || !els.audience.value.trim()) { message('Taslak için hedef ve okur alanlarını doldurun.', 'error'); return null; }
    setBusy(true, 'Taslak oluşturuluyor…');
    try { const brief=readBrief(); const { body } = await api('/api/drafts', { method: 'POST', body: JSON.stringify({ source_id: state.selectedSource.id, mode: els.mode.value, objective: els.objective.value.trim(), audience: els.audience.value.trim() }) }); state.draft = body; const saved=await api(`/api/drafts/${encodeURIComponent(body.id)}`,{method:'PUT',body:JSON.stringify({revision:body.revision,reader_brief:brief})}); state.draft=saved.body; await refreshState({ quiet: true, preserveEditor: false }); populateDraft(state.draft); setStage('idea',{focus:true}); $('brief-panel').open=false; if($('model-tools'))$('model-tools').open=true; message('Çalışma oluşturuldu. Öneriyi yalnız sen istediğinde model hazırlar.', 'success'); return state.draft; }
    catch (error) { message(`Taslak oluşturulamadı: ${formatError(error)}`, 'error'); return null; }
    finally { setBusy(false); }
  }
  async function saveDraft(extra = {}) {
    if (!state.draft && !(await createDraft())) return null;
    if (!isDirty() && !Object.keys(extra).length) return state.draft;
    setBusy(true, 'Taslak kaydediliyor…');
    try { const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}`, { method: 'PUT', body: JSON.stringify(editorPayload(extra)) }); state.draft = body; await refreshState({ quiet: true, preserveEditor: false }); populateDraft(state.draft); message('Taslak kaydedildi. Medya ve onay durumu yeniden değerlendirildi.', 'success'); return state.draft; }
    catch (error) {
      if (error.status === 409) { await refreshState({ quiet: true, preserveEditor: true }); message('Revizyon çakıştı. Yerel düzenleme korunuyor; sunucu sürümünü inceleyip açık düğmeyle yükleyebilirsiniz.', 'error'); }
      else message(`Taslak kaydedilemedi: ${formatError(error)}`, 'error');
      return null;
    } finally { setBusy(false); }
  }
  function setActiveJob(job) {
    state.activeJob = job ? {...job,localStartedAt:job.localStartedAt||Date.now()} : null;
    const jobRunning=Boolean(job); els.generate.disabled=jobRunning||!canWrite(); els.rewrite.disabled=jobRunning||!canWrite();
    setStatusText('job-status',job ? `${job.label} durumu alınıyor. Kayıtlı metnin korunuyor.` : '');
    els.jobStatus.hidden=!job;$('job-elapsed').hidden=!job;if(!job)$('job-elapsed').textContent='';
  }
  function renderJobProgress(job) {
    if(!state.activeJob)return;
    const reference=Date.parse(job.created_at||'');
    const since=Number.isFinite(reference)?reference:state.activeJob.localStartedAt;
    const seconds=Math.max(0,Math.floor((Date.now()-since)/1000));
    const label=state.activeJob.label;
    setStatusText('job-status',`${label} ${job.status==='running'?'çalışıyor':'sırada'}. Kayıtlı metnin korunuyor.`);
    $('job-elapsed').textContent=`${Number.isFinite(reference)?'İş kaydından':'Bu ekranda'} geçen süre: ${seconds} sn · tahmini bitiş yok.`;
    $('job-elapsed').hidden=false;
  }
  function jobFailure(job) { return safeText(job?.detail || job?.error || job?.message || 'Sunucu ayrıntı vermedi.'); }
  function resumeVisibleJob(){
    if(state.activeJob?.draftId===state.draft?.id && state.polling)return;
    if(state.polling){window.clearInterval(state.polling);state.polling=null;setActiveJob(null);}
    if(!state.draft)return;
    const job=[...(state.data?.jobs||[])].reverse().find(j=>j.draft_id===state.draft.id && ['pending','running'].includes(j.status));
    if(!job)return;
    const label=job.kind==='generate'?'Model önerisi':job.kind==='model_review'?'Model eleştirisi':'Medya işi';
    pollJob(job.id||job.job_id,label,job.kind);
    renderJobProgress(job);
    setStage(job.kind==='generate'?'idea':'review');
  }
  function pollJob(jobId, label, kind) {
    if (state.polling) window.clearInterval(state.polling); setActiveJob({ id: jobId, label, kind, draftId:state.draft?.id }); let tries = 0; let waiting = false;
    state.polling = window.setInterval(async () => {
      if (waiting) return; waiting = true; tries += 1;
      try {
        const { body } = await api('/api/state'); state.data = body || state.data; renderJobLog();
        const job = (body.jobs || []).find((item) => String(item.id || item.job_id) === String(jobId)); const status = job?.status;
        if (status === 'succeeded') { window.clearInterval(state.polling); state.polling = null; setActiveJob(null); applyServerState(body, { preserveEditor: true }); if(kind==='generate') setStage('idea',{focus:true}); message(kind==='generate' ? 'Öneri hazır. Model metnini oku; taslağa almak onay değildir.' : `${label} tamamlandı. Yerel metin korunuyor; inceleme revizyonunu kontrol edin.`, 'success'); }
        else if (status === 'failed') { window.clearInterval(state.polling); state.polling = null; setActiveJob(null); message(`${label} başarısız oldu: ${jobFailure(job)}`, 'error'); }
        else if (tries > 150) { window.clearInterval(state.polling); state.polling = null; setActiveJob(null); message(`${label} durumu zaman aşımına uğradı. Yerel API durumunu yenileyin.`, 'error'); }
        else if (job) { renderJobProgress(job); }
      } catch (error) { window.clearInterval(state.polling); state.polling = null; setActiveJob(null); message(`İş durumu alınamadı: ${formatError(error)}`, 'error'); }
      finally { waiting = false; }
    }, 1200);
  }
  async function generate(rewrite = false) {
    if(state.data?.capabilities?.job_execution===false){message('Bu oturumda işçi kapalı; metnin korunuyor. Çalışan yerel başlatıcıdan yeniden aç. İş sıraya alınmadı.','error');return;}
    if (state.activeJob) { message('Bir iş sürerken yeni model üretimi başlatılamaz.', 'error'); return; }
    if (rewrite && !els.main.value.trim()) { message('Yeniden yazmak için önce bir ana metin girin.', 'error'); return; }
    if (!state.draft && !(await createDraft())) return;
    const saved = await saveDraft(); if (!saved) return;
    setBusy(true, 'Model işi başlatılıyor…');
    try { const instruction = `${rewrite ? 'Mevcut ana metni yeniden yaz; kaynak/deney ayrımını koru. ' : ''}${$('generate-instruction').value.trim()}`.trim(); const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/generate`, { method: 'POST', body: JSON.stringify({ revision: state.draft.revision, instruction }) }); message(`${rewrite ? 'Yeniden yazım' : 'Model'} işi sıraya alındı. Yer tutucu metin yok.`); pollJob(body.job_id, rewrite ? 'Model yeniden yazımı' : 'Model üretimi', 'generate'); }
    catch (error) { message(`Model işi başlatılamadı: ${formatError(error)}`, 'error'); }
    finally { setBusy(false); }
  }
  async function review() {
    const saved = await saveDraft(); if (!saved) return;
    setBusy(true, 'Kopya onayı kaydediliyor…');
    try { const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/approvals/copy`, { method: 'POST', body: JSON.stringify({ revision: state.draft.revision, fingerprint:state.draft.approval_fingerprint, confirmed:true }) }); state.draft = body; await refreshState({ quiet: true, preserveEditor: false }); populateDraft(state.draft); setStage('preview',{focus:true}); message('Metin onayı kaydedildi. Son yerel görünümü kontrol et; X’e hiçbir işlem gönderilmedi.', 'success'); }
    catch (error) { message(`Metin onayı kaydedilemedi: ${formatError(error)}`, 'error'); }
    finally { setBusy(false); }
  }
  async function loadHistory() {
    if (!state.draft) { message('Tarihçe için önce bir taslak seçin.', 'error'); return; }
    setBusy(true, 'Revizyon geçmişi yükleniyor…');
    try {
      const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/history?limit=20`);
      state.history = Array.isArray(body.history) ? body.history : []; renderHistory(); message('Revizyon geçmişi yüklendi.', 'success');
    } catch (error) { message(`Tarihçe yüklenemedi: ${formatError(error)}`, 'error'); }
    finally { setBusy(false); }
  }
  async function restoreRevision(restoreRevision) {
    if (!state.draft) { message('Geri alma için önce bir taslak seçin.', 'error'); return; }
    if (isDirty()) { message('Kaydedilmemiş editör değişiklikleri korunuyor. Geri dönmeden önce kaydedin.', 'error'); return; }
    if (restoreRevision === state.draft.revision) { message('Bu zaten geçerli sürümdür.'); return; }
    if(!window.confirm('Eski sürüm silinmez; bu sürümden yeni taslak oluşturulur. Mevcut metin onayı ve medya yeniden kontrol edilecek. Devam edilsin mi?'))return;
    setBusy(true, 'Seçilen sürümden yeni taslak oluşturuluyor…');
    try {
      const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/restore`, { method: 'POST', body: JSON.stringify({ revision: state.draft.revision, restore_revision: restoreRevision }) });
      state.draft = body; await refreshState({ quiet: true, preserveEditor: false }); populateDraft(state.draft); await loadHistory();
      message(`Eski sürümden yeni taslak oluşturuldu. Önceki kayıt silinmedi; metin onayı ve medya yeniden değerlendirilmeli.`, 'success');
    } catch (error) { message(`Geri alma yapılamadı: ${formatError(error)}`, 'error'); }
    finally { setBusy(false); }
  }
  async function makeMedia(kind) {
    if(state.data?.capabilities?.job_execution===false){message('Bu oturumda işçi kapalı; metnin korunuyor. Çalışan yerel başlatıcıdan yeniden aç. İş sıraya alınmadı.','error');return;}
    if (state.activeJob) { message('Bir iş sürerken yeni medya işi başlatılamaz.', 'error'); return; }
    if (!state.draft) { message('Medya için bir taslak oluşturun.', 'error'); return; }
    if (!els.main.value.trim()) { message('Medya üretimi için ana metin gerekli.', 'error'); return; }
    if (kind === 'video' && sceneValues().length < 3) { message('Video için en az 3 sahne metni girin.', 'error'); return; }
    if (kind === 'audio' && !els.consent.checked) { message('Ses üretimi için görünür Cloud TTS iznini işaretleyip kaydedin.', 'error'); return; }
    const saved = await saveDraft(); if (!saved) return;
    setBusy(true, `${kind} işi başlatılıyor…`);
    try { const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/media`, { method: 'POST', body: JSON.stringify({ revision: state.draft.revision, kind }) }); message(`${kind} işi sıraya alındı. Gerçek dosya oluşana kadar önizleme gösterilmez.`); pollJob(body.job_id, `${kind} üretimi`, 'media'); }
    catch (error) { message(`Medya işi başlatılamadı: ${formatError(error)}`, 'error'); }
    finally { setBusy(false); }
  }
  function kindForFile(file) {
    if (file.type.startsWith('image/')) return 'photo'; if (file.type.startsWith('audio/')) return 'audio'; if (file.type.startsWith('video/')) return 'video'; return '';
  }
  function readFileAsBase64(file) {
    return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onerror = () => reject(new Error('Dosya okunamadı.')); reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || ''); reader.readAsDataURL(file); });
  }
  function uploadJson(path, payload) {
    if(!canWrite())return Promise.reject(new Error('Studio bağlantısı yok veya salt-okunur; dosya gönderilmedi.'));
    if (!window.XMLHttpRequest) return api(path, { method: 'POST', body: JSON.stringify(payload) }).then(({ body }) => body);
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest(); request.open('POST', path); request.withCredentials = true; request.setRequestHeader('Accept', 'application/json'); request.setRequestHeader('Content-Type', 'application/json');
      request.upload.onprogress = (event) => { if (!event.lengthComputable) return; const percent = Math.min(100, Math.round(event.loaded / event.total * 100)); els.uploadProgress.hidden = false; els.uploadProgress.value = percent; setUploadNote(`Yükleniyor: %${percent}`); };
      request.onerror = () => reject(new Error('Yükleme bağlantısı kesildi.')); request.onload = () => { let body = {}; try { body = JSON.parse(request.responseText || '{}'); } catch (_) { reject(new Error('Sunucu geçerli JSON dönmedi.')); return; } if (request.status < 200 || request.status >= 300) { const error = new Error(body.error || `Sunucu isteği kabul etmedi (${request.status}).`); error.status = request.status; reject(error); return; } resolve(body); };
      request.send(JSON.stringify(payload));
    });
  }
  async function uploadAsset() {
    const file = els.uploadFile.files?.[0]; if (!canWrite()){setUploadNote('Studio bağlantısı yok veya salt-okunur; dosya gönderilmedi.','error');return;} if (!state.draft) { setUploadNote('Önce bir taslak oluşturun.', 'error'); return; }
    if (!file) { setUploadNote('Yüklenecek dosyayı seçin.', 'error'); return; }
    const kind = kindForFile(file); if (!kind) { setUploadNote('Yalnız fotoğraf, ses veya video dosyası yüklenebilir.', 'error'); return; }
    if (file.size > 6 * 1024 * 1024) { setUploadNote('Dosya çözüldüğünde en fazla 6 MiB olabilir.', 'error'); return; }
    const altText = els.uploadAlt.value.trim(); if (!altText) { setUploadNote('Yüklenen her medya dosyası için Türkçe alt metin zorunludur.', 'error'); return; }
    setBusy(true, 'Dosya hazırlanıyor…'); els.uploadButton.disabled = true; els.uploadProgress.hidden = false; els.uploadProgress.value = 0;
    try {
      setUploadNote('Dosya güvenli biçimde kodlanıyor…'); const dataBase64 = await readFileAsBase64(file); els.uploadProgress.value = 15;
      const body = await uploadJson(`/api/drafts/${encodeURIComponent(state.draft.id)}/upload`, { revision: state.draft.revision, kind, filename: file.name, data_base64: dataBase64, rights: els.uploadRights.value, alt_text: altText });
      els.uploadProgress.value = 100; setUploadNote('Dosya yüklendi.', 'success'); els.uploadFile.value = ''; await refreshState({ quiet: true, preserveEditor: true }); if (body.draft && !isDirty()) populateDraft(body.draft); else renderAssets(); message('Medya dosyası taslağa eklendi.', 'success');
    } catch (error) { setUploadNote(`Yükleme başarısız: ${formatError(error)}`, 'error'); }
    finally { els.uploadButton.disabled = !canWrite(); setBusy(false); }
  }
  async function transcribeAsset(asset) {
    if (state.activeJob) { message('Bir iş sürerken yeni altyazı işi başlatılamaz.', 'error'); return; }
    if (!state.draft || !isUploadedAsset(asset)) return;
    if (!els.vpsAsr.checked) { message('VPS ASR için açık izni işaretleyin.', 'error'); return; }
    const filename = assetName(asset); if (!filename) { message('Altyazı çıkarılacak dosya adı bulunamadı.', 'error'); return; }
    setBusy(true, 'Türkçe altyazı işi başlatılıyor…');
    try { const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/transcribe`, { method: 'POST', body: JSON.stringify({ revision: state.draft.revision, filename, vps_allowed: true }) }); message('Türkçe altyazı işi sıraya alındı. İşçi hatası varsa burada gösterilecek.'); pollJob(body.job_id, 'Türkçe altyazı', 'transcribe'); }
    catch (error) { message(`Türkçe altyazı başlatılamadı: ${formatError(error)}`, 'error'); }
    finally { setBusy(false); }
  }
  async function importItems(items, source) {
    setBusy(true, 'Kaynaklar aktarılıyor…');
    try { const { body } = await api('/api/import', { method: 'POST', body: JSON.stringify({ items, source, complete: false }) }); $('import-note').textContent = `${body.imported ?? 0} eklendi, ${body.duplicates ?? 0} tekrar, toplam ${body.total ?? '?'}.`; await refreshState({ quiet: true }); message('Kaynak aktarımı tamamlandı.', 'success'); }
    catch (error) { $('import-note').textContent = `Aktarım yapılamadı: ${formatError(error)}`; message(`Kaynak aktarımı yapılamadı: ${formatError(error)}`, 'error'); }
    finally { setBusy(false); }
  }
  function manualItem() {
    for(const id of ['manual-url','manual-title','manual-text']){$(id).setAttribute('aria-invalid','false');$(id+'-error').hidden=true;$(id+'-error').textContent='';}
    const invalid=(id,text)=>{const input=$(id);input.setAttribute('aria-invalid','true');const error=$(id+'-error');error.textContent=text;error.hidden=false;input.focus();throw new Error(text);};
    const url=$('manual-url').value.trim(), title=$('manual-title').value.trim(), text=$('manual-text').value.trim();
    if(!url.startsWith('https://'))invalid('manual-url','Kaynak URL’si https:// ile başlamalı.');
    if(!title)invalid('manual-title','Kaynağın başlığını veya kısa adını yaz.');
    if(!text)invalid('manual-text','Kaynak metni veya transkript ekle; bağlantı tek başına okunmaz.');
    return {kind:$('manual-kind').value,url,title,text,author:$('manual-author').value.trim()};
  }
  async function createManualSource() {
    let payload; try{payload=manualItem();}catch(error){$('import-note').textContent=formatError(error);return;}
    const attachIntent=Boolean(state.draft && els.attachToProject.checked);
    if(attachIntent && !window.confirm('Bu kaynak açık çalışmaya bağlanacak. Metin onayı ve son görünüm yeniden kontrol edilecek. Devam?'))return;
    if(attachIntent && isDirty() && !(await saveDraft()))return;
    setBusy(true,'Kaynak ekleniyor…');
    try{
      const duplicate=(state.data?.bookmarks||[]).some(item=>item.url===payload.url);
      const attach=Boolean(state.draft && els.attachToProject.checked);
      const {body:source}=await api('/api/sources/manual',{method:'POST',body:JSON.stringify(payload)});
      let attached=false;
      if(attach){const {body:draft}=await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/sources`,{method:'POST',body:JSON.stringify({revision:state.draft.revision,source_id:source.id})});state.draft=draft;attached=true;}
      for(const id of ['manual-url','manual-title','manual-text','manual-author'])$(id).value='';
      await refreshState({quiet:true,preserveEditor:false});
      if(attached){populateDraft(state.draft);setStage('source',{focus:true});}
      else if(duplicate){selectSource(source);setStage('source',{focus:true});}
      $('import-note').textContent=attached?'Kaynak eklendi. Metin onayı ve son görünüm yeniden kontrol edilmeli.':duplicate?'Bu bağlantı zaten kayıtlı; hiçbir çalışma değiştirilmedi. Mevcut çalışmayı aç veya ayrı çalışma oluştur.':'Kaynak eklendi. Yeni çalışma için kaynağı seç.';
      message(attached?'Kaynak bağlandı; önceki onaylar yeniden değerlendirilmeli.':duplicate?'Bu kaynak zaten kayıtlı; mevcut çalışmanı değiştirmedik.':'Kaynak eklendi. Otomatik içerik üretilmedi.','success');
    }catch(error){$('import-note').textContent=`Kaynak eklenemedi: ${formatError(error)}`;message(`Kaynak eklenemedi: ${formatError(error)}`,'error');}
    finally{setBusy(false);}
  }
  function openDrawer(open) { const siblings=[...document.body.children].filter(node=>node!==els.drawer && node.id!=='loading-overlay'); els.drawer.inert=!open; siblings.forEach(node=>{node.inert=open; node.setAttribute?.('aria-hidden',String(open));}); if($('evidence-backdrop'))$('evidence-backdrop').hidden=!open; els.drawer.classList.toggle('is-open', open); els.drawer.setAttribute('aria-hidden', String(!open)); els.drawerToggle.setAttribute('aria-expanded', String(open)); if (open) $('evidence-close').focus(); else els.drawerToggle.focus(); }
  els.drawer.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const items=[...els.drawer.querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter(el=>!el.disabled);if(!items.length)return;const first=items[0],last=items[items.length-1];if(event.shiftKey && document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}});
  async function exportZip() {
    if(!canWrite()){message('Studio bağlantısı yok veya salt-okunur; ZIP oluşturulmadı.','error');return;}
    if (!state.draft) { message('ZIP için önce bir taslak oluşturun.', 'error'); return; }
    if (isDirty()) { const saved = await saveDraft(); if (!saved) { message('ZIP indirilmedi: kaydedilmemiş metin sunucudaki paketten farklı olabilir.', 'error'); return; } }
    try { const { response } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/export`); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `karincaai-${state.draft.id}.zip`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 10000); message('İndirme tarayıcıya iletildi. Dosya konumu tarayıcı ayarına bağlı; D: için çıktı klasörüne kaydet seçeneğini kullanın.', 'success'); }
    catch (error) { message(`ZIP indirilemedi: ${formatError(error)}`, 'error'); }
  }
  async function savePackage(kind = 'interim') {
    if (!state.draft) { message('Önce taslak seçin.', 'error'); return; }
    const final = kind === 'final'; let request;
    if (final) {
      // Never auto-save here: a save would create a new revision and clear the approval being packaged.
      updateFinalGate(); if ($('final-save-package').disabled) return;
      request = { revision: state.draft.revision, fingerprint: state.previewFingerprint, confirmed: true };
    } else {
      if (isDirty() && !(await saveDraft())) return;
      request = { revision: state.draft.revision };
    }
    setBusy(true, final ? 'Metin onaylı yerel paket D: klasörüne kaydediliyor…' : 'Ara inceleme paketi kaydediliyor…');
    try {
      const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/save-package`, { method: 'POST', body: JSON.stringify(request) });
      const text = final ? `Metin onaylı yerel paket kaydedildi ve doğrulandı: ${body.path}` : `Ara inceleme paketi kaydedildi ve doğrulandı: ${body.path}`;
      message(text, 'success'); if (final) $('final-preview-status').textContent = text;
    } catch (error) { message(`Paket kaydedilemedi: ${formatError(error)}`, 'error'); if (final) $('final-preview-status').textContent = `Paket kaydedilemedi: ${formatError(error)}`; }
    finally { setBusy(false); }
  }
  async function selectPrimaryMedia() {
    if (!state.draft) return;
    const filename = els.primary.value;
    if (filename === safeText(state.draft.primary_media?.name)) return;
    const preserveEditor = isDirty();
    setBusy(true, 'Birincil medya kaydediliyor…');
    try {
      const { body } = await api(`/api/drafts/${encodeURIComponent(state.draft.id)}/primary-media`, { method: 'POST', body: JSON.stringify({ revision: state.draft.revision, filename }) });
      state.draft = body; state.primaryAsset = filename; await refreshState({ quiet: true, preserveEditor }); if (preserveEditor) { renderAssets(); syncDirty(); } else populateDraft(state.draft); message('Birincil medya kalıcı olarak kaydedildi; metin onayı yeniden değerlendirilmelidir.', 'success');
    } catch (error) {
      els.primary.value = safeText(state.draft.primary_media?.name); message(`Birincil medya kaydedilemedi: ${formatError(error)}`, 'error');
    } finally { setBusy(false); }
  }
  function loadRemoteDraft() { if (!state.remoteDraft) return; populateDraft(state.remoteDraft); message('Sunucudaki güncel sürüm yüklendi; yerel kaydedilmemiş değişiklikler silindi.', 'success'); }

  function isCandidate(draft) { return draft.candidate_status === 'review_candidate' || /İNCELEME ADAYI/i.test(draft.title || ''); }
  function showWorks(messageText='') { $('works-landing').hidden=false; $('app-shell').hidden=true; if(messageText) message(messageText); }
  function showFocus() { $('works-landing').hidden=true; $('app-shell').hidden=false; }
  function setStage(stage, {focus=false}={}) {
    const names={source:'Kaynağını seç',idea:pendingProposal()?'Öneriyi değerlendir':'İçerik hedefi',edit:'Gönderiyi düzenle',review:'Kontrol et ve onayla',preview:'Son görünüm ve yerel dosya'};
    const help={source:'Bağlantıyı ve okuduğun metni birlikte ekle. Kaynak tek başına doğruluk kanıtı değildir.',idea:pendingProposal()?'Bu modelin önerisi. Önce oku; taslağa almak onay veya paylaşım değildir.':'Okura ne fayda sağlayacağını yaz. Model yalnız bu çalışmanın kaynaklarını kullanır.',edit:'Paket adı X gönderisine eklenmez. Ana gönderi ve ilk yanıt ayrı metinlerdir.',review:'Kaynak ve iddia sınırını, varsa medyanın haklarını kontrol et. Metin onayı X’te paylaşım yapmaz.',preview:'Yerel benzetimi kontrol et. Gerçek X görünümü değildir; yalnız cihazına dosya kaydedilir.'};
    if (!Object.hasOwn(names,stage)) return;
    if (!state.selectedSource && stage!=='source') stage='source';
    if (!state.draft && ['edit','review','preview'].includes(stage)) stage='idea';
    if (pendingProposal() && ['edit','review','preview'].includes(stage)) { message('Önce model önerisini taslağa al veya kaldır. Bu metin henüz taslağın değil.','error'); stage='idea'; }
    $('app-shell').dataset.stage=stage;
    const title=$('stage-title'),note=$('stage-help');
    if(title) {title.textContent=names[stage];if(focus)title.focus();}
    if(note)note.textContent=help[stage];
    const label=$('work-name');if(label)label.textContent=state.draft?workTitle(state.draft):state.selectedSource?.title||'Yeni çalışma';
    const mobileStage=$('stage-mobile');
    if(mobileStage){mobileStage.value=stage; for(const option of mobileStage.options||[]) option.disabled=(!state.selectedSource && option.value!=='source') || (!state.draft && ['edit','review','preview'].includes(option.value)) || (Boolean(pendingProposal()) && ['edit','review','preview'].includes(option.value));}
    $('source-actions').hidden=!(stage==='source' && state.selectedSource && state.draft);
    document.querySelectorAll('[data-stage-target]').forEach(button=>{
      const active=button.dataset.stageTarget===stage;
      button.setAttribute('aria-current',active?'step':'false');
      button.disabled=(!state.selectedSource && stage!=='source') || (!state.draft && ['edit','review','preview'].includes(button.dataset.stageTarget)) || (Boolean(pendingProposal()) && ['edit','review','preview'].includes(button.dataset.stageTarget));
    });
    if(stage==='idea' && $('brief-panel'))$('brief-panel').open=!pendingProposal();
    if(stage==='review' && $('claim-panel'))$('claim-panel').open=true;
  }
  function workTitle(draft) { return safeText(draft.title).trim() || `${safeText(sourceById(draft.source_id)?.title).trim() || 'Seçili kaynak'} kaynağından çalışma`; }
  function workStatus(draft) { const proposal=(state.data?.proposals || []).some(p=>p.draft_id===draft.id && p.status==='pending'); return proposal ? 'Öneri hazır · kararını bekliyor' : hasValidApproval(draft) ? 'Metin onaylı · yayın değil' : draft.main ? 'İnsan incelemesi bekliyor' : 'İçerik hedefi bekliyor'; }
  function renderProjects() {
    const list=$('works-list'); list.replaceChildren();
    if($('start-first-work'))$('start-first-work').textContent=(state.data?.drafts||[]).length?'Yeni kaynakla çalışma başlat':'İlk kaynağını ekle';
    const query=$('draft-search').value.trim().toLocaleLowerCase('tr'); const filter=$('draft-status-filter').value || 'all';
    const drafts=(state.data?.drafts || []).filter(d=>(filter==='all' || filter==='pending' && !hasValidApproval(d) || filter==='approved' && hasValidApproval(d)) && (!query || [d.title,d.main,d.reader_brief?.benefit,sourceById(d.source_id)?.title,sourceById(d.source_id)?.author].join(' ').toLocaleLowerCase('tr').includes(query)));
    setStatusText('works-result-count',state.data ? `${drafts.length} çalışma bulundu` : '');
    if (!drafts.length) { const text=state.data ? (state.data.drafts?.length?'Bu aramada veya filtrede çalışma yok.':'Henüz çalışma yok. Bir kaynak ekleyerek başlayın.') : 'Yerel API’den çalışmalar bekleniyor.'; appendTo(list,text); return; }
    for(const draft of drafts) { const card=document.createElement('button'); card.type='button';card.className='project-card';card.dataset.draftId=draft.id;card.setAttribute('aria-current',String(state.draft?.id===draft.id));const title=document.createElement('strong');title.textContent=workTitle(draft);const meta=document.createElement('span');meta.textContent=workStatus(draft);card.append(title,meta);card.addEventListener('click',()=>selectDraft(draft)); list.append(card); }
  }
  function renderJobLog() { const list=$('job-log');list.replaceChildren(); const budget=state.data?.model_budget; if(budget) appendTo(list,`Model çağrısı ${budget.used}/${budget.limit} · toplam ${budget.total_duration_ms ?? '?'} ms. Token bilgisi gelmediyse bilinmiyor.`); for(const job of [...(state.data?.jobs || [])].reverse()) { const row=document.createElement('p');row.textContent=`${job.kind} · ${job.status} · ${job.duration_ms ?? '?'} ms · ${job.detail || ''}`;list.append(row); } }
  function renderReviewState() {
    renderProposals();
    const draft=state.draft; $('approval-status').textContent=draft ? `${draft.copy_approved ? 'İnsan metin onayı kayıtlı' : 'İnsan metin onayı bekliyor'} · Paket incelemesi ayrı · X renderı doğrulanmadı · Yayın kapalı` : 'İnceleme için bir taslak aç.';
    const list=$('evaluation-results');list.replaceChildren(); const review=draft?.reviews?.deterministic?.at(-1); const model=draft?.reviews?.model?.at(-1);
    if(review) renderEvaluation(review,'Kural kontrolü'); if(model) renderEvaluation(model,'Model eleştirisi');
  }
  function renderEvaluation(result,label) {
    if(!result)return;
    if(result.deterministic || result.model){for(const item of [result.deterministic?.at(-1),result.model?.at(-1)].filter(Boolean))renderEvaluation(item,item.kind==='deterministic'?'Kural kontrolü':'Model eleştirisi');return;}
    const list=$('evaluation-results'); const title=document.createElement('h4');title.textContent=`${label}${result.revision ? ' · REV. '+result.revision : ''}${result.revision && result.revision!==state.draft?.revision ? ' · ESKİ İNCELEME' : ''}`;list.append(title);
    if(result.summary)appendTo(list,result.summary);
    const names={reader_problem:'Okur problemi',benefit:'Somut fayda',angle:'Seçilmiş açı',provenance:'Kaynak ve özgün katkı',claims:'İddia kanıtları',copy:'Metin ve biçim',usefulness:'Ne işe yarıyor?',concreteness:'Somut ne öğretiyor?',depth:'Seçilen düzey için yeterli mi?',originality:'Özgün katkı açık mı?',cliches:'Klişe, boş vaat ve yapay açılıştan uzak mı?',consistency:'Kaynak ve brief ile tutarlı mı?'};
    for(const question of result.questions || []){const row=document.createElement('div');row.className='evaluation-item';row.textContent=`${question.ok ? '✓':'Düzeltme gerekli ·'} ${question.question || names[question.key] || question.key}${question.reason ? '\n'+question.reason : ''}`;list.append(row);}
    const action=result.next_action || result.recommended_action;
    if(action)appendTo(list,action==='return_to_brief'?'2 başarısız deneme: açı ve içerik hedefini yeniden belirle.':action==='revise'?'Düzeltme öneriliyor; geri bildirim alanına gerekçeni kaydet.':'İnsan incelemesine sunulabilir; onay verilmedi.');
  }
  function pendingProposal() { return [...(state.data?.proposals || [])].reverse().find(p=>p.draft_id===state.draft?.id && p.status==='pending'); }
  function renderProposals() {
    const proposal=pendingProposal();$('proposal-panel').hidden=!proposal;
    if(!proposal){updatePreview();return;}
    const stale=proposal.revision!==state.draft.revision;
    $('proposal-context').textContent=stale?'Bu öneri eski metne ait. Uygulanamaz; kaldırıp yeni öneri iste.':'Bu model önerisidir; henüz taslağa alınmadı ve insan onayı değildir.';
    $('proposal-original').textContent=[state.draft.title,state.draft.main,state.draft.reply].filter(Boolean).join('\n\n') || 'Önceki kayıtlı metin yok.';
    if($('proposal-package-title'))$('proposal-package-title').textContent=proposal.data.title || 'Paket adı önerilmedi';
    if($('proposal-main'))$('proposal-main').textContent=proposal.data.main || 'Ana gönderi önerilmedi';
    if($('proposal-reply'))$('proposal-reply').textContent=proposal.data.reply || 'İlk yanıt önerilmedi';
    $('proposal-copy').textContent=[proposal.data.title,proposal.data.main,proposal.data.reply,...(proposal.data.scene_texts || [])].filter(Boolean).join('\n\n');
    $('proposal-apply').disabled=stale;
    updatePreview();
  }
  async function resolveProposal(action) {
    const proposal=pendingProposal();if(!proposal)return;
    if(action==='apply' && isDirty()){message('Kaydedilmemiş metnin korunuyor. Önce kendi metnini kaydet; eski öneri yeni revizyona uygulanmaz.','error');return;}
    try {
      const {body}=await api(`/api/drafts/${encodeURIComponent(proposal.draft_id)}/proposals/${encodeURIComponent(proposal.id)}/${action}`,{method:'POST',body:JSON.stringify({revision:proposal.revision})});
      if(action==='apply' && state.draft?.id===proposal.draft_id){if(isDirty()){state.remoteDraft=body;els.reloadServer.hidden=false;}else populateDraft(body);}
      proposal.status=action==='apply'?'applied':'discarded';renderProposals();
      await refreshState({quiet:true,preserveEditor:true});
      message(action==='apply'?'Öneri taslağa alındı; eski metin tarihçede. İnsan onayı verilmedi.':'Öneri kaldırıldı; kayıtlı metnin değişmedi.','success');
      setStage(action==='apply'?'edit':state.draft?.main?'edit':'idea',{focus:true});
    } catch(error){message(`Öneri işlemi yapılamadı: ${formatError(error)} Metnin korunuyor; güncel sürümü incele.`,'error');}
  }
  $('proposal-apply').addEventListener('click',()=>resolveProposal('apply'));
  $('proposal-discard').addEventListener('click',()=>resolveProposal('discard'));
  $('proposal-preview')?.addEventListener('click',expandPreview);
  async function suggestAngles() {
    if(!state.draft && !(await createDraft())) return;
    const saved=await saveDraft();if(!saved)return;
    try { const {body}=await api(`/api/drafts/${encodeURIComponent(saved.id)}/angles`);state.angles=body.angles || [];const list=$('angle-options');list.replaceChildren();for(const angle of state.angles){const button=document.createElement('button');button.type='button';button.className='angle-option';const title=document.createElement('strong');title.textContent=angle.title;const note=document.createElement('span');note.textContent=angle.rationale;button.append(title,note);button.addEventListener('click',()=>{state.selectedAngle=angle;$('brief-angle').value=angle.title;$('brief-content-type').value=angle.content_type || $('brief-content-type').value;updatePreview();message('Açı seçildi; kaydet. İnsan kabulü verilmedi.');});list.append(button);} }
    catch(error){message(`Açılar alınamadı: ${formatError(error)}`,'error');}
  }
  async function editorialReview(useModel=false) {
    if(useModel && state.data?.capabilities?.job_execution===false){message('Bu oturumda işçi kapalı; metnin korunuyor. Çalışan yerel başlatıcıdan yeniden aç. İş sıraya alınmadı.','error');return;}
    if(state.activeJob){message('Süren iş bitmeden yeni değerlendirme başlatılmaz.','error');return;}
    const saved=await saveDraft();if(!saved)return;
    try{const {body}=await api(`/api/drafts/${encodeURIComponent(saved.id)}/${useModel ? 'model-review':'editorial-review'}`,{method:'POST',body:JSON.stringify({revision:saved.revision})});if(useModel) pollJob(body.job_id,'Model eleştirisi','model_review');else{await refreshState({quiet:true,preserveEditor:false});const reviews=await api(`/api/drafts/${encodeURIComponent(saved.id)}/reviews`);$('evaluation-results').replaceChildren();renderEvaluation(reviews.body,'Kaydedilen değerlendirmeler');message('Kural kontrolü kaydedildi. İnsan onayı verilmedi.');}}
    catch(error){message(`İnceleme yapılamadı: ${formatError(error)}`,'error');}
  }
  let previewOpener=null;
  function expandPreview(event){previewOpener=event?.currentTarget||document.activeElement;const dialog=$('review-dialog');if(!dialog.showModal){message('Tarayıcı büyük incelemeyi desteklemiyor.','error');return;}updatePreview();const original=document.querySelector('.x-preview');const copy=original.cloneNode(true);for(const el of copy.querySelectorAll('[id]'))el.removeAttribute('id');$('review-dialog-content').replaceChildren(copy);$('final-reviewed').checked=false;state.previewFingerprint=safeText(state.draft?.approval_fingerprint);updateFinalGate();if(!dialog.open)dialog.showModal();dialog.querySelector('.review-dialog-scroll')?.focus();}
  function openStep(id){const target=$(id); if(!target)return; if(target.tagName==='DETAILS')target.open=true;let parent=target.parentElement;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}target.scrollIntoView?.({behavior:'auto',block:'start'});target.focus?.();}
  $('draft-search').addEventListener('input',renderProjects);$('draft-status-filter').addEventListener('change',renderProjects);
  $('suggest-angles').addEventListener('click',suggestAngles);$('editorial-review').addEventListener('click',()=>editorialReview(false));$('model-review').addEventListener('click',()=>editorialReview(true));
  $('claim-original').addEventListener('click',()=>mapCurrentCopy(true));$('claim-source').addEventListener('click',()=>mapCurrentCopy(false));
  $('review-dialog').addEventListener('keydown',event=>{if(event.key!=='Tab')return;const items=[...$('review-dialog').querySelectorAll('button,a[href],input,select,textarea,video[controls],[tabindex="0"]')].filter(el=>!el.disabled);if(!items.length)return;const first=items[0],last=items[items.length-1];if(event.shiftKey && document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}});
  $('review-dialog').addEventListener('close',()=>{const opener=previewOpener;previewOpener=null;if(opener?.isConnected && !opener.closest('[hidden]') && getComputedStyle(opener).display!=='none')opener.focus();else $('stage-title').focus();});
  $('preview-expand').addEventListener('click',expandPreview);$('review-close').addEventListener('click',()=>{$('review-dialog').close();});
  $('final-reviewed').addEventListener('change',updateFinalGate);
  $('final-approve-copy').addEventListener('click',async()=>{updateFinalGate();if($('final-approve-copy').disabled)return;$('review-dialog').close();await review();if(hasValidApproval(state.draft))expandPreview();});
  $('final-save-package').addEventListener('click',async()=>{updateFinalGate();if($('final-save-package').disabled)return;await savePackage('final');});
  $('open-final-preview')?.addEventListener('click',event=>{updatePreview();expandPreview(event);});
  // Long-lived inputs stay in the DOM; only their task surface changes.
  if (els.form.insertBefore) {
    const copy=document.querySelector('.copy-editor');
    if(copy && $('model-tools'))els.form.insertBefore($('model-tools'),copy);
    if(copy && $('job-status'))els.form.insertBefore($('job-status'),copy);
    if(copy && $('job-elapsed'))els.form.insertBefore($('job-elapsed'),copy);
    if($('media-tools') && $('claim-panel'))els.form.insertBefore($('media-tools'),$('claim-panel'));
  }
  const appShell=$('app-shell');
  if(appShell.insertBefore && $('editor-message'))appShell.insertBefore($('editor-message'),$('source-pane'));
  if($('editorial-focus')?.insertBefore && $('evidence-toggle'))$('editorial-focus').insertBefore($('evidence-toggle'),$('draft-form'));
  document.querySelectorAll('[data-stage-target]').forEach(button=>button.addEventListener('click',()=>setStage(button.dataset.stageTarget,{focus:true})));
  $('stage-mobile')?.addEventListener('change',event=>setStage(event.target.value,{focus:true}));
  $('go-review')?.addEventListener('click',()=>{if(isDirty()){message('Önce metni kaydet; sonra kaynak ve onay kontrolüne geç.','error');return;}setStage('review',{focus:true});});
  $('go-preview')?.addEventListener('click',()=>setStage('preview',{focus:true}));
  $('show-works')?.addEventListener('click',()=>{if(isDirty()){message('Kaydedilmemiş değişikliklerin korunuyor. Önce kaydet.','error');return;}showWorks();window.history?.replaceState?.(null,'',window.location.pathname || '/');});
  $('start-first-work')?.addEventListener('click',()=>{state.selectedSource=null;populateDraft(null);$('attach-to-project').checked=false;showFocus();setStage('source',{focus:true});$('source-box').open=true;$('source-import-panel').open=true;$('manual-url').focus();});
  $('new-work-from-source')?.addEventListener('click',()=>{if(!state.selectedSource)return;if(isDirty()){message('Önce değişikliklerini kaydet veya geri al.','error');return;}populateDraft(null);setStage('idea',{focus:true});message('Yeni çalışma için okuru ve amacı belirle. Önceki çalışmaya dokunulmadı.');});
  $('new-source').addEventListener('click',()=>{if(isDirty()){message('Kaydedilmemiş metnin korunuyor. Önce kaydet; sonra yeni kaynak ekle.','error');return;}if(!$('works-landing').hidden){state.selectedSource=null;populateDraft(null);} $('attach-to-project').checked=false;showFocus();setStage('source',{focus:true});$('source-box').open=true;$('source-import-panel').open=true;$('manual-url').focus();});
  if(window.addEventListener)window.addEventListener('hashchange',()=>{
    if(isDirty()){message('Bağlantı değişti ama kaydedilmemiş metnin korunuyor. Önce kaydet; sonra taslağı listeden seç.','error');return;}
    const wanted=window.location.hash.replace(/^#draft=/,'');
    const draft=(state.data?.drafts || []).find(d=>d.id===wanted);
    if(draft)selectDraft(draft);
    else {state.selectedSource=null;populateDraft(null);message('Bağlantıdaki taslak bulunamadı. Başka taslak açılmadı; inceleme listesinden geçerli adayı seçin.','error');}
  });
  if(window.addEventListener)window.addEventListener('beforeunload',event=>{if(isDirty()){event.preventDefault();event.returnValue='';}});
  $('refresh-state').addEventListener('click', () => refreshState());
  els.sourceSearch.addEventListener('input', renderSources);
  els.sourceFilters.addEventListener('click', (event) => { const button = event.target.closest('[data-filter]'); if (!button) return; state.filter = button.dataset.filter; [...els.sourceFilters.querySelectorAll('button')].forEach((item) => item.classList.toggle('is-active', item === button)); renderSources(); });
  $('manual-import').addEventListener('click', createManualSource);
  $('json-import').addEventListener('change', async (event) => { const file = event.target.files[0]; if (!file) return; try { const parsed = JSON.parse(await file.text()); const items = Array.isArray(parsed) ? parsed : parsed.items; if (!Array.isArray(items) || !items.length) throw new Error('JSON içinde items dizisi bulunamadı.'); await importItems(items, 'browser_bookmarks'); } catch (error) { $('import-note').textContent = `JSON içe aktarılamadı: ${formatError(error)}`; } finally { event.target.value = ''; } });
  $('create-draft').addEventListener('click', createDraft); $('save-draft').addEventListener('click', () => saveDraft()); els.showHistory.addEventListener('click', loadHistory);
  els.generate.addEventListener('click', () => generate(false)); els.rewrite.addEventListener('click', () => generate(true));
  $('review-draft').addEventListener('click', event => { updatePreview(); expandPreview(event); message('Metni ve seçili medyayı inceleyin. Bu kontrol insan onayı vermez.'); }); $('approve-copy').addEventListener('click', review);
  $('add-scene').addEventListener('click', () => { renderScenes([...sceneValues(), '']); syncDirty(); });
  els.form.addEventListener('input', updatePreview); els.form.addEventListener('change', syncDirty);
  document.querySelectorAll('[data-media-kind]').forEach((button) => button.addEventListener('click', () => makeMedia(button.dataset.mediaKind)));
  els.uploadButton.addEventListener('click', uploadAsset); $('export-zip').addEventListener('click', exportZip); els.reloadServer.addEventListener('click', loadRemoteDraft);
  els.primary.addEventListener('change', selectPrimaryMedia);
  $('save-package').addEventListener('click', () => savePackage('interim'));
  els.drawerToggle.addEventListener('click', () => openDrawer(true)); $('evidence-close').addEventListener('click', () => openDrawer(false));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && els.drawer.classList.contains('is-open')) openDrawer(false); if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's' && state.draft) { event.preventDefault(); saveDraft(); } });
  if (window.__STUDIO_TEST__) window.__STUDIO_TEST__ = { state, els, api, editorPayload, isDirty, generate, saveDraft, uploadAsset, transcribeAsset, pollJob, applyServerState, selectSource, selectDraft, loadHistory, restoreRevision, selectPrimaryMedia, renderAssets, subtitleAsset, renderProposals, resolveProposal, renderProjectSources, createManualSource, manualItem, setStage, renderJobProgress, updateFinalGate };
  refreshState();
})();
