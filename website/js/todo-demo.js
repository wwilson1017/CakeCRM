/* CakeCRM homepage todo demo — a working, browser-only copy of the GTD todos.
 * Mount: <div data-todo-demo></div> + this script + todo-demo.css.
 * State lives in localStorage (per visitor) and never leaves the browser. */
(function () {
  const KEY = 'cakecrm_site_todo_demo_v1';
  const CONTEXTS = ['@calls', '@computer', '@errands', '@office'];
  const PROJECTS = ['Blake holiday party', 'Spring food show', 'Website'];
  const KINDS = [['next_action', 'Next action'], ['waiting_for', 'Waiting'], ['someday_maybe', 'Someday']];
  const WD = [['sunday', 'sun'], ['monday', 'mon'], ['tuesday', 'tue', 'tues'], ['wednesday', 'wed'], ['thursday', 'thu', 'thur', 'thurs'], ['friday', 'fri'], ['saturday', 'sat']];

  const pad = n => String(n).padStart(2, '0');
  const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const addDays = n => { const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() + n); return iso(d); };
  const today = () => addDays(0);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = ds => {
    const t = today(); if (ds === t) return 'Today'; if (ds === addDays(1)) return 'Tomorrow';
    const [y, m, d] = ds.split('-').map(Number); // calendar date: local parts, never UTC
    return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  };

  function seed() {
    let id = 0; const t = (title, status, o = {}) => ({ id: ++id, title, status, context: '', project: '', due: '', star: false, ...o });
    return [
      t('Ask Rachel how many holiday gift boxes she needs', 'inbox'),
      t('Look into a booth at the spring food show', 'inbox'),
      t('Order more pastry boxes', 'inbox'),
      t('Renew food handler certificates', 'next_action', { context: '@errands', due: addDays(-4) }),
      t('Call Lisa about seasonal produce', 'next_action', { context: '@calls', due: addDays(-2) }),
      t('Draft dessert menu for the Blake holiday party', 'next_action', { context: '@computer', project: 'Blake holiday party', due: addDays(-2) }),
      t('Send revised catering menu', 'next_action', { context: '@computer', due: addDays(0), star: true }),
      t('Confirm pastry case delivery window with Priya', 'next_action', { context: '@calls', due: addDays(0) }),
      t('Update the price sheet', 'next_action', { context: '@computer', project: 'Website' }),
      t('Pick up cake boards', 'next_action', { context: '@errands', due: addDays(3) }),
      t('Signed proposal back from TechStart', 'waiting_for', { context: '@office' }),
      t('Book the tasting for the Chen-Williams wedding', 'done', { context: '@calls' }),
    ];
  }

  // Parse "call Val tomorrow #Website @calls !" — a small subset of the app's quick-add.
  function parse(input) {
    const out = { title: '', project: '', context: '', star: false, due: '', chips: [] };
    const words = input.split(/\s+/).filter(Boolean), keep = [];
    for (let i = 0; i < words.length; i++) {
      const w = words[i], lw = w.toLowerCase();
      if (/^#\S+/.test(w) && !out.project) { out.project = w.slice(1).replace(/[-_]/g, ' '); out.chips.push('# ' + out.project); continue; }
      if (/^@\S+/.test(w) && !out.context) { out.context = lw; out.chips.push(lw); continue; }
      if (w === '!' && !out.star) { out.star = true; out.chips.push('★ starred'); continue; }
      if (!out.due) {
        if (lw === 'today') { out.due = addDays(0); out.chips.push('due today'); continue; }
        if (lw === 'tomorrow') { out.due = addDays(1); out.chips.push('due tomorrow'); continue; }
        if (lw === 'next' && (words[i + 1] || '').toLowerCase() === 'week') { out.due = addDays(7); out.chips.push('due next week'); i++; continue; }
        if (lw === 'in' && /^\d{1,3}$/.test(words[i + 1] || '') && /^days?$/i.test(words[i + 2] || '')) { const n = +words[i + 1]; out.due = addDays(n); out.chips.push(`due in ${n} day${n === 1 ? '' : 's'}`); i += 2; continue; }
        const wd = WD.findIndex(n => n.includes(lw));
        if (wd >= 0) { const now = new Date().getDay(); const n = ((wd - now + 7) % 7) || 7; out.due = addDays(n); out.chips.push('due ' + fmt(out.due)); continue; }
      }
      keep.push(w);
    }
    out.title = keep.join(' ');
    return out;
  }

  function mount(root) {
    let todos, tab = 'today', triKind = 'next_action', lastDone = null, toastTimer;
    try { todos = JSON.parse(localStorage.getItem(KEY)) || seed(); } catch { todos = seed(); }
    const save = () => { try { localStorage.setItem(KEY, JSON.stringify(todos)); } catch { /* private mode: demo still works */ } };
    const nextId = () => Math.max(0, ...todos.map(t => t.id)) + 1;

    root.classList.add('td');
    root.innerHTML = `<div class="td-wrap">
      <div class="td-head"><div><h4>Todos</h4><p>Capture everything. Mind like water.</p></div><button class="td-reset" data-a="reset">Reset demo</button></div>
      <div class="td-tabs" role="tablist"></div>
      <div class="td-body"></div>
      <div class="td-foot">A working demo in your browser — nothing is sent anywhere.</div>
      <div class="td-toast" role="status" aria-live="polite"></div></div>`;
    const $tabs = root.querySelector('.td-tabs'), $body = root.querySelector('.td-body'), $toast = root.querySelector('.td-toast');

    function toast(msg, undo) {
      $toast.innerHTML = esc(msg) + (undo ? '<button data-a="undo">Undo</button>' : '');
      $toast.classList.add('show'); clearTimeout(toastTimer);
      toastTimer = setTimeout(() => $toast.classList.remove('show'), 3200);
    }

    const open = t => t.status !== 'done';
    const t0 = () => today();
    function rowHTML(t) {
      const over = t.due && t.due < t0() && open(t);
      const meta = [
        t.due ? `<span class="td-due${over ? ' over' : ''}">${esc(fmt(t.due))}</span>` : '',
        t.context ? `<span class="td-tag">${esc(t.context)}</span>` : '',
        t.project ? `<span class="td-tag"># ${esc(t.project)}</span>` : '',
      ].join('');
      return `<div class="td-row${t.status === 'done' ? ' done' : ''}" data-id="${t.id}">
        <button class="td-cb${t.status === 'done' ? ' on' : ''}" data-a="toggle" aria-label="${t.status === 'done' ? 'Mark not done' : 'Complete'}: ${esc(t.title)}"></button>
        <div class="td-main"><div class="td-t">${esc(t.title)}</div>${meta ? `<div class="td-meta">${meta}</div>` : ''}</div>
        <button class="td-star${t.star ? ' on' : ''}" data-a="star" aria-label="Star" aria-pressed="${t.star}">★</button></div>`;
    }
    const list = arr => arr.map(rowHTML).join('');
    const addBox = ph => `<form class="td-add" data-a="add"><input aria-label="Add to inbox" placeholder="${ph}" autocomplete="off"><button type="submit" disabled>Add</button></form>
      <div class="td-chips"><span class="td-hint">Try <code>call Val tomorrow #Website @calls !</code></span></div>`;

    function render() {
      const inbox = todos.filter(t => t.status === 'inbox').sort((a, b) => b.id - a.id); // newest first: triage what you just typed
      const tabs = [['today', 'Today'], ['inbox', 'Inbox', inbox.length], ['todo', 'To Do'], ['waiting', 'Waiting'], ['done', 'Done']];
      $tabs.innerHTML = tabs.map(([k, l, c]) => `<button class="td-tab" role="tab" aria-selected="${tab === k}" data-tab="${k}">${l}${c ? `<span class="c">${c}</span>` : ''}</button>`).join('');
      let h = '';
      if (tab === 'today') {
        const t = t0(), act = todos.filter(x => open(x) && x.status !== 'inbox' && x.status !== 'someday_maybe');
        const over = act.filter(x => x.due && x.due < t), due = act.filter(x => x.due === t), star = act.filter(x => x.star && !(x.due && x.due <= t));
        h = addBox('Add to inbox — try “call Val tomorrow @calls !”');
        if (over.length) h += `<div class="td-sec red">Overdue</div>${list(over)}`;
        if (due.length) h += `<div class="td-sec">Due today</div>${list(due)}`;
        if (star.length) h += `<div class="td-sec">Starred</div>${list(star)}`;
        if (!over.length && !due.length && !star.length) h += `<div class="td-empty"><b>Nothing due today.</b>Enjoy it, or pull something from To Do.</div>`;
      } else if (tab === 'inbox') {
        h = addBox('Add to inbox…');
        if (!inbox.length) h += `<div class="td-empty"><b>Inbox zero.</b>Everything has a home.</div>`;
        else {
          const t = inbox[0];
          h += `<div class="td-tri" data-id="${t.id}"><div class="ttl">${esc(t.title)}</div><div class="left">${inbox.length} in inbox · one at a time</div>
            <div class="td-step"><i>1</i>What kind of action?</div>
            <div class="td-opts">${KINDS.map(([k, l]) => `<button class="td-opt" data-a="kind" data-k="${k}" aria-pressed="${triKind === k}">${l}</button>`).join('')}</div>
            <div class="td-step"><i>2</i>Add detail (optional)</div>
            <div class="td-det"><select data-f="project" aria-label="Project"><option value="">No project</option>${[...new Set([...PROJECTS, t.project].filter(Boolean))].map(p => `<option${p === t.project ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select>
              <input type="date" data-f="due" aria-label="Due date" value="${t.due}"></div>
            <div class="td-step"><i>3</i>Last step — set context <span class="req" aria-label="required">★</span></div>
            <div class="td-opts">${[...new Set([...CONTEXTS, t.context].filter(Boolean))].map(c => `<button class="td-opt ctx" data-a="file" data-c="${esc(c)}"${c === t.context ? ' aria-pressed="true"' : ''}>${esc(c)}</button>`).join('')}</div></div>`;
          if (inbox.length > 1) h += `<div class="td-sec">Up next</div>${inbox.slice(1).map(x => `<div class="td-row"><div class="td-main"><div class="td-t">${esc(x.title)}</div></div></div>`).join('')}`;
        }
      } else if (tab === 'todo') {
        const na = todos.filter(t => t.status === 'next_action');
        if (!na.length) h = `<div class="td-empty"><b>No next actions.</b>File something from the Inbox.</div>`;
        for (const c of [...new Set(na.map(t => t.context || 'No context'))].sort()) h += `<div class="td-group"><div class="td-sec">${esc(c)}</div>${list(na.filter(t => (t.context || 'No context') === c))}</div>`;
      } else if (tab === 'waiting') {
        const w = todos.filter(t => t.status === 'waiting_for'), s = todos.filter(t => t.status === 'someday_maybe');
        h = w.length ? `<div class="td-sec">Waiting for</div>${list(w)}` : `<div class="td-empty"><b>Not waiting on anyone.</b></div>`;
        if (s.length) h += `<div class="td-sec">Someday / maybe</div>${list(s)}`;
      } else {
        const d = todos.filter(t => t.status === 'done').reverse();
        h = d.length ? list(d) : `<div class="td-empty"><b>Nothing done yet.</b>Tick something off on Today.</div>`;
      }
      $body.innerHTML = h;
    }

    function complete(t, rowEl) {
      if (t.status === 'done') { t.status = t.prev || 'next_action'; save(); render(); return; }
      lastDone = { id: t.id, prev: t.status };
      t.prev = t.status; t.status = 'done';
      save();
      const cb = rowEl && rowEl.querySelector('.td-cb'); if (cb) cb.classList.add('on');
      if (rowEl) { rowEl.classList.add('done'); setTimeout(() => { rowEl.classList.add('out'); setTimeout(render, 320); }, 350); } else render();
      toast('Done: ' + t.title, true);
    }

    root.addEventListener('click', e => {
      const b = e.target.closest('[data-a],[data-tab]'); if (!b || !root.contains(b)) return;
      if (b.dataset.tab) { tab = b.dataset.tab; triKind = 'next_action'; render(); return; }
      const a = b.dataset.a, row = b.closest('[data-id]'), t = row && todos.find(x => x.id === +row.dataset.id);
      if (a === 'reset') { todos = seed(); tab = 'today'; save(); render(); toast('Demo reset'); }
      else if (a === 'undo' && lastDone) { const u = todos.find(x => x.id === lastDone.id); if (u) { u.status = lastDone.prev; lastDone = null; save(); render(); } $toast.classList.remove('show'); }
      else if (a === 'toggle' && t) complete(t, row);
      else if (a === 'star' && t) { t.star = !t.star; save(); render(); }
      else if (a === 'kind') { triKind = b.dataset.k; root.querySelectorAll('[data-a=kind]').forEach(x => x.setAttribute('aria-pressed', x === b)); }
      else if (a === 'file' && t) {
        const tri = b.closest('.td-tri');
        t.project = tri.querySelector('[data-f=project]').value; t.due = tri.querySelector('[data-f=due]').value;
        t.context = b.dataset.c; t.status = triKind; triKind = 'next_action'; save();
        tri.style.transition = 'opacity .25s,transform .25s'; tri.style.opacity = 0; tri.style.transform = 'translateX(30px)';
        setTimeout(render, 230);
        toast(`Filed to ${t.status === 'next_action' ? 'To Do' : t.status === 'waiting_for' ? 'Waiting' : 'Someday'} · ${t.context}`);
      }
    });
    root.addEventListener('input', e => {
      if (!e.target.matches('.td-add input')) return;
      const p = parse(e.target.value), form = e.target.form;
      form.querySelector('button').disabled = !p.title;
      form.nextElementSibling.innerHTML = p.chips.length ? p.chips.map(c => `<span class="td-chip">${esc(c)}</span>`).join('') : `<span class="td-hint">Try <code>call Val tomorrow #Website @calls !</code></span>`;
    });
    root.addEventListener('submit', e => {
      if (!e.target.matches('.td-add')) return; e.preventDefault();
      const input = e.target.querySelector('input'), p = parse(input.value); if (!p.title) return;
      todos.push({ id: nextId(), title: p.title, status: 'inbox', context: p.context, project: p.project, due: p.due, star: p.star });
      save(); const stay = tab; render();
      if (stay === 'inbox') root.querySelector('.td-add input').focus();
      else { const i = root.querySelector('.td-add input'); if (i) i.focus(); }
      toast('Added to Inbox — file it there');
    });
    render();
  }
  document.querySelectorAll('[data-todo-demo]').forEach(mount);
})();
