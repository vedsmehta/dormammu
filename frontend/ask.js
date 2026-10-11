// The ask box. On the landing page it is a live demo: chips fill the box and
// swap the example fan-out, and "Handle it" sends you to sign in. Whatever was
// typed is carried through sign-in and reappears in the dashboard's ask box.
(() => {
  'use strict';

  const KEY = 'dormammu.ask';
  const store = {
    get() { try { return sessionStorage.getItem(KEY) || ''; } catch { return ''; } },
    set(v) { try { sessionStorage.setItem(KEY, v); } catch { /* private mode */ } },
  };

  const input = document.querySelector('[data-ask]');
  const chips = document.querySelectorAll('[data-fill]');
  const fan = document.querySelector('.fan');

  // Example fan-outs for the landing page. All vendors and prices are made up.
  const SCENES = {
    move: [
      ['', 'Sutro Fiber', '$50/mo', '$70', 'Best internet quote'],
      ['', 'BayLink Internet', '$55/mo', '$65', 'Done'],
      ['texting', 'Cableton', 'Texting', '', 'No answer, so it switched to text'],
      ['talking', 'Hearth Mutual', 'Talking', '', 'Asking for the student rate'],
      ['ringing', 'Lantern Insurance', 'Ringing', '', 'Renters insurance'],
      ['', 'Campus Dental', 'Thu 2:30', '', 'Cleaning booked'],
    ],
    bill: [
      ['talking', 'Northline retention', 'Talking', '', 'Asking them to keep your rate'],
      ['ringing', 'Northline loyalty', 'Ringing', '', 'A second try, different desk'],
      ['', 'Pebble Mobile', '$40/mo', '$50', 'Competing quote to compare'],
      ['', 'Tandem Wireless', '$48/mo', '$55', 'Competing quote to compare'],
      ['texting', 'Kite Mobile', 'Texting', '', 'No answer, so it switched to text'],
      ['', 'Orbit Cellular', '$52/mo', '', 'No student rate'],
    ],
    dentist: [
      ['', 'Campus Dental', 'Thu 2:30', '', 'Earliest slot, booked'],
      ['', 'Elm Street Dental', 'Fri 9:00', '', 'Open, not booked'],
      ['', 'Bright Smile', 'Full', '', 'Nothing in the next two weeks'],
      ['talking', 'Parkside Dental', 'Talking', '', 'Checking Tuesday afternoon'],
      ['ringing', 'University Dental', 'Ringing', '', ''],
      ['texting', 'Dr. Okafor', 'Texting', '', 'Books by text'],
    ],
    cancel: [
      ['', 'Ironworks Gym', 'Canceled', '', 'Last charge on the 1st'],
      ['', 'StreamBox', 'Canceled', '', 'Confirmation on file'],
      ['talking', 'MealCrate', 'Talking', '', 'Declining the retention offer'],
      ['ringing', 'CloudVault', 'Ringing', '', ''],
      ['ringing', 'BoxFit', 'Needs you', '', 'They want the account holder'],
      ['texting', 'Paperback Club', 'Texting', '', 'No answer, so it switched to text'],
    ],
  };

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  };

  const playScene = (name) => {
    const scene = SCENES[name];
    const ends = fan && fan.querySelector('.ends');
    const lines = fan && fan.querySelector('.fan-lines');
    if (!scene || !ends || !lines) return;

    ends.replaceChildren(...scene.map(([state, vendor, got, was, note], i) => {
      const li = el('li');
      li.style.setProperty('--i', i);
      const result = el('span', state ? `got t-${state}` : 'got');
      if (was) result.append(el('s', '', was));
      result.append(got);
      li.append(el('span', state ? `dot ${state}` : 'dot'), el('b', '', vendor), result);
      if (note) li.append(el('small', '', note));
      return li;
    }));

    // Re-inserting the nodes restarts the draw-in animation, without the page-load delay.
    fan.style.setProperty('--t0', '0s');
    lines.replaceWith(lines.cloneNode(true));
  };

  chips.forEach((chip) => chip.addEventListener('click', () => {
    if (input) { input.value = chip.dataset.fill; input.focus(); }
    chips.forEach((c) => c.setAttribute('aria-pressed', String(c === chip)));
    if (chip.dataset.scene) playScene(chip.dataset.scene);
  }));

  // Landing page: remember the request, then let the form go to sign-in.
  const form = document.querySelector('form[data-ask-form]');
  if (form && input) form.addEventListener('submit', () => store.set(input.value.trim()));

  // Sign-in and dashboard: show the request that was carried over.
  const pending = store.get();
  if (pending) {
    const echo = document.querySelector('[data-ask-echo]');
    if (echo) { echo.querySelector('q').textContent = pending; echo.hidden = false; }
    if (input && input.hasAttribute('data-ask-restore')) input.value = pending;
  }
})();
