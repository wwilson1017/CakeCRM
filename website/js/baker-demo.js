/* Scripted "Ask Baker" mock. NOT AI: every reply is canned, so the page can
 * show the workflow (read the deal, answer, log, draft-with-approval) with no
 * backend. It plays itself once when scrolled into view; any click takes over.
 * Mode behaviour mirrors the app: Read changes nothing; Ask runs routine edits
 * (logging a call) but asks before an email draft; Auto runs both. */
(function () {
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const day = n => { const d = new Date(); d.setDate(d.getDate() + n); return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); };
  const CANCEL = Symbol('cancel');

  const MODES = {
    read: 'Read — Baker can look things up but not change data.',
    ask: 'Ask — everyday edits like logging a call go straight in. Email drafts, deletes, merges and notifications wait for your OK.',
    auto: 'Auto — changes run without asking. Switching it on is your call.',
  };
  const Q = {
    next: 'What should my next step be on this deal?',
    health: 'How healthy is this deal?',
    log: 'Log a call: Priya agreed to a Saturday double delivery instead of Sunday.',
    draft: 'Draft a follow-up email to Priya.',
  };
  const MAIL = '<b>To:</b> Priya Nair\n<b>Subject:</b> Saturday delivery for the pastry case\n\nHi Priya — thanks for the call. We\'ll do a double delivery on Saturday so both cafés are stocked before Sunday\'s open. I\'ll confirm the window by Friday.';

  function mount(root) {
    let mode = 'ask', runId = 0, autoplayed = false;
    root.classList.add('bk');
    root.innerHTML = `
      <div class="bk-deal">
        <h5>Lakeside Coffee pastry case</h5><div class="who">Priya Nair · Lakeside Coffee</div>
        <div class="bk-val"><span class="bk-stage">Negotiation</span><b>$3,100</b></div>
        <dl class="bk-kv"><dt>Probability</dt><dd>70%</dd><dt>Temperature</dt><dd>🔥 Hot</dd><dt>Expected close</dt><dd>${day(5)}</dd><dt>Owner</dt><dd>You</dd></dl>
        <div class="bk-h">Due today</div><div class="bk-todo">Confirm pastry case delivery window with Priya</div>
        <div class="bk-h">Activity</div><div class="bk-acts"></div>
      </div>
      <div class="bk-chat">
        <div class="bk-top"><b>Baker</b><span class="bk-live" hidden>● Live demo</span><span class="bk-demo">Scripted, not AI</span></div>
        <div class="bk-log" aria-live="polite"></div>
        <div class="bk-foot"><div class="bk-viewing">Viewing: Lakeside Coffee pastry case</div>
          <div class="bk-chips">
            <button class="bk-chip" data-s="next">What should my next step be?</button>
            <button class="bk-chip" data-s="health">How healthy is this deal?</button>
            <button class="bk-chip" data-s="log">Log a call I just had</button>
            <button class="bk-chip" data-s="draft">Draft a follow-up email</button>
            <button class="bk-chip bk-replay" data-s="replay" hidden>↻ Replay the demo</button>
          </div>
          <div class="bk-comp"><div class="bk-input"><span class="bk-typed"></span><span class="bk-ph">Message Baker…</span></div><span class="bk-send" aria-hidden="true">↑</span></div>
          <div class="bk-modes" role="group" aria-label="Tool mode">${['read', 'ask', 'auto'].map(m => `<button class="bk-mode" data-m="${m}" aria-pressed="${m === mode}">${m[0].toUpperCase() + m.slice(1)}</button>`).join('')}</div>
          <div class="bk-modehint">${MODES[mode]}</div></div>
      </div>
      <svg class="bk-cursor" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 2l15 9-6.5 1.5L9 19z" fill="#1d1a17" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
    const $ = s => root.querySelector(s);
    const log = $('.bk-log'), typed = $('.bk-typed'), ph = $('.bk-ph'), send = $('.bk-send'), cursor = $('.bk-cursor');
    const scroll = () => { log.scrollTop = log.scrollHeight; };
    const add = (cls, html) => { const d = document.createElement('div'); d.className = cls; d.innerHTML = html; log.appendChild(d); scroll(); return d; };

    function reset() {
      log.innerHTML = '<div class="bk-ai"><p>Hi — I can see <b>Lakeside Coffee pastry case</b> is open. Ask me anything about it.</p></div>';
      $('.bk-acts').innerHTML = `<div class="bk-act"><i>☎</i><div>Call<small>Yesterday — Priya is fine on price; the sticking point is a Sunday delivery before the 6am open.</small></div></div>
        <div class="bk-act"><i>✉</i><div>Email<small>${day(-6)} — Sent the revised quote for two cafés.</small></div></div>`;
      typed.textContent = ''; ph.hidden = false; cursor.classList.remove('on');
    }

    // Each run gets its own clock; starting another run makes the old one's next sleep throw.
    function runner() {
      const id = ++runId;
      const sleep = ms => new Promise((res, rej) => setTimeout(() => (id === runId ? res() : rej(CANCEL)), RM ? 0 : ms));
      const alive = () => { if (id !== runId) throw CANCEL; };

      async function typeInto(text) {
        ph.hidden = true; typed.textContent = '';
        for (let i = 1; i <= text.length; i++) { typed.textContent = text.slice(0, i); await sleep(28 + Math.random() * 45); }
        await sleep(350); send.classList.add('go'); await sleep(220); send.classList.remove('go');
        typed.textContent = ''; ph.hidden = false;
      }
      async function thinking(box) {
        const t = document.createElement('div'); t.className = 'bk-dots'; t.innerHTML = '<i></i><i></i><i></i>'; box.appendChild(t); scroll();
        await sleep(900); t.remove();
      }
      async function steps(box, labels) {
        const wrap = document.createElement('div'); wrap.className = 'bk-steps'; box.appendChild(wrap);
        for (const l of labels) { const s = document.createElement('span'); s.className = 'bk-step'; s.textContent = l; wrap.appendChild(s); scroll(); await sleep(700); s.classList.add('ok'); }
      }
      async function type(box, blocks) {
        let ul = null;
        for (const [tag, lead, text] of blocks) {
          let el;
          if (tag === 'li') { if (!ul) { ul = document.createElement('ul'); box.appendChild(ul); } el = document.createElement('li'); ul.appendChild(el); }
          else { ul = null; el = document.createElement('p'); box.appendChild(el); }
          if (lead) { const b = document.createElement('b'); b.textContent = lead + ' '; el.appendChild(b); }
          const span = document.createElement('span'), caret = document.createElement('span'); caret.className = 'bk-caret';
          el.append(span, caret);
          // stream word by word, like a model does
          const words = text.split(/(\s+)/);
          for (let i = 0; i < words.length; i += 2) { span.textContent = words.slice(0, i + 1).join(''); scroll(); await sleep(35 + Math.random() * 40); }
          caret.remove();
        }
      }
      async function pointAndClick(btn) {
        const r = root.getBoundingClientRect(), b = btn.getBoundingClientRect();
        cursor.style.left = (b.left - r.left + b.width / 2) + 'px'; cursor.style.top = (b.top - r.top + b.height / 2) + 'px';
        if (!cursor.classList.contains('on')) { cursor.classList.add('on'); }
        await sleep(900); btn.classList.add('press'); await sleep(180); btn.classList.remove('press');
        cursor.classList.remove('on'); btn.click();
      }
      function logCall() {
        const a = document.createElement('div'); a.className = 'bk-act new';
        a.innerHTML = '<i>☎</i><div>Call<small>Just now — Priya agreed to a Saturday double delivery instead of Sunday.</small></div>';
        $('.bk-acts').prepend(a);
      }

      const S = {
        async next(b) {
          await steps(b, ['Reading the deal', 'Checking deal health', 'Reading activity']);
          await type(b, [
            ['p', 'Call Priya today and settle the Sunday delivery.', "That's the only thing holding this deal up."],
            ['li', "Price isn't the problem.", "Yesterday's call says she's fine on price."],
            ['li', 'The next step is already scheduled.', 'Your todo due today is "Confirm pastry case delivery window with Priya".'],
            ['li', 'Have a backup ready.', "If a Sunday drop before 6am won't work, offer a double delivery on Saturday."],
          ]);
        },
        async health(b) {
          await steps(b, ['Checking deal health']);
          await type(b, [
            ['p', 'Good shape.', "It's in Negotiation at 70%, marked hot, and you touched it yesterday, so it isn't going cold."],
            ['li', 'Timing:', `15 days in stage, expected close ${day(5)}.`],
            ['li', 'One small gap:', 'no company is linked to this deal. Want me to link Lakeside Coffee?'],
          ]);
        },
        async log(b) {
          if (mode === 'read') { await type(b, [['p', '', "I'm in Read mode, so I can look things up but not change anything. Switch to Ask and I'll log that call."]]); return; }
          await steps(b, ['Logging a call']);
          logCall();
          b.insertAdjacentHTML('beforeend', '<p class="bk-done">✓ Logged a call on Lakeside Coffee pastry case.</p>'); scroll();
          await type(b, [['p', '', mode === 'ask' ? "Everyday edits like this go straight in under Ask. Want me to mark today's todo done too?" : 'Done. Auto mode runs changes without asking.']]);
        },
        async draft(b, auto) {
          if (mode === 'read') { await type(b, [['p', '', "I'm in Read mode, so I can't create a Gmail draft. Switch to Ask and I'll write it for your approval."]]); return; }
          await steps(b, ['Reading the deal', 'Writing a draft']);
          if (mode === 'auto') {
            b.insertAdjacentHTML('beforeend', `<div class="bk-card"><div class="t">Gmail draft created</div><div class="mail">${MAIL}</div></div><p class="bk-done" style="margin-top:8px">✓ Saved to your Gmail Drafts. Nothing was sent — you send it.</p>`);
            scroll(); return;
          }
          await type(b, [['p', '', "Here's a draft. Email drafts always wait for your OK:"]]);
          const card = document.createElement('div'); card.className = 'bk-card';
          card.innerHTML = `<div class="t">Approve: create Gmail draft</div><div class="mail">${MAIL}</div><div class="bk-btns"><button class="yes">Approve</button><button class="no">Deny</button></div>`;
          b.appendChild(card); scroll();
          const answer = new Promise(res => { card.querySelector('.yes').onclick = () => res(true); card.querySelector('.no').onclick = () => res(false); });
          if (auto) { await sleep(1300); await pointAndClick(card.querySelector('.yes')); }
          const ok = await answer; alive();
          card.querySelectorAll('button').forEach(x => x.disabled = true);
          const r = add('bk-ai', '');
          if (ok) { await steps(r, ['Creating draft']); r.insertAdjacentHTML('beforeend', '<p class="bk-done">✓ Saved to your Gmail Drafts. Nothing was sent — you send it.</p>'); }
          else await type(r, [['p', '', 'Okay — no draft created.']]);
          scroll();
        },
      };

      async function ask(key, auto) {
        if (auto) await typeInto(Q[key]);
        add('bk-me', esc(Q[key]));
        const b = add('bk-ai', '');
        await thinking(b);
        await S[key](b, auto);
      }
      return { ask, sleep, id };
    }

    async function autoplay() {
      const r = runner(); reset();
      $('.bk-live').hidden = false; $('.bk-replay').hidden = true;
      try {
        await r.sleep(700);
        for (const k of ['next', 'log', 'draft']) { await r.ask(k, true); await r.sleep(1600); }
      } catch (e) { if (e !== CANCEL) throw e; return; }
      if (r.id === runId) { $('.bk-live').hidden = true; $('.bk-replay').hidden = false; }
    }

    root.addEventListener('click', async e => {
      const m = e.target.closest('.bk-mode');
      if (m) { mode = m.dataset.m; root.querySelectorAll('.bk-mode').forEach(x => x.setAttribute('aria-pressed', x === m)); $('.bk-modehint').textContent = MODES[mode]; return; }
      if (e.target.closest('.bk-btns')) return; // approval buttons resolve inside the running script
      const c = e.target.closest('.bk-chip'); if (!c) return;
      if (c.dataset.s === 'replay') { autoplay(); return; }
      // Any chip takes over from the autoplay (or from a reply still streaming).
      const r = runner(); cursor.classList.remove('on'); typed.textContent = ''; ph.hidden = false;
      $('.bk-live').hidden = true; $('.bk-replay').hidden = false;
      try { await r.ask(c.dataset.s, false); } catch (err) { if (err !== CANCEL) throw err; }
    });

    reset();
    if (RM) { $('.bk-replay').hidden = false; return; }
    new IntersectionObserver((es, io) => {
      if (es.some(x => x.isIntersecting) && !autoplayed) { autoplayed = true; io.disconnect(); autoplay(); }
    }, { threshold: 0.45 }).observe(root);
  }
  document.querySelectorAll('[data-baker-demo]').forEach(mount);
})();
