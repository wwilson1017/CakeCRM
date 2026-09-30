/* Small page behaviours: the AI-key switch in "Simple on purpose" and the hero slice leaning toward the pointer. */
const sw=document.getElementById('sw'),box=document.getElementById('aibox'),l=document.getElementById('swl');
sw.onclick=()=>{const on=sw.getAttribute('aria-checked')!=='true';sw.setAttribute('aria-checked',on);box.classList.toggle('on-ai',on);l.textContent=on?'AI key: added':'AI key: none'};

// the slice leans a few degrees toward the pointer
(()=>{const t=document.getElementById('markTilt');if(!t||matchMedia('(prefers-reduced-motion:reduce)').matches||matchMedia('(pointer:coarse)').matches)return;
addEventListener('pointermove',e=>{const r=t.getBoundingClientRect(),dx=(e.clientX-(r.left+r.width/2))/innerWidth,dy=(e.clientY-(r.top+r.height/2))/innerHeight;
t.style.transform=`rotateY(${(dx*28).toFixed(1)}deg) rotateX(${(-dy*20).toFixed(1)}deg)`},{passive:true})})();
