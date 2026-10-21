import gsap from 'gsap';

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
const numberFormat = new Intl.NumberFormat('en-NG');

// Cards marked data-tilt lean toward the pointer on a spring.
export function bindTilt(root) {
  if (reducedMotion || !finePointer) return;
  root.querySelectorAll('[data-tilt]').forEach((el) => {
    if (el._tilt) return;
    const max = Number(el.dataset.tilt) || 6;
    const spring = { rx: 0, ry: 0, tx: 0, ty: 0, frame: 0, hover: 0, th: 0 };
    el._tilt = spring;
    el.classList.add('tilt-ready');
    const loop = () => {
      spring.rx += (spring.tx - spring.rx) * 0.14;
      spring.ry += (spring.ty - spring.ry) * 0.14;
      spring.hover += (spring.th - spring.hover) * 0.14;
      el.style.transform = `perspective(1100px) rotateX(${spring.rx.toFixed(3)}deg) rotateY(${spring.ry.toFixed(3)}deg) translateZ(${(spring.hover * 6).toFixed(2)}px)`;
      const settled = Math.abs(spring.tx - spring.rx) < 0.01 && Math.abs(spring.ty - spring.ry) < 0.01 && Math.abs(spring.th - spring.hover) < 0.005;
      if (settled && spring.th === 0) {
        el.style.transform = '';
        spring.frame = 0;
      } else spring.frame = requestAnimationFrame(loop);
    };
    const kick = () => { if (!spring.frame) spring.frame = requestAnimationFrame(loop); };
    el.addEventListener('pointermove', (event) => {
      const rect = el.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width;
      const y = (event.clientY - rect.top) / rect.height;
      spring.ty = (x - 0.5) * max * 2;
      spring.tx = (0.5 - y) * max * 2;
      spring.th = 1;
      kick();
    });
    el.addEventListener('pointerleave', () => {
      spring.tx = 0;
      spring.ty = 0;
      spring.th = 0;
      kick();
    });
  });
}

// Entrance choreography for a freshly opened view.
export function revealView(root) {
  if (reducedMotion) return;
  const blocks = root.querySelectorAll('[data-reveal]');
  if (blocks.length) {
    gsap.fromTo(blocks,
      { opacity: 0, y: 34, rotateX: -9, transformPerspective: 1200, transformOrigin: '50% 0%' },
      { opacity: 1, y: 0, rotateX: 0, duration: 0.95, ease: 'expo.out', stagger: 0.07, clearProps: 'opacity,transform' });
  }
  const items = root.querySelectorAll('[data-stagger] > *');
  if (items.length) {
    gsap.fromTo(items, { opacity: 0, y: 16 }, { opacity: 1, y: 0, duration: 0.7, ease: 'power3.out', stagger: 0.045, delay: 0.18, clearProps: 'opacity,transform' });
  }
  root.querySelectorAll('[data-bar]').forEach((bar, index) => {
    gsap.fromTo(bar, { width: '0%' }, { width: `${bar.dataset.bar}%`, duration: 1.5, delay: 0.3 + index * 0.08, ease: 'expo.out' });
  });
  root.querySelectorAll('[data-count]').forEach((el) => {
    const target = Number(el.dataset.count);
    const decimals = Number(el.dataset.decimals || 0);
    const counter = { value: 0 };
    gsap.to(counter, {
      value: target,
      duration: target > 100 ? 2 : 1.4,
      delay: 0.25,
      ease: 'expo.out',
      onUpdate: () => {
        el.textContent = decimals ? counter.value.toFixed(decimals) : numberFormat.format(Math.round(counter.value));
      },
    });
  });
}

// Short exit for the outgoing view, so navigation feels continuous.
export function leaveView(root) {
  if (reducedMotion || !root) return Promise.resolve();
  return new Promise((resolve) => {
    gsap.to(root, { opacity: 0, y: -14, scale: 0.985, filter: 'blur(4px)', duration: 0.22, ease: 'power2.in', onComplete: resolve });
  });
}
