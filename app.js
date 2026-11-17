'use strict';
// Standalone interactions: no WordPress, jQuery, or plugin requests.
const menuToggle = document.querySelector('.menu-toggle');
const navigation = document.querySelector('#site-nav');
const backdrop = document.querySelector('.menu-backdrop');
const closeButton = document.querySelector('.menu-close');
function closeMenu(restoreFocus = true) {
  navigation.classList.remove('is-open');
  menuToggle.setAttribute('aria-expanded', 'false');
  document.body.classList.remove('menu-open');
  backdrop.hidden = true;
  if (restoreFocus) menuToggle.focus();
}
if (menuToggle && navigation) {
  menuToggle.addEventListener('click', () => {
    if (navigation.classList.contains('is-open')) return closeMenu();
    navigation.classList.add('is-open');
    menuToggle.setAttribute('aria-expanded', 'true');
    document.body.classList.add('menu-open');
    backdrop.hidden = false;
    closeButton.focus();
  });
  closeButton.addEventListener('click', () => closeMenu());
  backdrop.addEventListener('click', () => closeMenu());
  navigation.querySelectorAll('a').forEach(link => link.addEventListener('click', () => closeMenu(false)));
  document.addEventListener('keydown', event => {
    if (!navigation.classList.contains('is-open')) return;
    if (event.key === 'Escape') closeMenu();
    if (event.key === 'Tab') {
      const links = [...navigation.querySelectorAll('button, a[href]')];
      if (event.shiftKey && document.activeElement === links[0]) { event.preventDefault(); links.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === links.at(-1)) { event.preventDefault(); links[0].focus(); }
    }
  });
  matchMedia('(min-width: 1025px)').addEventListener('change', event => { if (event.matches) closeMenu(false); });
}
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
document.querySelectorAll('.gallery').forEach(gallery => {
  const slides = [...gallery.querySelectorAll('.slide')];
  const pauseButton = gallery.querySelector('.gallery-pause');
  const status = gallery.querySelector('.gallery-status');
  let current = 0;
  let paused = reducedMotion.matches;
  let visible = false;
  let timer;
  let touchStart = null;
  function show(index, userInitiated = false) {
    current = (index + slides.length) % slides.length;
    slides.forEach((slide, i) => {
      slide.classList.toggle('is-active', i === current);
      slide.setAttribute('aria-hidden', String(i !== current));
    });
    status.setAttribute('aria-live', userInitiated ? 'polite' : 'off');
    status.textContent = `Photo ${current + 1} of ${slides.length}`;
  }
  function schedule() {
    clearInterval(timer);
    if (!paused && visible && !document.hidden && !gallery.matches(':hover') && !gallery.contains(document.activeElement)) {
      timer = setInterval(() => show(current + 1), Number(gallery.dataset.delay) || 6000);
    }
  }
  function pauseLabel() {
    pauseButton.textContent = paused ? '▷' : 'Ⅱ';
    pauseButton.setAttribute('aria-label', `${paused ? 'Play' : 'Pause'} ${gallery.getAttribute('aria-label')} slideshow`);
  }
  function step(direction) { show(current + direction, true); schedule(); }
  gallery.querySelector('.previous').addEventListener('click', () => step(-1));
  gallery.querySelector('.next').addEventListener('click', () => step(1));
  pauseButton.addEventListener('click', () => { paused = !paused; pauseLabel(); schedule(); });
  gallery.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault(); step(event.key === 'ArrowLeft' ? -1 : 1);
    }
  });
  gallery.addEventListener('touchstart', event => { touchStart = {x:event.changedTouches[0].clientX, y:event.changedTouches[0].clientY}; }, {passive:true});
  gallery.addEventListener('touchend', event => {
    if (!touchStart) return;
    const dx = event.changedTouches[0].clientX - touchStart.x;
    const dy = event.changedTouches[0].clientY - touchStart.y;
    if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy)) step(dx < 0 ? 1 : -1);
    touchStart = null;
  }, {passive:true});
  gallery.addEventListener('touchcancel', () => { touchStart = null; }, {passive:true});
  ['mouseenter', 'mouseleave', 'focusin'].forEach(type => gallery.addEventListener(type, schedule));
  gallery.addEventListener('focusout', () => setTimeout(schedule, 0));
  document.addEventListener('visibilitychange', schedule);
  reducedMotion.addEventListener('change', event => { paused = event.matches; pauseLabel(); schedule(); });
  new IntersectionObserver(entries => { visible = entries[0].isIntersecting; schedule(); }, {threshold:0.15}).observe(gallery);
  pauseLabel();
});
const video = document.querySelector('#testimonial');
if (video) {
  const play = document.querySelector('.video-play');
  video.parentElement.classList.add('has-js');
  play.hidden = false;
  play.addEventListener('click', async () => {
    try { await video.play(); } catch { video.controls = true; video.focus(); }
  });
  video.addEventListener('play', () => { play.hidden = true; video.parentElement.classList.add('has-played'); });
}
// Hours belong to the Miami studio, irrespective of the visitor's time zone.
const day = new Intl.DateTimeFormat('en-US', {weekday:'long', timeZone:'America/New_York'}).format(new Date());
document.querySelectorAll('#fyb-hours .fyb-row').forEach(row => {
  if (row.firstElementChild.textContent === day) { row.classList.add('active'); row.setAttribute('aria-current','date'); }
});
