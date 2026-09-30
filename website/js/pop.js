/* Scroll-linked "pop": each [data-pop] element gets --p from 0 (just entering
 * the bottom of the viewport) to 1 (its top reaches data-pop-end of the
 * viewport, default 0.2). The page's CSS turns --p into scale/tilt. */
(function () {
  const els = [...document.querySelectorAll('[data-pop]')];
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) { els.forEach(e => e.style.setProperty('--p', 1)); return; }
  let raf = 0;
  const ease = t => 1 - Math.pow(1 - t, 3);
  function tick() {
    raf = 0;
    const vh = innerHeight;
    for (const el of els) {
      const r = el.getBoundingClientRect(), end = parseFloat(el.dataset.popEnd || '0.2');
      if (r.top > vh * 1.2 || r.bottom < -vh * .2) continue;
      const p = Math.min(1, Math.max(0, (vh - r.top) / (vh * (1 - end))));
      el.style.setProperty('--p', ease(p).toFixed(4));
    }
  }
  const req = () => { if (!raf) raf = requestAnimationFrame(tick); };
  addEventListener('scroll', req, { passive: true }); addEventListener('resize', req); tick();
})();
