/* WorkBuddy 任务看板 —— 前端（只读） */
(() => {
  'use strict';

  // 配置来自 config.js（不入版本库）。endpoint 默认取当前站点域名。
  const RUNTIME = window.WB_CONFIG || {};
  const PUBLIC_CONFIG = {
    endpoint: RUNTIME.endpoint || location.origin,
    oauthRelayBaseUrl: RUNTIME.oauthRelayBaseUrl || 'https://www.workbuddy.cn/v2/as/genie-baas/oauth',
    publishableKey: RUNTIME.publishableKey || '',
  };

  const ONLINE_WINDOW_MS = 3 * 60 * 1000;
  const REFRESH_MS = 60 * 1000;

  const STATUS_META = {
    completed: { label: '已完成', cls: 'badge-completed' },
    archived: { label: '已归档', cls: 'badge-archived' },
    pending: { label: '待处理', cls: 'badge-pending' },
    scheduled: { label: '定时中', cls: 'badge-scheduled' },
    paused: { label: '已暂停', cls: 'badge-paused' },
    failed: { label: '失败', cls: 'badge-failed' },
  };

  const FILTERS = [
    { key: 'all', label: '全部' },
    { key: 'completed', label: '已完成' },
    { key: 'automation', label: '定时任务' },
  ];

  const $ = (id) => document.getElementById(id);
  const state = { session: null, host: null, tasks: [], filter: 'all', query: '', timer: null, busy: false };
  let cloud = null;

  /* ---------------- 工具 ---------------- */

  const pad = (n) => String(n).padStart(2, '0');

  function fmtClock(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtFull(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtRelative(iso) {
    if (!iso) return '—';
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return '—';
    const diff = Date.now() - t;
    const min = Math.floor(diff / 60000);
    if (min < 1) return '刚刚';
    if (min < 60) return `${min} 分钟前`;
    const h = Math.floor(min / 60);
    if (h < 24) return `${h} 小时前`;
    const d = Math.floor(h / 24);
    if (d === 1) return '昨天';
    if (d < 30) return `${d} 天前`;
    const mo = Math.floor(d / 30);
    if (mo < 12) return `${mo} 个月前`;
    return `${Math.floor(mo / 12)} 年前`;
  }

  function dayBucket(iso) {
    if (!iso) return '更早';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '更早';
    const now = new Date();
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const t = d.getTime();
    if (t >= startToday) return '今天';
    if (t >= startToday - 86400000) return '昨天';
    if (t >= startToday - 6 * 86400000) return '本周';
    if (t >= startToday - 29 * 86400000) return '本月';
    return '更早';
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function showView(id) {
    for (const v of ['view-login', 'view-bind', 'view-app']) {
      const node = $(v);
      if (node) node.hidden = v !== id;
    }
    window.scrollTo({ top: 0 });
  }

  function setError(id, message) {
    const node = $(id);
    if (!node) return;
    if (!message) {
      node.hidden = true;
      node.textContent = '';
      return;
    }
    node.hidden = false;
    node.textContent = message;
  }

  /* ---------------- 轻提示 ---------------- */

  let toastTimer = null;

  function showToast(message) {
    const node = $('toast');
    if (!node) return;
    node.textContent = message;
    node.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      node.hidden = true;
    }, 3200);
  }

  /* ---------------- 鉴权 ---------------- */

  async function restoreAuth() {
    const url = new URL(location.href);
    if (!url.searchParams.has('code') && !url.searchParams.has('error')) {
      const { data } = await cloud.auth.getSession();
      state.session = data || null;
      return;
    }

    const provider = url.searchParams.get('provider');
    if (provider !== 'workbuddy') {
      setError('login-error', '登录回调缺少有效的登录方式，请重新登录。');
      showView('view-login');
      return;
    }

    const { data: session, error } = await cloud.auth.handleWorkBuddyCallback();
    if (error) {
      setError('login-error', error.message || '登录失败，请重试。');
      showView('view-login');
      return;
    }
    if (session) {
      for (const key of ['code', 'state', 'provider']) url.searchParams.delete(key);
      history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
      state.session = session;
      return;
    }
    const { data } = await cloud.auth.getSession();
    state.session = data || null;
  }

  async function signInWithWorkBuddy() {
    setError('login-error', '');
    const back = new URL(location.href);
    back.pathname = '/';
    for (const key of ['code', 'state', 'provider']) back.searchParams.delete(key);
    const { data, error } = await cloud.auth.signInWithOAuth({
      provider: 'workbuddy',
      redirectTo: back.toString(),
    });
    if (error) {
      setError('login-error', error.message || '暂时无法发起登录，请稍后重试。');
      return;
    }
    location.assign(data.url);
  }

  let pendingOtp = null;

  async function sendCode() {
    const email = $('input-email').value.trim();
    setError('login-error', '');
    if (!email || !email.includes('@')) {
      setError('login-error', '请输入有效的邮箱地址。');
      return;
    }
    const btn = $('btn-send-code');
    btn.disabled = true;
    btn.textContent = '发送中…';
    const { data, error } = await cloud.auth.sendOtp({ email });
    btn.disabled = false;
    btn.textContent = '重新获取';
    if (error) {
      setError('login-error', error.message || '验证码发送失败。');
      return;
    }
    pendingOtp = { email, verificationId: data.verificationId, isExistingUser: data.isExistingUser };
    $('field-code').hidden = false;
    $('input-code').focus();
    $('login-hint').textContent = `验证码已发送至 ${email}，10 分钟内有效。`;
  }

  async function submitEmailLogin(event) {
    event.preventDefault();
    setError('login-error', '');
    const email = $('input-email').value.trim();
    const code = $('input-code').value.trim();
    if (!pendingOtp || pendingOtp.email !== email) {
      setError('login-error', '请先获取该邮箱的验证码。');
      return;
    }
    if (!code) {
      setError('login-error', '请输入收到的验证码。');
      return;
    }
    const btn = $('btn-email-login');
    btn.disabled = true;
    btn.textContent = '登录中…';
    const { data, error } = await cloud.auth.verifyOtp({
      email: pendingOtp.email,
      verificationId: pendingOtp.verificationId,
      isExistingUser: pendingOtp.isExistingUser,
      token: code,
    });
    btn.disabled = false;
    btn.textContent = '登录';
    if (error) {
      setError('login-error', error.message || '验证码校验失败。');
      return;
    }
    pendingOtp = null;
    state.session = data;
    await afterLogin();
  }

  async function signOut() {
    try {
      await cloud.auth.signOut();
    } catch {
      /* 忽略 */
    }
    state.session = null;
    state.host = null;
    state.tasks = [];
    if (state.timer) clearInterval(state.timer);
    $('login-hint').textContent = '推荐使用与电脑端相同的账号，登录后即可查看你的任务。';
    $('field-code').hidden = true;
    showView('view-login');
  }

  /* ---------------- 数据 ---------------- */

  async function loadData() {
    const hostRes = await cloud.database.from('wb_host').select('*').limit(1);
    if (hostRes.error) throw new Error(hostRes.error.message || '读取主机状态失败');
    state.host = (hostRes.data && hostRes.data[0]) || null;

    if (!state.host) {
      state.tasks = [];
      return false;
    }

    const taskRes = await cloud.database
      .from('wb_task')
      .select('task_uid,kind,title,status,workspace,model,mode,msg_count,preview,created_at,updated_at,last_activity_at')
      .order('updated_at', { ascending: false })
      .limit(400);
    if (taskRes.error) throw new Error(taskRes.error.message || '读取任务列表失败');
    state.tasks = taskRes.data || [];
    return true;
  }

  async function refresh({ silent } = {}) {
    if (state.busy) return;
    state.busy = true;
    const btn = $('btn-refresh');
    if (!silent && btn) btn.classList.add('spin');
    try {
      const bound = await loadData();
      if (!bound) {
        showView('view-bind');
      } else {
        render();
        showView('view-app');
      }
    } catch (e) {
      const msg = e.message || '数据加载失败，请稍后重试。';
      // 主面板可见时错误写进绑定视图用户看不到，改用轻提示
      if ($('view-app') && !$('view-app').hidden) showToast(msg);
      else setError('bind-error', msg);
    } finally {
      state.busy = false;
      if (btn) btn.classList.remove('spin');
    }
  }

  async function submitBind(event) {
    event.preventDefault();
    setError('bind-error', '');
    const code = $('input-bind').value.trim().toUpperCase();
    if (code.length < 6) {
      setError('bind-error', '绑定码格式不正确。');
      return;
    }
    const { data, error } = await cloud.database.rpc('wb_bind', { p_code: code });
    if (error) {
      setError('bind-error', error.message || '绑定失败，请重试。');
      return;
    }
    if (!data || data.ok !== true) {
      const map = {
        invalid_code: '绑定码不存在或已使用，请检查后重试。',
        already_bound: '这台电脑已绑定到其他账号，无法重复绑定。',
        not_signed_in: '登录状态已失效，请重新登录。',
      };
      setError('bind-error', map[data && data.error] || '绑定失败，请重试。');
      return;
    }
    $('input-bind').value = '';
    await refresh();
  }

  /* ---------------- 渲染 ---------------- */

  function renderHostStatus() {
    const host = state.host;
    const pill = $('host-pill');
    // 数据只在电脑端点「任务同步」按钮时上传，因此「电脑在线」不是有效判据，统一以数据时点表述
    if (pill) pill.hidden = true;

    if (!host) {
      $('sync-time').textContent = '尚无同步记录';
      return;
    }

    const lastSeen = host.last_seen ? new Date(host.last_seen).getTime() : 0;
    const fresh = lastSeen && Date.now() - lastSeen < 2 * 60 * 1000;
    $('sync-time').textContent = fresh
      ? `刚刚同步 · ${fmtClock(host.last_seen)}`
      : `数据截至 ${fmtClock(host.last_seen)}（${fmtRelative(host.last_seen)}）`;
    $('host-name').textContent = host.host_name || '任务看板';
  }

  function renderStats() {
    const sessions = state.tasks.filter((t) => t.kind === 'session');
    const done = sessions.filter((t) => t.status === 'completed').length;
    const autos = state.tasks.filter((t) => t.kind === 'automation').length;

    const box = $('stats');
    box.textContent = '';
    const items = [
      { n: done, label: '已完成' },
      { n: autos, label: '定时任务' },
    ];
    for (const it of items) {
      const node = el('div', 'stat');
      node.appendChild(el('div', 'stat-num', it.n));
      node.appendChild(el('div', 'stat-label', it.label));
      box.appendChild(node);
    }
  }

  function renderChips() {
    const box = $('chips');
    box.textContent = '';
    for (const f of FILTERS) {
      const btn = el('button', `chip${state.filter === f.key ? ' active' : ''}`, f.label);
      btn.type = 'button';
      btn.addEventListener('click', () => {
        state.filter = f.key;
        renderChips();
        renderList();
      });
      box.appendChild(btn);
    }
  }

  function matches(task) {
    if (state.filter === 'completed' && task.status !== 'completed') return false;
    if (state.filter === 'automation' && task.kind !== 'automation') return false;
    if (state.query) {
      const q = state.query.toLowerCase();
      const hay = `${task.title || ''} ${task.workspace || ''} ${task.preview || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  function buildCard(task) {
    const card = el('article', 'card');
    card.addEventListener('click', () => openSheet(task));

    const head = el('div', 'card-head');
    head.appendChild(el('div', 'card-title', task.title || '(未命名任务)'));
    if (task.status !== 'working') {
      const meta = STATUS_META[task.status] || { label: task.status || '未知', cls: 'badge-pending' };
      head.appendChild(el('span', `badge ${meta.cls}`, meta.label));
    }
    card.appendChild(head);

    if (task.preview) card.appendChild(el('div', 'card-preview', task.preview));

    const metaRow = el('div', 'card-meta');
    metaRow.appendChild(el('span', null, fmtRelative(task.updated_at || task.last_activity_at)));
    if (task.workspace) {
      metaRow.appendChild(el('span', 'dot'));
      metaRow.appendChild(el('span', null, task.workspace));
    }
    if (task.msg_count > 0) {
      metaRow.appendChild(el('span', 'dot'));
      metaRow.appendChild(el('span', null, `${task.msg_count} 条对话`));
    }
    if (task.model) {
      metaRow.appendChild(el('span', 'dot'));
      metaRow.appendChild(el('span', 'meta-tag', String(task.model).replace('deepseek-', '')));
    }
    card.appendChild(metaRow);
    return card;
  }

  function render() {
    renderChrome();
    renderList();
  }

  /** 顶栏、统计卡、筛选：仅在数据变化时需要重建 */
  function renderChrome() {
    renderHostStatus();
    renderStats();
    renderChips();
  }

  /** 任务列表：搜索与筛选时只重建这一部分，避免整页重排 */
  function renderList() {
    const list = $('list');
    const empty = $('empty');
    list.textContent = '';

    const visible = state.tasks.filter(matches);
    if (!visible.length) {
      empty.hidden = false;
      empty.textContent = state.query ? '没有匹配的任务。' : '还没有可显示的任务记录。';
      return;
    }
    empty.hidden = true;

    if (state.filter === 'all' && !state.query) {
      let current = null;
      for (const task of visible) {
        const bucket = dayBucket(task.updated_at || task.last_activity_at);
        if (bucket !== current) {
          current = bucket;
          list.appendChild(el('div', 'group-label', bucket));
        }
        list.appendChild(buildCard(task));
      }
    } else {
      for (const task of visible) list.appendChild(buildCard(task));
    }
  }

  /** 把抽屉滚到对话末尾，让最近一条消息直接可见 */
  function scrollToConversationEnd(container) {
    requestAnimationFrame(() => {
      const panel = document.querySelector('.sheet-panel');
      if (!panel || !container) return;
      const panelRect = panel.getBoundingClientRect();
      const bodyRect = container.getBoundingClientRect();
      const overflow = bodyRect.bottom - panelRect.bottom;
      if (overflow > 0) panel.scrollTop += overflow + 16;
    });
  }

  /* ---------------- 对话渲染（头像/名称对齐、气泡、时间戳、Markdown） ---------------- */

  const escapeHtml = (s) =>
    String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  /** 行内格式：先整体转义再解析 Markdown，代码段用占位符保护，避免内部符号被二次处理 */
  function renderInline(raw) {
    let text = escapeHtml(raw);
    const stash = [];
    const keep = (html) => {
      stash.push(html);
      return '' + (stash.length - 1) + '';
    };
    text = text.replace(/`([^`\n]+)`/g, (m, c) => keep('<code>' + c + '</code>'));
    text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/(^|[\s（(「『])__(?!\s)([^_\n]*?\S)__(?=[\s)）。，：；,.:;!?！？」』]|$)/g, '$1<strong>$2</strong>');
    text = text.replace(/(^|[\s（(「『、，。：；])\*(?!\s|\*)([^*\n]*?\S)\*(?![*\w])/g, '$1<em>$2</em>');
    text = text.replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    text = text.replace(/(\d+)/g, (m, i) => stash[Number(i)]);
    return text;
  }

  function splitRow(line) {
    const s = line.trim().replace(/^\|/, '').replace(/\|$/, '');
    return s.split('|').map((c) => c.trim());
  }

  /** Markdown 子集：代码块、表格、列表、标题、引用、分割线；不认识的语法原样保留 */
  function renderMarkdown(src) {
    const text = String(src == null ? '' : src)
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n');
    const lines = text.split('\n');
    const out = [];
    let para = [];
    const flush = () => {
      if (!para.length) return;
      out.push('<p>' + para.map((l) => renderInline(l)).join('<br>') + '</p>');
      para = [];
    };

    let i = 0;
    while (i < lines.length) {
      const line = lines[i];

      if (/^```/.test(line.trim())) {
        flush();
        const buf = [];
        i += 1;
        while (i < lines.length && !/^```\s*$/.test(lines[i].trim())) {
          buf.push(lines[i]);
          i += 1;
        }
        i += 1; // 跳过闭合围栏
        out.push('<pre><code>' + escapeHtml(buf.join('\n')) + '</code></pre>');
        continue;
      }

      if (!line.trim()) {
        flush();
        i += 1;
        continue;
      }

      const h = line.match(/^(#{1,4})\s+(.+)$/);
      if (h) {
        flush();
        const level = Math.min(6, h[1].length + 2);
        out.push('<h' + level + '>' + renderInline(h[2].trim()) + '</h' + level + '>');
        i += 1;
        continue;
      }

      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        flush();
        out.push('<hr>');
        i += 1;
        continue;
      }

      if (/^>\s?/.test(line)) {
        flush();
        const buf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^>\s?/, ''));
          i += 1;
        }
        out.push('<blockquote>' + buf.map((l) => renderInline(l)).join('<br>') + '</blockquote>');
        continue;
      }

      if (/^\s*[-*+]\s+/.test(line)) {
        flush();
        const items = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*+]\s+/, ''));
          i += 1;
        }
        out.push('<ul>' + items.map((it) => '<li>' + renderInline(it) + '</li>').join('') + '</ul>');
        continue;
      }

      if (/^\s*\d+[.)]\s+/.test(line)) {
        flush();
        const items = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ''));
          i += 1;
        }
        out.push('<ol>' + items.map((it) => '<li>' + renderInline(it) + '</li>').join('') + '</ol>');
        continue;
      }

      // 表格：当前行含 | 且下一行是 |---| 形式的分隔行
      if (line.includes('|') && i + 1 < lines.length && lines[i + 1].includes('-') && /^\s*\|?[\s\-:|]+\|?\s*$/.test(lines[i + 1])) {
        flush();
        const header = splitRow(line);
        i += 2;
        const body = [];
        while (i < lines.length && lines[i].trim() && lines[i].includes('|')) {
          body.push(splitRow(lines[i]));
          i += 1;
        }
        let html = '<div class="table-wrap"><table><thead><tr>';
        for (const c of header) html += '<th>' + renderInline(c) + '</th>';
        html += '</tr></thead><tbody>';
        for (const row of body) {
          html += '<tr>' + row.map((c) => '<td>' + renderInline(c) + '</td>').join('') + '</tr>';
        }
        html += '</tbody></table></div>';
        out.push(html);
        continue;
      }

      para.push(line.trim());
      i += 1;
    }
    flush();
    return out.join('');
  }

  function fmtMsgTime(ts) {
    if (!ts) return '';
    const d = new Date(Number(ts));
    if (Number.isNaN(d.getTime())) return '';
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /** 同一角色的连续消息归为一组：头像与名称只出现一次 */
  function buildMessageGroup(role, msgs) {
    const isUser = role === 'u';
    const group = el('div', `msg-group${isUser ? ' is-user' : ' is-ai'}`);

    const head = el('div', 'msg-head');
    head.appendChild(el('span', `avatar${isUser ? ' avatar-user' : ' avatar-ai'}`, isUser ? '我' : 'AI'));
    head.appendChild(el('span', 'msg-name', isUser ? '我' : 'WorkBuddy'));
    group.appendChild(head);

    for (const m of msgs) {
      const bubble = el('div', `msg-bubble${isUser ? ' is-user' : ' is-ai'}`);
      bubble.innerHTML = renderMarkdown(m.t || '');
      group.appendChild(bubble);
      const t = fmtMsgTime(m.ts);
      if (t) group.appendChild(el('div', 'msg-time', t));
    }
    return group;
  }

  function renderConversation(container, messages) {
    container.textContent = '';
    const frag = document.createDocumentFragment();
    let curRole = null;
    let cur = [];
    const flush = () => {
      if (cur.length) frag.appendChild(buildMessageGroup(curRole, cur));
      cur = [];
    };
    for (const m of messages) {
      if (m.r !== curRole) {
        flush();
        curRole = m.r;
      }
      cur.push(m);
    }
    flush();
    container.appendChild(frag);
  }

  /** 按需拉取单个任务的对话正文；列表查询不包含正文字段 */
  async function loadTranscript(task, container, countEl) {
    try {
      const { data, error } = await cloud.database
        .from('wb_task')
        .select('transcript,transcript_count')
        .eq('task_uid', task.task_uid)
        .maybeSingle();
      if (error) throw error;

      const messages = data && Array.isArray(data.transcript) ? data.transcript : [];
      container.textContent = '';

      if (!messages.length) {
        container.appendChild(
          el(
            'div',
            'conv-hint',
            task.kind === 'automation'
              ? '定时任务没有对话记录。'
              : '该任务暂无可读对话。它可能是在云端运行，本地没有留下会话记录。',
          ),
        );
        if (countEl) countEl.textContent = '';
        return;
      }

      if (countEl) countEl.textContent = `${messages.length} 条`;
      renderConversation(container, messages);
      scrollToConversationEnd(container);
    } catch (e) {
      container.textContent = '';
      container.appendChild(el('div', 'conv-hint', `对话读取失败：${e.message || '请稍后重试'}`));
    }
  }

  function openSheet(task) {
    const body = $('sheet-body');
    body.textContent = '';
    body.appendChild(el('h2', null, task.title || '(未命名任务)'));

    if (task.status !== 'working') {
      const meta = STATUS_META[task.status] || { label: task.status || '未知', cls: 'badge-pending' };
      const statusWrap = el('div', 'sheet-status');
      statusWrap.appendChild(el('span', `badge ${meta.cls}`, meta.label));
      body.appendChild(statusWrap);
    }

    // 对话正文（优先展示，按需加载）
    const conv = el('section', 'conv');
    const convHead = el('div', 'conv-head');
    convHead.appendChild(el('span', 'conv-title', '对话记录'));
    const convCount = el('span', 'conv-count', '');
    convHead.appendChild(convCount);
    conv.appendChild(convHead);
    const convBody = el('div', 'conv-body');
    convBody.appendChild(el('div', 'conv-hint', '正在读取…'));
    conv.appendChild(convBody);
    body.appendChild(conv);

    const dl = el('dl', 'kv');
    const rows = [
      ['工作区', task.workspace || '—'],
      ['创建时间', fmtFull(task.created_at)],
      ['最后更新', fmtFull(task.updated_at)],
      ['最后活动', fmtFull(task.last_activity_at)],
      ['对话轮数', task.msg_count ? String(task.msg_count) : '—'],
      ['模型', task.model || '—'],
      ['模式', task.mode || '—'],
      ['类型', task.kind === 'automation' ? '定时任务' : '会话任务'],
    ];
    for (const [k, v] of rows) {
      dl.appendChild(el('dt', null, k));
      dl.appendChild(el('dd', null, v));
    }
    body.appendChild(dl);

    $('sheet').hidden = false;
    const panel = document.querySelector('.sheet-panel');
    if (panel) panel.scrollTop = 0; // 重置上次打开的滚动位置，随后再定位到末尾
    loadTranscript(task, convBody, convCount);
  }

  function closeSheet() {
    $('sheet').hidden = true;
  }

  /* ---------------- 启动 ---------------- */

  async function afterLogin() {
    const bound = await loadData().catch(() => false);
    if (bound) {
      render();
      showView('view-app');
      if (state.timer) clearInterval(state.timer);
      state.timer = setInterval(() => {
        if (document.visibilityState === 'visible') refresh({ silent: true });
      }, REFRESH_MS);
    } else {
      showView('view-bind');
    }
  }

  async function boot() {
    if (!PUBLIC_CONFIG.publishableKey) {
      showView('view-login');
      setError('login-error', '缺少配置：请复制 web/config.example.js 为 web/config.js，并填入你的 publishableKey。');
      return;
    }
    if (!window.WorkBuddyCloud || typeof window.WorkBuddyCloud.createWorkBuddyCloud !== 'function') {
      showView('view-login');
      setError('login-error', '客户端组件加载失败，请检查网络后刷新页面。');
      return;
    }
    cloud = window.WorkBuddyCloud.createWorkBuddyCloud(PUBLIC_CONFIG);

    // 事件绑定
    $('btn-workbuddy').addEventListener('click', signInWithWorkBuddy);
    $('btn-send-code').addEventListener('click', sendCode);
    $('form-email').addEventListener('submit', submitEmailLogin);
    $('form-bind').addEventListener('submit', submitBind);
    $('btn-logout').addEventListener('click', signOut);
    $('btn-logout-bind').addEventListener('click', signOut);
    $('btn-refresh').addEventListener('click', () => refresh());
    let searchTimer = null;
    $('search').addEventListener('input', (e) => {
      state.query = e.target.value.trim();
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(() => renderList(), 200);
    });
    $('sheet').addEventListener('click', (e) => {
      if (e.target.closest('[data-close]')) closeSheet();
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && state.session) refresh({ silent: true });
    });

    try {
      await restoreAuth();
    } catch (e) {
      setError('login-error', e.message || '登录状态检查失败。');
    }

    if (state.session) {
      await afterLogin();
    } else {
      showView('view-login');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
