// Shared client-side logic for the intake form: choice rows, body-map/tag
// chips, the signature pad, and submission. Used by /profile, /kiosk/new and
// /kiosk/update — all three set `window.INTAKE_CONFIG` before loading this file.
//
// INTAKE_CONFIG shape:
//   profile: {...}            prefill data (server's `profile` dict, tojson'd)
//   kiosk: bool                kiosk-mode validation (name+email+consent+signature required)
//   submitUrl: string          where to POST the collected JSON
//   requirePhone: bool         require the phone field before submit (first-visit only)
//   successRedirect: string|null  where to navigate on success; null = reload in place
//   trackChangedSections: bool track which .tile sections were opened, and submit them

(function () {
  const CONFIG = window.INTAKE_CONFIG;
  const PROFILE = CONFIG.profile || {};
  const KIOSK = !!CONFIG.kiosk;

  const state = {
    focusZones: new Set(PROFILE.focus_zones || []),
    avoidZones: new Set(PROFILE.avoid_zones || []),
    problemTags: new Set(PROFILE.problem_tags || []),
    healthFlags: new Set(PROFILE.health_flags || []),
    changedSections: new Set(),
    choices: {
      has_health_problems: PROFILE.has_health_problems || '',
      pregnancy: PROFILE.pregnancy || '',
      blood_pressure: PROFILE.blood_pressure || '',
      exercise: PROFILE.exercise || '',
      recent_surgery: PROFILE.recent_surgery || '',
      pressure: PROFILE.pressure || '',
    },
  };
  window.__intakeState = state;

  // --- choices (yes/no & single-select rows) ---
  window.setChoice = function (group, value) {
    state.choices[group] = state.choices[group] === value ? '' : value;
    renderChoices(group);
    markChanged(group);
  };
  function renderChoices(group) {
    const row = document.querySelector(`.choice-row[data-group="${group}"]`);
    if (!row) return;
    row.querySelectorAll('.choice-btn').forEach((b) =>
      b.classList.toggle('active', b.dataset.value === state.choices[group]));
    const detail = document.getElementById('d-' + group);
    if (detail) detail.classList.toggle('open', state.choices[group] === 'yes');
  }
  Object.keys(state.choices).forEach(renderChoices);

  // --- body map + chips ---
  function renderZones() {
    document.querySelectorAll('.bm-zone, .zone-chip').forEach((el) => {
      const id = el.dataset.zone;
      el.classList.toggle('focus', state.focusZones.has(id));
      el.classList.toggle('avoid', state.avoidZones.has(id));
    });
  }
  window.cycleZone = function (id) {
    if (state.focusZones.has(id)) { state.focusZones.delete(id); state.avoidZones.add(id); }
    else if (state.avoidZones.has(id)) { state.avoidZones.delete(id); }
    else { state.focusZones.add(id); }
    renderZones();
    markChanged('bodymap');
  };
  document.querySelectorAll('.bm-zone').forEach((g) => {
    g.addEventListener('click', () => cycleZone(g.dataset.zone));
  });
  renderZones();

  // --- tag chips ---
  window.toggleTag = function (btn, type) {
    const id = btn.dataset.tag;
    const set = type === 'problem' ? state.problemTags : state.healthFlags;
    if (set.has(id)) { set.delete(id); btn.classList.remove('active'); }
    else { set.add(id); btn.classList.add('active'); }
    markChanged(type === 'problem' ? 'preferences' : 'health');
  };

  // --- section-change tracking (kiosk_update tiles) ---
  function markChanged(group) {
    if (!CONFIG.trackChangedSections) return;
    const sectionMap = {
      has_health_problems: 'health', pregnancy: 'health', blood_pressure: 'health',
      exercise: 'lifestyle', recent_surgery: 'lifestyle',
      pressure: 'preferences', preferences: 'preferences', health: 'health',
      lifestyle: 'lifestyle', bodymap: 'bodymap',
    };
    const section = sectionMap[group] || group;
    state.changedSections.add(section);
  }
  document.querySelectorAll('[data-section]').forEach((el) => {
    el.addEventListener('input', () => markChanged(el.dataset.section));
  });

  // --- tile toggling (kiosk_update only) ---
  document.querySelectorAll('.tile-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = document.getElementById(btn.dataset.target);
      if (!target) return;
      const open = target.classList.toggle('open');
      btn.classList.toggle('active', open);
      if (open) markChanged(btn.dataset.section);
    });
  });

  // --- tap-only counters (avoids the keyboard for "how many times/week"-style answers) ---
  document.querySelectorAll('.stepper').forEach((stepper) => {
    const target = document.getElementById(stepper.dataset.target);
    const valueEl = stepper.querySelector('.stepper-value');
    const suffixCs = stepper.dataset.suffixCs || '';
    const suffixEn = stepper.dataset.suffixEn || '';
    const max = parseInt(stepper.dataset.max || '14', 10);
    const existing = parseInt((target.value || '').match(/\d+/), 10);
    let n = Number.isNaN(existing) ? 0 : existing;
    function render() {
      valueEl.textContent = n;
      target.value = n > 0 ? `${n}${suffixCs} / ${n}${suffixEn}` : '';
    }
    render();
    stepper.querySelectorAll('.stepper-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        n = Math.max(0, Math.min(max, n + parseInt(btn.dataset.delta, 10)));
        render();
        markChanged('lifestyle');
      });
    });
  });

  // --- signature pad ---
  const sigPad = document.getElementById('sigPad');
  const sigCtx = sigPad.getContext('2d');
  let sigDirty = false, sigDrawing = false;
  function sizeSigPad() {
    const prev = sigDirty || PROFILE.signature_png ? sigPad.toDataURL() : null;
    const ratio = window.devicePixelRatio || 1;
    const w = sigPad.clientWidth, h = sigPad.clientHeight;
    sigPad.width = w * ratio; sigPad.height = h * ratio;
    sigCtx.scale(ratio, ratio);
    sigCtx.lineWidth = 2.2; sigCtx.lineCap = 'round'; sigCtx.strokeStyle = '#3a2b33';
    if (prev) { const img = new Image(); img.onload = () => sigCtx.drawImage(img, 0, 0, w, h); img.src = prev; }
  }
  sizeSigPad();
  if (PROFILE.signature_png) {
    const img = new Image();
    img.onload = () => { sigCtx.drawImage(img, 0, 0, sigPad.clientWidth, sigPad.clientHeight); sigDirty = true; };
    img.src = PROFILE.signature_png;
  }
  function sigPos(e) {
    const r = sigPad.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }
  sigPad.addEventListener('pointerdown', (e) => {
    sigDrawing = true; sigDirty = true;
    sigPad.setPointerCapture(e.pointerId);
    const [x, y] = sigPos(e);
    sigCtx.beginPath(); sigCtx.moveTo(x, y);
  });
  sigPad.addEventListener('pointermove', (e) => {
    if (!sigDrawing) return;
    const [x, y] = sigPos(e);
    sigCtx.lineTo(x, y); sigCtx.stroke();
  });
  ['pointerup', 'pointercancel'].forEach((ev) =>
    sigPad.addEventListener(ev, () => { sigDrawing = false; }));
  window.clearSignature = function () {
    sigCtx.clearRect(0, 0, sigPad.width, sigPad.height);
    sigDirty = false;
  };

  // --- submit ---
  function collect() {
    const data = {
      name: document.getElementById('f-name').value.trim(),
      phone: document.getElementById('f-phone').value.trim(),
      email: document.getElementById('f-email').value.trim(),
      has_health_problems: state.choices.has_health_problems,
      health_problems: state.choices.has_health_problems === 'yes'
        ? document.getElementById('f-health_problems').value.trim() : '',
      pregnancy: state.choices.pregnancy,
      blood_pressure: state.choices.blood_pressure,
      exercise: state.choices.exercise,
      exercise_detail: state.choices.exercise === 'yes'
        ? document.getElementById('f-exercise_detail').value.trim() : '',
      recent_surgery: state.choices.recent_surgery,
      surgery_detail: state.choices.recent_surgery === 'yes'
        ? document.getElementById('f-surgery_detail').value.trim() : '',
      pressure: state.choices.pressure || 'medium',
      problem_tags: [...state.problemTags],
      health_flags: [...state.healthFlags],
      focus_zones: [...state.focusZones],
      avoid_zones: [...state.avoidZones],
      oil_allergies: document.getElementById('f-allergies').value.trim(),
      note_original: document.getElementById('f-note').value.trim(),
      consent: document.getElementById('f-consent').checked,
      signature_png: sigDirty ? sigPad.toDataURL('image/png') : (PROFILE.signature_png || ''),
    };
    if (CONFIG.trackChangedSections) {
      data.changed_sections = [...state.changedSections];
    }
    return data;
  }

  function showError(msg) {
    const box = document.getElementById('formError');
    box.textContent = msg;
    box.style.display = msg ? 'block' : 'none';
  }

  window.submitForm = async function () {
    const data = collect();
    showError('');
    if (KIOSK && (!data.name || !data.email)) {
      showError(window.INTAKE_STRINGS.missing_name_email);
      return;
    }
    if (CONFIG.requirePhone && !data.phone) {
      showError(window.INTAKE_STRINGS.missing_phone);
      return;
    }
    if (KIOSK && (!data.consent || !data.signature_png)) {
      showError(window.INTAKE_STRINGS.consent_required);
      return;
    }
    const btn = document.getElementById('submitBtn');
    const status = document.getElementById('saveStatus');
    btn.disabled = true;
    status.textContent = '...';
    try {
      const r = await fetch(CONFIG.submitUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'body=' + encodeURIComponent(JSON.stringify(data)),
      });
      const payload = await r.json().catch(() => ({}));
      if (r.ok) {
        if (CONFIG.successRedirect) { window.location = payload.redirect || CONFIG.successRedirect; return; }
        status.textContent = '✓ ' + window.INTAKE_STRINGS.saved;
        setTimeout(() => { location.reload(); }, 1200);
      } else {
        status.textContent = '';
        showError((payload.error_message) || ('✗ Chyba / Error (' + r.status + ')'));
        btn.disabled = false;
      }
    } catch (e) {
      status.textContent = '';
      showError('✗ Offline');
      btn.disabled = false;
    }
  };
})();
