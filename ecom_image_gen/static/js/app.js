// ═══════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════
let state = {
  prompts: null,       // {H1: {...}, H2: ..., ...}
  wsPath: '',          // output workspace path
  productImage: null,  // File object
  analyzeTaskId: null,
  generateTaskId: null,
  lightboxImages: [],  // [{src, label}]
  lightboxIdx: 0,
};

function getFormData() {
  const fd = new FormData();
  fd.set('sku', document.getElementById('sku').value || 'DEMO');
  if (state.productImage) fd.set('image', state.productImage);
  ['category','style','platform','language','model_region','model_gender',
   'model_age','model_skin','model_body','model_scene','shooting_style',
   'face_visible','generation_mode','additional_requirements'].forEach(id => {
    const el = document.getElementById(id);
    if (el) fd.set(id, el.value);
  });
  fd.set('gen_images', '0');
  fd.set('stop_at_stage', '2'); // 只跑到 Stage2 (营销策略), 用户确认后再生成提示词
  fd.set('force', document.getElementById('forceRegen').checked ? '1' : '0');
  return fd;
}

// ═══════════════════════════════════════════════
// Step 1: Analyze
// ═══════════════════════════════════════════════
async function doAnalyze() {
  const fileInput = document.getElementById('imageFile');
  if (!fileInput.files[0]) { alert('请选择产品图片'); return; }
  state.productImage = fileInput.files[0];

  const btn = document.getElementById('btnAnalyze');
  btn.disabled = true; btn.textContent = '分析中...';
  setLeftStatus('info', '提交分析任务...');

  const fd = getFormData();
  try {
    const r = await fetch('/api/generate', {method:'POST',body:fd});
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    state.analyzeTaskId = data.task_id;
    pollAnalyze();
  } catch(e) {
    setLeftStatus('err', e.message);
    btn.disabled = false; btn.textContent = '🔍 分析产品';
  }
}

function pollAnalyze() {
  const tid = state.analyzeTaskId;
  if (!tid) return;
  fetch('/api/status/' + tid).then(r => r.json()).then(data => {
    if (!data || data.error) { setLeftStatus('err', data?.error||'failed'); return; }

    // Progress
    let pct = 0, msg = '';
    const prog = data.progress || [];
    prog.forEach(p => {
      if (p.stage === 'stage1') { pct = 30; msg = '视觉分析中...'; }
      if (p.stage === 'stage2') { pct = 55; msg = '营销策略生成...'; }
      if (p.stage === 'stage3' && p.status === 'running') { pct = 75; msg = '生成提示词...'; }
      if (p.stage === 'stage3' && p.status === 'done') { pct = 90; msg = '提示词生成完成'; }
    });

    if (data.status === 'done') {
      pct = 100; msg = '✓ 分析完成';
      setLeftStatus('ok', msg);
      document.getElementById('btnAnalyze').disabled = false;
      document.getElementById('btnAnalyze').textContent = '🔍 重新分析';
      state.wsPath = data.result?.workspace || '';
      // Stage2 only: 显示营销策略, 等待用户确认
      if (data.result?.campaign) {
        renderCampaign(data.result.product, data.result.campaign);
        document.getElementById('btnGenPrompts').disabled = false;
      }
      // Full flow (backward compat): 显示提示词
      if (data.result?.prompts !== undefined) {
        state.prompts = data.result;
        renderPrompts(data.result);
        document.getElementById('btnGenerate').disabled = false;
      }
    } else if (data.status === 'failed') {
      setLeftStatus('err', data.error || '分析失败');
      document.getElementById('btnAnalyze').disabled = false;
      document.getElementById('btnAnalyze').textContent = '🔍 分析产品';
    } else {
      setLeftStatus('info', msg + ` (${pct}%)`);
      setTimeout(pollAnalyze, 2000);
    }
  }).catch(() => setTimeout(pollAnalyze, 3000));
}

function setLeftStatus(type, msg) {
  const el = document.getElementById('leftStatus');
  el.style.display = msg ? 'block' : 'none';
  el.className = 'status-msg ' + type;
  el.textContent = msg;
}

// ═══════════════════════════════════════════════
// Campaign display (营销策略确认)
// ═══════════════════════════════════════════════
function renderCampaign(product, campaign) {
  if (!campaign) return;
  const area = document.getElementById('campaignArea');
  const items = [
    ['core_selling_point', '核心卖点'],
    ['pain_points', '痛点'],
    ['benefits', '利益点'],
    ['usage_scenarios', '使用场景'],
    ['steps', '使用步骤'],
    ['comparison_points', '对比点'],
    ['trust_elements', '信任元素'],
  ];
  let html = '';
  if (product?.product_name) {
    html += `<div style="font-size:13px;font-weight:700;margin-bottom:8px">${escHtml(product.product_name)}</div>`;
  }
  items.forEach(([key, label]) => {
    const val = campaign[key];
    if (!val || (Array.isArray(val) && !val.length)) return;
    html += `<div class="campaign-card">`;
    html += `<div class="campaign-head" onclick="this.parentElement.classList.toggle('open')">${label}</div>`;
    html += `<div class="campaign-body">`;
    if (typeof val === 'string') {
      html += `<p>${escHtml(val)}</p>`;
    } else if (Array.isArray(val)) {
      html += '<ul>' + val.map(v => `<li>${escHtml(v)}</li>`).join('') + '</ul>';
    }
    html += `</div></div>`;
  });
  area.innerHTML = html || '<div class="empty-state">营销策略为空</div>';
}

// ═══════════════════════════════════════════════
// Step 2: Generate Prompts (from confirmed campaign)
// ═══════════════════════════════════════════════
let promptGenTaskId = null;
async function doGeneratePrompts() {
  if (!state.wsPath) { alert('请先分析产品'); return; }
  const btn = document.getElementById('btnGenPrompts');
  btn.disabled = true; btn.textContent = '生成中...';

  const fd = new FormData();
  fd.set('ws', state.wsPath);
  fd.set('generation_mode', document.getElementById('generation_mode').value);
  fd.set('force', document.getElementById('forceRegen').checked ? '1' : '0');
  ['category','style','platform','language','model_region','model_gender',
   'model_age','model_skin','model_body','model_scene','shooting_style',
   'face_visible','additional_requirements','sku'].forEach(id => {
    const el = document.getElementById(id);
    if (el) fd.set(id, el.value);
  });

  try {
    const r = await fetch('/api/generate-prompts', {method:'POST',body:fd});
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    promptGenTaskId = data.task_id;
    pollPromptGen();
  } catch(e) {
    btn.disabled = false; btn.textContent = '✓ 确认并生成提示词';
    document.getElementById('promptList').innerHTML =
      '<div class="status-msg err">' + e.message + '</div>';
  }
}

function pollPromptGen() {
  const tid = promptGenTaskId;
  if (!tid) return;
  fetch('/api/status/' + tid).then(r => r.json()).then(data => {
    if (!data || data.error) {
      document.getElementById('promptList').innerHTML =
        '<div class="status-msg err">' + (data?.error||'failed') + '</div>';
      resetPromptBtn();
      return;
    }
    if (data.status === 'done') {
      promptGenTaskId = null;
      resetPromptBtn();
      fetch('/api/prompts?ws=' + encodeURIComponent(state.wsPath))
        .then(r => r.json())
        .then(prompts => {
          state.prompts = prompts;
          renderPromptCards(prompts);
          document.getElementById('btnGenerate').disabled = false;
        });
    } else if (data.status === 'failed') {
      promptGenTaskId = null;
      resetPromptBtn();
      document.getElementById('promptList').innerHTML =
        '<div class="status-msg err">' + (data.error||'failed') + '</div>';
    } else {
      setTimeout(pollPromptGen, 2000);
    }
  }).catch(() => setTimeout(pollPromptGen, 3000));
}

function resetPromptBtn() {
  const btn = document.getElementById('btnGenPrompts');
  if (btn) { btn.disabled = false; btn.textContent = '✓ 确认并生成提示词'; }
}

// ═══════════════════════════════════════════════
// Step 3: Generate Images
// ═══════════════════════════════════════════════
async function doGenerateImages() {
  if (!state.prompts) { alert('请先分析产品'); return; }
  if (!state.productImage) { alert('请选择产品图片'); return; }

  const btn = document.getElementById('btnGenerate');
  btn.disabled = true; btn.textContent = '生成中...';

  document.getElementById('resultArea').innerHTML = `
    <div class="status-msg info">提交图片生成任务...</div>
    <div class="progress-bar"><div class="fill" id="genBar" style="width:5%"></div></div>
    <div id="genProgress"></div>`;

  const fd = new FormData();
  fd.set('image', state.productImage);
  fd.set('prompts', JSON.stringify(state.prompts));
  fd.set('generation_mode', document.getElementById('generation_mode').value);
  fd.set('sku', document.getElementById('sku').value || 'DEMO');
  fd.set('force', document.getElementById('forceRegen').checked ? '1' : '0');
  
  // Pass model_attrs for lookbook mode
  ['model_region','model_gender','model_age','model_skin','model_body',
   'model_scene','shooting_style','face_visible'].forEach(id => {
    const el = document.getElementById(id);
    if (el && el.value) fd.set(id, el.value);
  });

  try {
    const r = await fetch('/api/generate-images', {method:'POST',body:fd});
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    state.generateTaskId = data.task_id;
    pollGenerate();
  } catch(e) {
    document.getElementById('resultArea').innerHTML =
      '<div class="status-msg err">' + e.message + '</div>';
    btn.disabled = false; btn.textContent = '🖼️ 生成图片';
  }
}

let renderedImageCodes = {};

function pollGenerate() {
  const tid = state.generateTaskId;
  if (!tid) return;
  fetch('/api/status/' + tid).then(r => r.json()).then(data => {
    if (!data || data.error) {
      document.getElementById('resultArea').innerHTML =
        '<div class="status-msg err">' + (data?.error||'failed') + '</div>';
      document.getElementById('btnGenerate').disabled = false;
      document.getElementById('btnGenerate').textContent = '🖼️ 生成图片';
      return;
    }

    const prog = data.progress || [];

    // ── Incremental image display ──
    let wsPath = '';
    prog.forEach(p => { if (p.ws) wsPath = p.ws; });

    prog.forEach(p => {
      if (p.stage === 'image_done' && p.code && !renderedImageCodes[p.code]) {
        renderedImageCodes[p.code] = true;
        addImageToGrid(p.code, wsPath, p.status);
      }
    });

    // ── Progress bar ──
    let pct = 5, totalDone = 0;
    prog.forEach(p => {
      if (p.stage === 'images' && p.status === 'running') { pct = 10; if (p.ws) wsPath = p.ws; }
      if (p.stage === 'image_done') totalDone++;
    });
    
    const modeEl = document.getElementById('generation_mode');
    const totalEst = {full:14, hero:5, detail:9, lookbook:5}[modeEl?.value] || 14;
    if (totalDone > 0) pct = 10 + (totalDone / totalEst * 85);
    document.getElementById('genBar').style.width = Math.min(pct, 95) + '%';

    try {
      if (data.status === 'done') {
        stopPoll();
        document.getElementById('genBar').style.width = '100%';
        // 参考图追加
        const modeEl2 = document.getElementById('generation_mode');
        if (modeEl2?.value === 'lookbook' && wsPath && !renderedImageCodes['lookbook_ref']) {
          renderedImageCodes['lookbook_ref'] = true;
          addImageToGrid('lookbook_ref', wsPath, 'done');
        }
        if (modeEl2?.value !== 'lookbook' && wsPath && !renderedImageCodes['product_ref']) {
          renderedImageCodes['product_ref'] = true;
          addImageToGrid('product_ref', wsPath, 'done');
        }
        document.getElementById('resultFoot').style.display = 'block';
      } else if (data.status === 'failed') {
        stopPoll();
        document.getElementById('resultArea').innerHTML =
          '<div class="status-msg err">' + (data.error||'failed') + '</div>';
      } else {
        setTimeout(pollGenerate, 2000);
        return;
      }
    } catch(e) {
      console.error('pollGenerate error:', e);
      stopPoll();
    }
    
    document.getElementById('btnGenerate').disabled = false;
    document.getElementById('btnGenerate').textContent =
      data.status === 'done' ? '🖼️ 重新生成' : '🖼️ 生成图片';
  }).catch(() => setTimeout(pollGenerate, 3000));
}

function stopPoll() {
  state.generateTaskId = null;
  // 确保按钮一定还原
  const btn = document.getElementById('btnGenerate');
  if (btn) {
    btn.disabled = false;
    btn.textContent = '🖼️ 重新生成';
  }
}

function addImageToGrid(code, wsPath, status) {
  const area = document.getElementById('resultArea');
  if (!document.getElementById('imgGrid')) {
    const modeEl = document.getElementById('generation_mode');
    const modeLabel = {full:'全套', hero:'主图', detail:'详情', lookbook:'套图'}[modeEl?.value] || '';
    area.innerHTML = '<div class="status-msg info">🖼️ ' + modeLabel + '图片生成中...</div>' +
      '<div class="img-grid" id="imgGrid"></div>';
  }
  const grid = document.getElementById('imgGrid');
  if (!grid) return;

  const imgSrc = '/api/image?ws=' + encodeURIComponent(wsPath) + '&code=' + code;
  const div = document.createElement('div');
  const isRef = code === 'lookbook_ref' || code === 'product_ref';
  div.className = 'img-item' + (isRef ? ' ref-row' : '');
  div.onclick = function() {
    const idx = state.lightboxImages.findIndex(i => i.code === code);
    if (idx >= 0) openLightbox(idx);
  };
  const img = document.createElement('img');
  img.src = imgSrc;
  img.alt = code;
  img.loading = 'lazy';
  img.onerror = function() { this.parentElement.style.display = 'none'; };
  const label = document.createElement('span');
  label.className = 'label';
  const labelMap = {lookbook_ref: '📐参考图', product_ref: '🆔产品证'};
  label.textContent = (labelMap[code] || code) + (status === 'cached' ? ' ↻' : '');
  div.appendChild(img);
  div.appendChild(label);
  grid.appendChild(div);

  state.lightboxImages = state.lightboxImages || [];
  state.lightboxImages.push({code: code, src: imgSrc});
}

// Reset image tracking on new generate
const origDoGen = doGenerateImages;
doGenerateImages = function() {
  renderedImageCodes = {};
  state.lightboxImages = [];
  document.getElementById('resultArea').innerHTML = `
    <div class="status-msg info">提交图片生成任务...</div>
    <div class="progress-bar"><div class="fill" id="genBar" style="width:5%"></div></div>`;
  origDoGen();
};

// ═══════════════════════════════════════════════
// Prompt Cards
// ═══════════════════════════════════════════════

const MODULE_ORDER = ['H1','H2','H3','H4','H5','D1','D2','D3','D4','D5','D6','D7','D8','D9','M1','M2','M3','M4','M5'];

function renderPrompts(result) {
  if (result?.workspace) {
    fetch('/api/prompts?ws=' + encodeURIComponent(result.workspace))
      .then(r => r.json())
      .then(prompts => renderPromptCards(prompts))
      .catch(() => {
        renderPromptCards(result);
      });
  } else if (typeof result === 'object') {
    renderPromptCards(result);
  }
}

function renderPromptCards(prompts) {
  if (!prompts || !Object.keys(prompts).length) return;

  state.prompts = prompts;

  let html = '';
  const codes = MODULE_ORDER.filter(c => prompts[c]);
  codes.forEach(code => {
    const p = prompts[code];
    const promptText = p.prompt || p.prompt_text || '';
    const size = p.size || '';
    const objective = p.objective || '';
    html += `<div class="prompt-card" id="card-${code}">
      <div class="card-head" onclick="toggleCard('${code}')">
        <span class="code">${code}</span>
        <span class="meta">${size} · ${objective.slice(0,30)}</span>
      </div>
      <div class="card-body" id="body-${code}" ondblclick="editPrompt('${code}')">${escHtml(promptText)}</div>
      <div class="edit-actions" id="actions-${code}">
        <button class="save-btn" onclick="savePrompt('${code}')">✓ 确认</button>
        <button class="cancel-btn" onclick="cancelEdit('${code}')">↩ 还原</button>
      </div>
    </div>`;
  });

  document.getElementById('promptList').innerHTML = html || '<div class="empty-state">无提示词</div>';
  document.getElementById('btnGenerate').disabled = false;
}

function toggleCard(code) {
  document.getElementById('card-' + code).classList.toggle('open');
}

function collapseAll() {
  document.querySelectorAll('.prompt-card').forEach(c => c.classList.remove('open'));
}

function editPrompt(code) {
  const body = document.getElementById('body-' + code);
  const actions = document.getElementById('actions-' + code);
  if (body.classList.contains('editing')) return;

  body.dataset.original = body.textContent;
  body.contentEditable = 'true';
  body.classList.add('editing');
  body.focus();
  actions.classList.add('show');
}

function savePrompt(code) {
  const body = document.getElementById('body-' + code);
  const actions = document.getElementById('actions-' + code);
  body.contentEditable = 'false';
  body.classList.remove('editing');
  actions.classList.remove('show');

  if (state.prompts && state.prompts[code]) {
    state.prompts[code].prompt = body.textContent;
  }
}

function cancelEdit(code) {
  const body = document.getElementById('body-' + code);
  const actions = document.getElementById('actions-' + code);
  body.textContent = body.dataset.original || '';
  body.contentEditable = 'false';
  body.classList.remove('editing');
  actions.classList.remove('show');
}

function escHtml(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ═══════════════════════════════════════════════
// Results & Lightbox
// ═══════════════════════════════════════════════
function renderResults(result) {
  const ws = result?.workspace || state.wsPath || '';
  const possibleCodes = MODULE_ORDER.filter(c => state.prompts && state.prompts[c]);
  const modeEl = document.getElementById('generation_mode');
  if (modeEl?.value === 'lookbook') {
    possibleCodes.unshift('lookbook_ref');
  } else {
    possibleCodes.unshift('product_ref');
  }
  const labelMap = {lookbook_ref: '📐参考图', product_ref: '🆔产品证'};
  state.lightboxImages = possibleCodes.map(code => ({
    src: '/api/image?ws=' + encodeURIComponent(ws) + '&code=' + code,
    label: labelMap[code] || code
  }));

  let html = '<div class="status-msg ok">✓ 生成完成</div>';
  if (result?.images) {
    html += '<div style="font-size:11px;color:#666;margin-bottom:8px">' +
      '新增 ' + (result.images.generated||0) +
      ' / 跳过 ' + (result.images.skipped||0) +
      ' / 失败 ' + (result.images.failed||0) + '</div>';
  }

  html += '<div class="img-grid" id="imgGrid">';
  state.lightboxImages.forEach((img, i) => {
    html += `<div class="img-item" onclick="openLightbox(${i})">
      <img src="${img.src}" alt="${img.label}" loading="lazy" onerror="this.parentElement.style.display='none'">
      <span class="label">${img.label}</span>
    </div>`;
  });
  html += '</div>';

  document.getElementById('resultArea').innerHTML = html;
  document.getElementById('resultFoot').style.display = 'block';
}

function openLightbox(idx) {
  state.lightboxIdx = idx;
  const img = state.lightboxImages[idx];
  if (!img) return;
  document.getElementById('lightboxImg').src = img.src;
  document.getElementById('lightbox').classList.add('show');
}

function closeLightbox() {
  document.getElementById('lightbox').classList.remove('show');
}

function navLightbox(dir) {
  const idx = state.lightboxIdx + dir;
  if (idx >= 0 && idx < state.lightboxImages.length) {
    openLightbox(idx);
  }
}

document.addEventListener('keydown', e => {
  if (document.getElementById('lightbox').classList.contains('show')) {
    if (e.key === 'Escape') closeLightbox();
    if (e.key === 'ArrowLeft') navLightbox(-1);
    if (e.key === 'ArrowRight') navLightbox(1);
  }
});

function downloadAll() {
  state.lightboxImages.forEach(img => {
    const a = document.createElement('a');
    a.href = img.src; a.download = img.label + '.png'; a.click();
  });
}

// Handle file input
document.getElementById('imageFile').addEventListener('change', function() {
  state.productImage = this.files[0];
});
