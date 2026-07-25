/* Peplink 設定管理コンソール — 画面ロジック
   サーバー(run.py)の /api/* を叩いて描画する。機器の認証情報はここには来ない。 */

'use strict';

const state = {
  meta: null,
  dashboard: null,
  audit: null,
  settings: null,
  pending: [],
  editorDevice: null,
  editorCategory: '',
  settingsFilter: { category: '', access: '', search: '' },
};

// --------------------------------------------------------------------- 通信

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try {
    body = await res.json();
  } catch (err) {
    throw new Error(`応答を解析できません (HTTP ${res.status})`);
  }
  if (!res.ok) throw new Error(body && body.error ? body.error : `HTTP ${res.status}`);
  return body;
}

const post = (path, payload) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });

// --------------------------------------------------------------------- 汎用

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

function banner(message, kind) {
  const node = $('#banner');
  if (!message) {
    node.hidden = true;
    return;
  }
  node.hidden = false;
  node.className = 'banner' + (kind ? ' ' + kind : '');
  node.textContent = message;
}

/** 値を人が読める文字列にする。 */
function fmtValue(value) {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'boolean') return value ? '有効' : '無効';
  if (Array.isArray(value)) return value.length ? value.join(', ') : null;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function valueCell(value, unit) {
  const shown = fmtValue(value);
  if (shown === null) return el('span', 'val empty', '(未設定)');
  const span = el('span', 'val', unit ? `${shown} ${unit}` : shown);
  return span;
}

function chip(kind, label) {
  const node = el('span', `chip ${kind}`);
  node.appendChild(el('span', 'dot'));
  node.appendChild(document.createTextNode(label));
  return node;
}

function signalBars(level) {
  const lv = Number.isFinite(level) ? Math.max(0, Math.min(4, level)) : 0;
  const node = el('span', `sig s${lv}`);
  for (let i = 0; i < 4; i += 1) node.appendChild(el('i'));
  return node;
}

const REF_BASE = '../../docs/';

function refLink(ref) {
  if (!ref) return document.createTextNode('—');
  const a = el('a', 'ref-link', ref.replace(/\.md$/, ''));
  a.href = REF_BASE + ref;
  a.target = '_blank';
  a.rel = 'noopener';
  return a;
}

// --------------------------------------------------------------------- 起動

async function init() {
  bindTabs();
  bindControls();
  try {
    state.meta = await api('/api/meta');
  } catch (err) {
    banner(`サーバーに接続できません: ${err.message}`);
    return;
  }
  const badge = $('#mode-badge');
  if (state.meta.demo) {
    badge.textContent = 'DEMO';
    badge.title = 'デモモード: 実機には接続していません';
  } else {
    badge.textContent = state.meta.allow_write ? 'LIVE / 変更可' : 'LIVE / 参照専用';
    badge.classList.add('live');
  }
  fillDeviceSelects();
  fillProfileSelect();
  await loadDashboard();
}

function bindTabs() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => {
        const on = t === tab;
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        $(`#screen-${t.dataset.screen}`).classList.toggle('active', on);
      });
      const screen = tab.dataset.screen;
      if (screen === 'audit') loadAudit();
      if (screen === 'settings') loadSettings();
      if (screen === 'editor') loadEditor();
    });
  });
}

function bindControls() {
  $('#btn-refresh').addEventListener('click', async () => {
    banner('機器から設定を取得しています…', 'info');
    try {
      await loadDashboard(true);
      banner('同期しました。', 'ok');
      setTimeout(() => banner(null), 2500);
    } catch (err) {
      banner(`同期に失敗しました: ${err.message}`);
    }
  });

  $('#audit-device').addEventListener('change', () => {
    // 機器を切り替えたら、その機器に割り当てられたプロファイルを選び直す
    syncProfileToDevice();
    loadAudit();
  });
  $('#audit-profile').addEventListener('change', loadAudit);
  $('#audit-only-issues').addEventListener('change', renderAudit);

  $('#settings-device').addEventListener('change', loadSettings);
  $('#settings-category').addEventListener('change', (e) => {
    state.settingsFilter.category = e.target.value;
    renderSettings();
  });
  $('#settings-access').addEventListener('change', (e) => {
    state.settingsFilter.access = e.target.value;
    renderSettings();
  });
  $('#settings-search').addEventListener('input', (e) => {
    state.settingsFilter.search = e.target.value.trim().toLowerCase();
    renderSettings();
  });

  $('#editor-device').addEventListener('change', loadEditor);
  $('#editor-category').addEventListener('change', (e) => {
    state.editorCategory = e.target.value;
    renderEditorForm();
  });
  $('#btn-apply').addEventListener('click', applyPending);
  $('#btn-clear').addEventListener('click', clearPending);
}

function fillDeviceSelects() {
  const devices = state.meta.devices || [];
  ['#audit-device', '#settings-device', '#editor-device'].forEach((sel) => {
    const node = $(sel);
    node.innerHTML = '';
    devices.forEach((dev) => {
      const opt = el('option', null, `${dev.label}${dev.model ? ' / ' + dev.model : ''}`);
      opt.value = dev.id;
      node.appendChild(opt);
    });
  });
}

function fillProfileSelect() {
  const node = $('#audit-profile');
  node.innerHTML = '';
  (state.meta.profiles || []).forEach((prof) => {
    const opt = el('option', null, prof.title);
    opt.value = prof.name;
    node.appendChild(opt);
  });
  syncProfileToDevice();
}

/** 選択中の機器に config.yaml で割り当てられたプロファイルを選択状態にする。 */
function syncProfileToDevice() {
  const deviceId = $('#audit-device').value;
  const dev = (state.meta.devices || []).find((d) => String(d.id) === String(deviceId));
  const node = $('#audit-profile');
  if (dev && dev.profile && [...node.options].some((o) => o.value === dev.profile)) {
    node.value = dev.profile;
  }
}

// --------------------------------------------------------------- ダッシュボード

async function loadDashboard(refresh) {
  const data = await api('/api/dashboard' + (refresh ? '?refresh=1' : ''));
  state.dashboard = data;
  renderDashboard();
  const latest = (data.devices || [])
    .map((d) => d.collected_at)
    .filter(Boolean)
    .sort()
    .pop();
  $('#sync-time').textContent = latest ? `最終同期 ${latest.replace('T', ' ').slice(0, 19)}` : '—';
}

function renderDashboard() {
  const devices = (state.dashboard && state.dashboard.devices) || [];
  const row = $('#stat-row');
  row.innerHTML = '';

  const online = devices.filter((d) => d.online).length;
  const wans = devices.flatMap((d) => d.wans || []);
  const connected = wans.filter((w) => w.status_led === 'green').length;
  const enabled = wans.filter((w) => w.enable).length;
  const tunnels = devices.flatMap((d) => (d.tunnels && d.tunnels.profiles) || []);
  const established = tunnels.filter((t) => t.status === 'CONNECTED').length;
  const failCount = devices.reduce(
    (acc, d) => acc + ((d.audit && d.audit.counts && d.audit.counts.fail) || 0),
    0,
  );
  const warnCount = devices.reduce(
    (acc, d) => acc + ((d.audit && d.audit.counts && d.audit.counts.warn) || 0),
    0,
  );
  const compliances = devices.map((d) => d.audit && d.audit.compliance).filter((v) => v !== null && v !== undefined);
  const avgCompliance = compliances.length
    ? Math.round(compliances.reduce((a, b) => a + b, 0) / compliances.length)
    : null;

  row.appendChild(
    statTile('オンライン機器', online, `/ ${devices.length}台`, online === devices.length ? ['ok', '✓ 全機応答あり'] : ['bad', '✕ 応答のない機器あり']),
  );
  row.appendChild(
    statTile('接続中WAN', connected, `/ ${enabled}回線`, connected === enabled ? ['ok', '✓ 全回線が接続'] : ['warn', '△ 待機・切断の回線あり']),
  );
  row.appendChild(
    statTile('SpeedFusion トンネル', established, `/ ${tunnels.length}本`, tunnels.length && established === tunnels.length ? ['ok', '✓ すべて確立'] : ['warn', '△ 未確立のトンネルあり']),
  );
  row.appendChild(
    statTile(
      '設定監査 適合率',
      avgCompliance === null ? '—' : avgCompliance,
      avgCompliance === null ? '' : '%',
      failCount ? ['bad', `✕ 要対応 ${failCount}件 / △ ${warnCount}件`] : warnCount ? ['warn', `△ 要確認 ${warnCount}件`] : ['ok', '✓ 不一致なし'],
    ),
  );

  const grid = $('#device-grid');
  grid.innerHTML = '';
  devices.forEach((dev) => grid.appendChild(deviceCard(dev)));
  if (!devices.length) {
    grid.appendChild(el('p', 'hint', '機器が登録されていません。config.yaml を確認してください。'));
  }
}

function statTile(label, value, suffix, delta) {
  const tile = el('div', 'stat');
  tile.appendChild(el('div', 'label', label));
  const v = el('div', 'value', value);
  if (suffix) v.appendChild(el('small', null, ' ' + suffix));
  tile.appendChild(v);
  if (delta) tile.appendChild(el('div', `delta ${delta[0]}`, delta[1]));
  return tile;
}

const LED_CHIP = {
  green: ['ok', '✓ 接続中'],
  yellow: ['warn', '△ 要確認'],
  red: ['bad', '✕ 切断'],
  flash: ['warn', '△ 接続処理中'],
  gray: ['idle', '無効'],
  empty: ['idle', '—'],
};

function deviceCard(dev) {
  const card = el('article', 'device');

  const head = el('div', 'device-head');
  const title = el('h3');
  title.appendChild(document.createTextNode(dev.label || dev.id));
  const meta = [dev.model, dev.firmware ? 'FW ' + dev.firmware : null, dev.host]
    .filter(Boolean)
    .join(' / ');
  title.appendChild(el('span', 'model', ' ' + meta));
  head.appendChild(title);
  head.appendChild(el('div', 'spacer'));
  if (!dev.online) {
    head.appendChild(chip('bad', '✕ オフライン'));
  } else if (dev.audit && dev.audit.error) {
    head.appendChild(chip('warn', '△ プロファイル不備'));
  } else if (dev.audit && dev.audit.counts) {
    const c = dev.audit.counts;
    if (c.fail) head.appendChild(chip('bad', `✕ 不一致 ${c.fail}件`));
    else if (c.warn) head.appendChild(chip('warn', `△ 要確認 ${c.warn}件`));
    else head.appendChild(chip('ok', `✓ 適合 ${dev.audit.compliance}%`));
  }
  card.appendChild(head);

  (dev.wans || []).forEach((wan) => {
    const row = el('div', 'wan-row');
    const main = el('div', 'wan-main');
    const isCell = wan.type === 'cellular' || wan.type === 'gobi';
    if (isCell) main.appendChild(signalBars(wan.level));
    const name = el('span', 'wan-name', wan.name);
    if (!isCell) name.style.paddingLeft = '26px';
    main.appendChild(name);
    const sub = [wan.carrier, wan.mobile_type, wan.ssid].filter(Boolean).join(' / ');
    main.appendChild(el('span', 'wan-carrier', sub || (wan.ip ? wan.ip : '')));
    const led = LED_CHIP[wan.status_led] || ['idle', wan.message || '—'];
    const prio = wan.enable && wan.priority ? ` (P${wan.priority})` : wan.enable ? '' : '';
    main.appendChild(chip(led[0], led[1] + prio));
    row.appendChild(main);

    const metrics = [];
    if (wan.rsrp !== null && wan.rsrp !== undefined) metrics.push(`RSRP ${wan.rsrp} dBm`);
    if (wan.rsrq !== null && wan.rsrq !== undefined) metrics.push(`RSRQ ${wan.rsrq} dB`);
    if (wan.sinr !== null && wan.sinr !== undefined) metrics.push(`SINR ${wan.sinr} dB`);
    if (wan.strength !== null && wan.strength !== undefined) metrics.push(`信号強度 ${wan.strength}`);
    if (wan.bands && wan.bands.length) metrics.push(`Band ${wan.bands.join('+')}`);
    if (wan.usage && wan.usage.limit) {
      metrics.push(`データ ${Math.round(wan.usage.usage / 1024)}/${Math.round(wan.usage.limit / 1024)} GB (${wan.usage.percent}%)`);
    }
    if (!metrics.length) metrics.push(wan.message || '—');
    const m = el('div', 'wan-metrics');
    m.textContent = metrics.join(' ・ ');
    row.appendChild(m);
    card.appendChild(row);
  });

  const foot = el('div', 'device-foot');
  const profiles = (dev.tunnels && dev.tunnels.profiles) || [];
  if (profiles.length) {
    profiles.forEach((p) => {
      const ok = p.status === 'CONNECTED';
      foot.appendChild(chip(ok ? 'ok' : 'warn', `${ok ? '✓' : '△'} ${p.name || 'トンネル'}: ${p.status}`));
    });
  } else {
    foot.appendChild(el('span', 'hint', 'SpeedFusion プロファイルなし'));
  }
  foot.appendChild(el('div', 'spacer'));
  const lans = (dev.lans || []).map((l) => `${l.name} ${l.ip}/${l.mask}`).join(' ・ ');
  if (lans) foot.appendChild(el('span', 'hint', 'LAN: ' + lans));
  if (dev.client_count !== null && dev.client_count !== undefined) {
    foot.appendChild(el('span', 'hint', `接続端末 ${dev.client_count}台`));
  }
  card.appendChild(foot);

  if (dev.errors && dev.errors.length) {
    const errs = el('div', 'device-errors');
    errs.textContent =
      '取得できなかった項目: ' + dev.errors.map((e) => `${e.endpoint} (${e.message})`).join(' / ');
    card.appendChild(errs);
  }
  return card;
}

// ------------------------------------------------------------------- 設定監査

const VERDICT = {
  ok: ['ok', '✓ 一致'],
  warn: ['warn', '△ 要確認'],
  fail: ['bad', '✕ 不一致'],
  unknown: ['idle', '? 判定不能'],
  manual: ['info', 'ⓘ 手動確認'],
};

async function loadAudit() {
  const device = $('#audit-device').value;
  const profile = $('#audit-profile').value;
  if (!device) return;
  try {
    state.audit = await api(`/api/audit?device=${encodeURIComponent(device)}&profile=${encodeURIComponent(profile)}`);
    banner(null);
  } catch (err) {
    banner(`監査を実行できません: ${err.message}`);
    return;
  }
  renderAudit();
}

function renderAudit() {
  const data = state.audit;
  const tbody = $('#audit-table tbody');
  tbody.innerHTML = '';
  if (!data) return;
  if (data.error) {
    banner(data.error);
    return;
  }

  const c = data.counts || {};
  $('#audit-meter').style.width = (data.compliance || 0) + '%';
  $('#audit-count').textContent = `適合 ${c.ok || 0} / ${data.judged || 0}項目`;

  const legend = $('#audit-legend');
  legend.innerHTML = '';
  ['ok', 'warn', 'fail', 'unknown', 'manual'].forEach((v) => {
    if (!c[v]) return;
    legend.appendChild(chip(VERDICT[v][0], `${VERDICT[v][1]} ${c[v]}`));
  });

  const onlyIssues = $('#audit-only-issues').checked;
  const rows = (data.results || []).filter((r) => !onlyIssues || ['fail', 'warn', 'unknown', 'manual'].includes(r.verdict));
  if (!rows.length) {
    const tr = el('tr');
    const td = el('td', 'empty-row', '該当する項目はありません。');
    td.colSpan = 6;
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  rows.forEach((r) => {
    const tr = el('tr');

    const tdV = el('td');
    const v = VERDICT[r.verdict] || VERDICT.unknown;
    tdV.appendChild(chip(v[0], v[1]));
    tr.appendChild(tdV);

    const tdName = el('td');
    tdName.appendChild(document.createTextNode(r.label));
    tdName.appendChild(el('span', 'path', r.key));
    if (r.reason) tdName.appendChild(el('span', 'reason', r.reason));
    tr.appendChild(tdName);

    const tdExpect = el('td');
    tdExpect.appendChild(el('span', 'val', r.expect_text || '—'));
    tr.appendChild(tdExpect);

    const tdActual = el('td');
    if (r.verdict === 'manual') tdActual.appendChild(el('span', 'val empty', '(APIで取得不可)'));
    else tdActual.appendChild(valueCell(r.actual));
    if (r.verdict !== 'ok' && r.message) tdActual.appendChild(el('span', 'reason', r.message));
    tr.appendChild(tdActual);

    const tdRef = el('td');
    tdRef.appendChild(refLink(r.ref));
    tr.appendChild(tdRef);

    const tdAct = el('td');
    if ((r.verdict === 'fail' || r.verdict === 'warn') && r.writable && r.fix_value !== undefined && r.fix_value !== null) {
      const btn = el('button', 'fixbtn', '期待値へ修正');
      btn.type = 'button';
      btn.addEventListener('click', () => stageFix(r));
      tdAct.appendChild(btn);
    }
    tr.appendChild(tdAct);
    tbody.appendChild(tr);
  });
}

async function stageFix(row) {
  const device = $('#audit-device').value;
  try {
    const res = await post('/api/pending', { device, changes: [{ key: row.key, value: row.fix_value }] });
    state.pending = res.pending || [];
    banner(`「${row.label}」の修正を保留に追加しました。④設定エディタで内容を確認して反映してください。`, 'info');
    renderPending();
  } catch (err) {
    banner(`保留に追加できません: ${err.message}`);
  }
}

// ------------------------------------------------------------------- 設定一覧

async function loadSettings() {
  const device = $('#settings-device').value;
  if (!device) return;
  try {
    state.settings = await api(`/api/settings?device=${encodeURIComponent(device)}`);
    banner(null);
  } catch (err) {
    banner(`設定一覧を取得できません: ${err.message}`);
    return;
  }
  const cats = [...new Set(state.settings.items.map((i) => i.category))];
  const sel = $('#settings-category');
  const keep = sel.value;
  sel.innerHTML = '';
  const all = el('option', null, 'すべて');
  all.value = '';
  sel.appendChild(all);
  cats.forEach((cat) => {
    const opt = el('option', null, cat);
    opt.value = cat;
    sel.appendChild(opt);
  });
  if (cats.includes(keep)) sel.value = keep;
  state.settingsFilter.category = sel.value;
  renderSettings();
}

function accessChip(item) {
  if (item.manual) return chip('info', 'API非対応');
  if (item.writable) return chip('ok', '読み書き可');
  if (item.readable) return chip('idle', '参照のみ');
  return chip('idle', '—');
}

function renderSettings() {
  const data = state.settings;
  const tbody = $('#settings-table tbody');
  tbody.innerHTML = '';
  if (!data) return;

  const f = state.settingsFilter;
  const rows = data.items.filter((item) => {
    if (f.category && item.category !== f.category) return false;
    if (f.access === 'rw' && !item.writable) return false;
    if (f.access === 'ro' && !(item.readable && !item.writable)) return false;
    if (f.access === 'manual' && !item.manual) return false;
    if (f.search) {
      const hay = `${item.label} ${item.key} ${item.note || ''}`.toLowerCase();
      if (!hay.includes(f.search)) return false;
    }
    return true;
  });

  const total = data.items.length;
  const rw = data.items.filter((i) => i.writable).length;
  const ro = data.items.filter((i) => i.readable && !i.writable).length;
  const mn = data.items.filter((i) => i.manual).length;
  $('#settings-count').textContent = `表示 ${rows.length} / 全 ${total}件(読み書き可 ${rw} ・ 参照のみ ${ro} ・ API非対応 ${mn})`;

  if (!rows.length) {
    const tr = el('tr');
    const td = el('td', 'empty-row', '条件に一致する項目はありません。');
    td.colSpan = 5;
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  rows.forEach((item) => {
    const tr = el('tr');
    const tdName = el('td');
    tdName.appendChild(document.createTextNode(item.label));
    tdName.appendChild(el('span', 'path', item.key));
    tr.appendChild(tdName);

    const tdVal = el('td');
    if (item.manual) tdVal.appendChild(el('span', 'val empty', '(APIで取得不可)'));
    else tdVal.appendChild(valueCell(item.value, item.unit));
    tr.appendChild(tdVal);

    const tdAcc = el('td');
    tdAcc.appendChild(accessChip(item));
    tr.appendChild(tdAcc);

    const tdNote = el('td');
    tdNote.appendChild(el('span', 'reason', item.manual || item.note || '—'));
    tr.appendChild(tdNote);

    const tdRef = el('td');
    tdRef.appendChild(refLink(item.ref));
    tr.appendChild(tdRef);
    tbody.appendChild(tr);
  });
}

// ----------------------------------------------------------------- エディタ

async function loadEditor() {
  const device = $('#editor-device').value;
  if (!device) return;
  state.editorDevice = device;
  try {
    const [settings, pending] = await Promise.all([
      api(`/api/settings?device=${encodeURIComponent(device)}`),
      api(`/api/pending?device=${encodeURIComponent(device)}`),
    ]);
    state.settings = settings;
    state.pending = pending.pending || [];
    banner(null);
  } catch (err) {
    banner(`設定を取得できません: ${err.message}`);
    return;
  }

  const writable = state.settings.items.filter((i) => i.writable);
  const cats = [...new Set(writable.map((i) => i.category))];
  const sel = $('#editor-category');
  const keep = sel.value;
  sel.innerHTML = '';
  cats.forEach((cat) => {
    const opt = el('option', null, cat);
    opt.value = cat;
    sel.appendChild(opt);
  });
  const all = el('option', null, 'すべて表示');
  all.value = '';
  sel.appendChild(all);
  // 一度に全項目を出すと長くなるため、既定では先頭の分類だけを表示する
  sel.value = cats.includes(keep) ? keep : cats[0] || '';
  state.editorCategory = sel.value;

  const canWrite = state.meta.allow_write && state.settings.device.can_write !== false;
  $('#editor-write-state').textContent = canWrite
    ? '変更可能な状態です'
    : '参照専用モードです(反映はできません)。--allow-write または config.yaml の allow_write: true が必要です';

  renderEditorForm();
  renderPending();
}

function renderEditorForm() {
  const form = $('#editor-form');
  form.innerHTML = '';
  if (!state.settings) return;
  const items = state.settings.items.filter(
    (i) => i.writable && (!state.editorCategory || i.category === state.editorCategory),
  );
  if (!items.length) {
    form.appendChild(el('p', 'hint', '変更可能な項目がありません。'));
    return;
  }

  const groups = new Map();
  items.forEach((item) => {
    if (!groups.has(item.category)) groups.set(item.category, []);
    groups.get(item.category).push(item);
  });

  groups.forEach((groupItems, category) => {
    const group = el('div', 'form-group');
    group.appendChild(el('h4', null, category));
    groupItems.forEach((item) => group.appendChild(editorField(item)));
    form.appendChild(group);
  });
}

function editorField(item) {
  const wrap = el('div', 'field');
  const id = 'f-' + item.key.replace(/[^\w]/g, '-');
  const label = el('label', null, item.label);
  label.htmlFor = id;
  wrap.appendChild(label);

  const pending = state.pending.find((p) => p.key === item.key);
  if (pending) wrap.classList.add('changed');
  const current = pending ? pending.value : item.value;

  let input;
  const options = item.options;
  if (Array.isArray(options) && options.length) {
    input = el('select');
    options.forEach((opt) => {
      const o = el('option', null, describeOption(opt, item));
      o.value = JSON.stringify(opt);
      input.appendChild(o);
    });
    // 現在値が選択肢に無い場合は追加して選べるようにする
    const currentJson = JSON.stringify(normalizeForCompare(current, options));
    if (![...input.options].some((o) => o.value === currentJson)) {
      const o = el('option', null, `現在値: ${fmtValue(current) ?? '(未設定)'}`);
      o.value = currentJson;
      input.appendChild(o);
    }
    input.value = currentJson;
  } else {
    input = el('input');
    input.type = 'text';
    input.value = current === null || current === undefined ? '' : Array.isArray(current) ? current.join(',') : String(current);
    input.placeholder = item.unit ? `数値(${item.unit})` : '';
  }
  input.id = id;
  input.addEventListener('change', () => {
    const raw = input.tagName === 'SELECT' ? JSON.parse(input.value) : parseInputValue(input.value, item);
    stageChange(item, raw);
  });
  wrap.appendChild(input);

  const helpParts = [];
  if (item.note) helpParts.push(item.note);
  if (item.ref) helpParts.push(`根拠: ${item.ref.replace(/\.md$/, '')}`);
  helpParts.push(`キー: ${item.key}`);
  wrap.appendChild(el('span', 'help', helpParts.join(' — ')));
  return wrap;
}

function normalizeForCompare(value, options) {
  if (value === undefined) return null;
  // 真偽値の選択肢に文字列が来た場合などを吸収
  if (options.every((o) => typeof o === 'boolean') && typeof value !== 'boolean') {
    if (value === 'true' || value === 1) return true;
    if (value === 'false' || value === 0) return false;
  }
  return value === undefined ? null : value;
}

function describeOption(opt, item) {
  if (typeof opt === 'boolean') return opt ? '有効' : '無効';
  if (Array.isArray(opt)) {
    if (!opt.length) return '何もしない';
    return opt.map((o) => (o === 'email' ? '通知のみ' : o === 'disconnect' ? '自動切断' : o)).join(' + ');
  }
  if (opt === '' && item.key.endsWith('sim_scheme')) return '両方(既定)';
  if (opt === '1' && item.key.endsWith('sim_scheme')) return 'SIM Aのみ';
  if (opt === '2' && item.key.endsWith('sim_scheme')) return 'SIM Bのみ';
  if (opt === 'alternate') return '定期的に交互';
  if (opt === 'remote_sim') return '遠隔SIM';
  if (opt === 'smartcheck') return 'SmartCheck(セルラー推奨)';
  if (opt === 'nslookup') return 'DNS Lookup';
  if (opt === 'ping') return 'PING';
  if (opt === 'http') return 'HTTP';
  return String(opt);
}

function parseInputValue(text, item) {
  const trimmed = text.trim();
  if (trimmed === '') return null;
  if (item.unit === 'kbps' || item.unit === 'MB' || item.unit === '秒' || item.unit === '回') {
    const num = Number(trimmed);
    if (!Number.isFinite(num)) throw new Error('数値を入力してください');
    return num;
  }
  if (trimmed.includes(',')) return trimmed.split(',').map((s) => s.trim()).filter(Boolean);
  if (/^-?\d+$/.test(trimmed)) return Number(trimmed);
  return trimmed;
}

async function stageChange(item, value) {
  const device = state.editorDevice;
  try {
    const res = await post('/api/pending', { device, changes: [{ key: item.key, value }] });
    state.pending = res.pending || [];
    banner(null);
    renderPending();
    renderEditorForm();
  } catch (err) {
    banner(`変更を保留できません: ${err.message}`);
  }
}

const IMPACT = {
  'healthcheck.': '影響: 低 — 通信断なし',
  'allowance.': '影響: 低 — 通信断なし',
  '.apn': '影響: 中 — 反映時に該当WANが再接続します',
  'sim_scheme': '影響: 中 — SIM切替に伴う再接続が発生します',
  'band_selection': '影響: 中 — モデムの再接続が発生します',
  'priority': '影響: 中 — 経路が切り替わり既存セッションが張り直されます',
  'routing_mode': '影響: 高 — 経路方式が変わります。設定誤りで通信不能になります',
  'mtu': '影響: 中 — 断片化の挙動が変わります',
};

function impactOf(key) {
  for (const [frag, text] of Object.entries(IMPACT)) {
    if (key.includes(frag)) return text;
  }
  return '影響: 反映時に該当WANの再接続が発生する場合があります';
}

function renderPending() {
  const list = $('#pending-list');
  list.innerHTML = '';
  const items = state.pending || [];
  $('#pending-count').textContent = `${items.length}件`;
  const canWrite = state.meta && state.meta.allow_write;
  $('#btn-apply').disabled = !items.length || !canWrite;

  if (!items.length) {
    list.appendChild(el('p', 'hint', '保留中の変更はありません。'));
    return;
  }
  const index = new Map((state.settings ? state.settings.items : []).map((i) => [i.key, i]));
  items.forEach((change) => {
    const item = index.get(change.key);
    const box = el('div', 'diff');
    box.appendChild(el('span', 'key', item ? item.label : change.key));
    box.appendChild(el('br'));
    box.appendChild(el('code', 'from', '− ' + (fmtValue(item ? item.value : null) ?? '(未設定)')));
    box.appendChild(el('br'));
    box.appendChild(el('code', 'to', '+ ' + (fmtValue(change.value) ?? '(未設定)')));
    box.appendChild(el('div', 'impact', impactOf(change.key)));
    list.appendChild(box);
  });
}

async function applyPending() {
  const device = state.editorDevice;
  const count = (state.pending || []).length;
  if (!count) return;
  if (!window.confirm(`${count}件の変更を機器に反映します。項目により該当WANの再接続が発生します。実行しますか?`)) {
    return;
  }
  $('#btn-apply').disabled = true;
  banner('機器へ反映しています…', 'info');
  try {
    const res = await post('/api/apply', { device });
    let msg = `反映しました(${(res.saved || []).join(' / ') || '変更なし'})。`;
    if (res.warning) msg += ` 機器からの警告: ${res.warning}`;
    banner(msg, 'ok');
    await loadEditor();
    await loadDashboard();
  } catch (err) {
    banner(`反映に失敗しました: ${err.message}`);
    renderPending();
  }
}

async function clearPending() {
  const device = state.editorDevice;
  try {
    await post('/api/pending/clear', { device });
    state.pending = [];
    renderPending();
    renderEditorForm();
    banner('保留中の変更を取り消しました。', 'ok');
    setTimeout(() => banner(null), 2000);
  } catch (err) {
    banner(`取り消しに失敗しました: ${err.message}`);
  }
}

document.addEventListener('DOMContentLoaded', init);
