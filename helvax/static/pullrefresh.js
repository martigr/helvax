// Pull to refresh for the home screen app. iOS offers no pull to refresh in standalone mode
// (in Safari it has its own), so this script draws a small indicator and reloads the page.
// It stays off once a form field was changed, so a half-filled form isn't lost.
// The reload is a fresh GET of the current URL: a page shown in answer to a POST is never sent again.
(function () {
  var standalone = window.navigator.standalone === true ||
    (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  if (!standalone || !('ontouchstart' in window)) return;

  var THRESHOLD = 70;  // px the indicator has to travel before releasing reloads
  var MAX = 110;
  var root = document.documentElement;
  root.classList.add('pull-refresh');

  var indicator = document.createElement('div');
  indicator.className = 'ptr';
  indicator.setAttribute('aria-hidden', 'true');
  indicator.innerHTML = '<span class="ptr-icon"></span>';
  document.body.appendChild(indicator);

  var dirty = false;
  function markDirty(e) {
    var t = e.target;
    if (t && t.form && t.type !== 'hidden') dirty = true;
  }
  document.addEventListener('input', markDirty, true);
  document.addEventListener('change', markDirty, true);

  var startX = 0, startY = 0, distance = 0, pulling = false, busy = false;

  function scrollTop() {
    return (document.scrollingElement || root).scrollTop;
  }

  function show(d) {
    indicator.style.transform = 'translate(-50%, ' + (d - 48) + 'px)';
    indicator.style.opacity = Math.min(1, d / THRESHOLD);
    indicator.classList.toggle('ptr-ready', d >= THRESHOLD);
    indicator.querySelector('.ptr-icon').style.transform = 'rotate(' + Math.min(180, d / THRESHOLD * 180) + 'deg)';
  }

  function reset() {
    pulling = false;
    distance = 0;
    indicator.classList.add('ptr-back');
    indicator.style.transform = '';
    indicator.style.opacity = '';
    indicator.classList.remove('ptr-ready');
  }

  document.addEventListener('touchstart', function (e) {
    pulling = false;
    if (busy || dirty || e.touches.length !== 1 || scrollTop() > 0) return;
    if (document.querySelector('dialog[open]')) return;
    var t = e.target;
    if (t && t.closest && t.closest('input, select, textarea, [contenteditable]')) return;
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    pulling = true;
    indicator.classList.remove('ptr-back');
  }, {passive: true});

  document.addEventListener('touchmove', function (e) {
    if (!pulling) return;
    var dx = e.touches[0].clientX - startX;
    var dy = e.touches[0].clientY - startY;
    // Sideways swipes (e.g. scrolling a wide table) and scrolling up are not a pull
    if (dy <= 0 || Math.abs(dx) > dy || scrollTop() > 0) {
      if (distance > 0) reset(); else pulling = false;
      return;
    }
    distance = Math.min(MAX, dy * 0.5);  // resistance: the indicator moves at half the finger speed
    show(distance);
  }, {passive: true});

  function release() {
    if (!pulling) return;
    if (distance >= THRESHOLD && !dirty) {
      busy = true;
      pulling = false;
      indicator.classList.add('ptr-back', 'ptr-busy');
      show(THRESHOLD);
      window.location.replace(window.location.href.split('#')[0]);
    } else {
      reset();
    }
  }
  document.addEventListener('touchend', release, {passive: true});
  document.addEventListener('touchcancel', reset, {passive: true});

  // Back from the bfcache: don't keep showing the spinner
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) {
      busy = false;
      indicator.classList.remove('ptr-busy');
      reset();
    }
  });
})();
