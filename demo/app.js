import { MOODS, createStation, setMood, nextTrack, toggleLike, toggleVibeLock, newMix, currentTrack } from './model.mjs';

const $ = selector => document.querySelector(selector);
const moodLabels = { neutral: 'NEUTRAL', chill: 'CHILL / LOW KEY', energetic: 'ENERGY / LIFTED', focus: 'FOCUS', melancholy: 'MELANCHOLY', discovery: 'DISCOVERY' };
const queueSlots = [
  { name: 'exploit', color: '#2dd4a0', bg: '#0a2418', reason: 'A familiar thread from this station.' },
  { name: 'explore', color: '#5eadf5', bg: '#09142a', reason: 'A nearby sound to keep things moving.' },
  { name: 'wildcard', color: '#a78bfa', bg: '#100728', reason: 'A small turn in a new direction.' },
  { name: 'context', color: '#f59e0b', bg: '#161008', reason: 'A natural fit for this moment.' },
];
const els = {
  moodButtons: [...document.querySelectorAll('[data-mood]')], title: $('#track-title'), artist: $('#track-artist'), canvas: $('#waveform'),
  like: $('#like-button'), play: $('#play-button'), seek: $('#seek'), elapsed: $('#elapsed'), duration: $('#duration'),
  lock: $('#lock-button'), queue: $('#queue-list'), queueCount: $('#queue-count'), toast: $('#toast'), description: $('#description'),
};
let state = createStation();
let position = 0;
let playing = false;
let timer = null;
let toastTimer = null;
let audio = null;
let voices = [];
let vizTick = 0;
let vizFrame = 0;

function fmt(seconds) { return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`; }
function currentIndex() { return state.queue.findIndex(track => track.id === state.currentId); }
function updateSlider(slider, ratio) { slider.style.setProperty('--fill', `${Math.max(0, Math.min(100, ratio * 100))}%`); }
function notify(message) {
  els.toast.textContent = message; els.toast.classList.add('visible'); clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.remove('visible'), 2200);
}
function drawWave() {
  const canvas = els.canvas; if (!canvas) return;
  const ctx = canvas.getContext('2d'); const ratio = window.devicePixelRatio || 1; const size = canvas.clientWidth || 168;
  if (canvas.width !== Math.round(size * ratio)) { canvas.width = Math.round(size * ratio); canvas.height = Math.round(size * ratio); }
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const track = currentTrack(state); const hueMap = { chill: 168, neutral: 200, focus: 275, energetic: 28, melancholy: 215, discovery: 140 };
  const hue = hueMap[state.mood] || 200; const primary = `hsl(${hue},80%,66%)`; const secondary = `hsl(${(hue + 44) % 360},68%,79%)`;
  ctx.clearRect(0, 0, size, size); ctx.fillStyle = `hsl(${hue},20%,7%)`; ctx.fillRect(0, 0, size, size);
  let a = 2, b = 3; if ((track.bpm || 120) < 82) { a = 1; b = 2; } else if ((track.bpm || 120) > 115) { a = 3; b = 4; }
  const cx = size / 2, cy = size / 2, amp = size * .43 * (.44 + (track.energy || .5) * .52), delta = vizTick * .18;
  ctx.lineWidth = 1.2; ctx.strokeStyle = primary.replace('hsl(', 'hsla(').replace(')', ',.3)'); ctx.beginPath();
  for (let i = 0; i <= 500; i++) { const t = i / 500 * Math.PI * 2; const x = cx + amp * Math.sin(a * t + delta); const y = cy + amp * Math.sin(b * t); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
  ctx.stroke();
  const speed = ((track.bpm || 120) / 60) * .52; const head = (vizTick * speed) % (Math.PI * 2);
  for (let i = 40; i >= 0; i--) { const t = head - i * .036, fade = 1 - i / 40; ctx.beginPath(); ctx.arc(cx + amp * Math.sin(a * t + delta), cy + amp * Math.sin(b * t), 1.4 + fade * 2.6, 0, Math.PI * 2); ctx.fillStyle = `hsla(${hue},80%,66%,${fade * .88})`; ctx.fill(); }
  ctx.beginPath(); ctx.arc(cx + amp * Math.sin(a * head + delta), cy + amp * Math.sin(b * head), 4.5, 0, Math.PI * 2); ctx.fillStyle = secondary; ctx.fill();
  if (playing && !window.matchMedia('(prefers-reduced-motion: reduce)').matches && !vizFrame) vizFrame = requestAnimationFrame(() => { vizFrame = 0; vizTick += .016; drawWave(); });
}
function render() {
  const track = currentTrack(state); const liked = state.likedIds.includes(track.id);
  els.title.textContent = track.title; els.artist.textContent = track.artist; els.description.textContent = track.note;
  $('#bpm-badge').textContent = `${track.bpm || 96} BPM`;
  $('#energy-badge').textContent = `E ${Math.round((track.energy ?? .5) * 100)}%`;
  $('#valence-badge').textContent = `V ${Math.round((track.valence ?? .5) * 100)}%`;
  $('#station-label').textContent = moodLabels[state.mood]; $('#context-mood').textContent = state.mood;
  $('#context-energy').textContent = `${state.mood === 'energetic' ? '↗' : '→'} energy`;
  els.like.setAttribute('aria-pressed', String(liked)); els.like.setAttribute('aria-label', `${liked ? 'Unlike' : 'Like'} ${track.title}`); els.like.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.8 8.7c0 4.4-8.8 10.1-8.8 10.1S3.2 13.1 3.2 8.7A4.7 4.7 0 0 1 12 6.2a4.7 4.7 0 0 1 8.8 2.5Z"/></svg>`;
  els.seek.max = track.duration; els.seek.value = Math.min(position, track.duration); els.elapsed.textContent = fmt(position); els.duration.textContent = fmt(track.duration);
  els.seek.setAttribute('aria-valuetext', `${fmt(position)} of ${fmt(track.duration)}`); updateSlider(els.seek, position / track.duration);
  els.play.innerHTML = playing ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>' : '<svg class="play-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 4 13 8-13 8z"/></svg>';
  els.play.setAttribute('aria-label', playing ? 'Pause synthesized demo audio' : 'Play synthesized demo audio');
  els.lock.setAttribute('aria-pressed', String(state.vibeLocked)); els.lock.setAttribute('aria-label', state.vibeLocked ? 'Unlock vibe' : "Lock to this song's vibe"); els.lock.title = state.vibeLocked ? 'Unlock vibe' : "Lock to this song's vibe";
  $('#lock-hint').textContent = state.vibeLocked ? 'Vibe locked · mood held' : 'Choose a station mood';
  els.moodButtons.forEach(button => { const selected = button.dataset.mood === state.mood; button.classList.toggle('active', selected); button.setAttribute('aria-pressed', String(selected)); button.disabled = state.vibeLocked && !selected; });
  const upcoming = [...state.queue.slice(currentIndex() + 1), ...state.queue.slice(0, currentIndex())];
  els.queueCount.textContent = String(upcoming.length).padStart(2, '0');
  els.queue.innerHTML = upcoming.map((item, i) => {
    const slot = queueSlots[i % queueSlots.length]; const energy = Math.round((item.energy ?? .5) * 100), valence = Math.round((item.valence ?? .5) * 100);
    return `<li class="queue-item" style="border-left-color:${slot.color}"><div class="queue-head"><span class="queue-title">${item.artist} – ${item.title}</span><span class="queue-slot" style="background:${slot.bg};color:${slot.color}">${slot.name}</span></div><div class="queue-reason">${slot.reason}</div><div class="queue-metrics"><span>E</span><div class="qb"><div class="qb-fill" style="width:${energy}%"></div></div><span>V</span><div class="qb"><div class="qb-fill valence" style="width:${valence}%"></div></div><span class="queue-bpm">${item.bpm || 96} BPM</span></div></li>`;
  }).join('');
  const vol = Number($('#volume-wide').value); $('#volume-value').textContent = `${vol}%`; updateSlider($('#volume-wide'), vol / 100);
  drawWave();
}
function stopVoices() { voices.forEach(voice => { try { voice.stop(); } catch {} }); voices = []; }
function startAudio() {
  if (!audio) { const Ctx = window.AudioContext || window.webkitAudioContext; if (!Ctx) { notify('Synthesized audio is not supported in this browser.'); return; } audio = new Ctx(); }
  audio.resume(); stopVoices(); const track = currentTrack(state); const gain = audio.createGain(); gain.gain.value = Number($('#volume-wide').value) / 100 * .055; gain.connect(audio.destination);
  [1, 1.25, 1.5, 1.25].forEach((ratio, i) => { const osc = audio.createOscillator(), voiceGain = audio.createGain(); osc.type = i ? 'triangle' : 'sine'; osc.frequency.value = track.tone * ratio; voiceGain.gain.value = i ? .11 : .56; osc.connect(voiceGain).connect(gain); osc.start(); voices.push(osc); });
  voices.push({ stop: () => { gain.gain.setTargetAtTime(0, audio.currentTime, .08); setTimeout(() => { try { gain.disconnect(); } catch {} }, 400); } });
}
function setPlaying(value) { playing = value; if (playing) { startAudio(); clearInterval(timer); cancelAnimationFrame(vizFrame); vizFrame = 0; timer = setInterval(() => { if (!playing) return; position++; if (position >= currentTrack(state).duration) { advance(); return; } render(); }, 1000); } else { clearInterval(timer); cancelAnimationFrame(vizFrame); vizFrame = 0; stopVoices(); } render(); }
function advance() { state = nextTrack(state); position = 0; if (playing) startAudio(); render(); }
function changeMood(mood) {
  if (state.vibeLocked) { notify('Vibe lock is holding the current mood. Turn it off to change.'); return; }
  if (!MOODS.includes(mood)) return; state = setMood(state, mood); position = 0; if (playing) startAudio(); render(); notify(`Tuned to ${mood}.`);
}
function createNewMix() { state = newMix(state); position = 0; if (playing) startAudio(); render(); notify(`A fresh ${state.mood} mix is ready.`); }

els.moodButtons.forEach(button => button.addEventListener('click', () => changeMood(button.dataset.mood)));
els.play.addEventListener('click', () => setPlaying(!playing)); $('#next-button').addEventListener('click', advance);
$('#replay-button').addEventListener('click', () => { position = 0; if (!playing) setPlaying(true); else { startAudio(); render(); } });
$('#new-mix').addEventListener('click', createNewMix);
els.like.addEventListener('click', () => { state = toggleLike(state); render(); notify(state.likedIds.includes(state.currentId) ? 'Saved to your liked tracks.' : 'Removed from liked tracks.'); });
els.lock.addEventListener('click', () => { state = toggleVibeLock(state); render(); notify(state.vibeLocked ? 'Vibe lock on. The current mood is held.' : 'Vibe lock off. Choose another mood anytime.'); });
els.seek.addEventListener('input', event => { position = Number(event.target.value); render(); });
$('#volume-wide').addEventListener('input', () => { render(); if (audio && playing) startAudio(); });
window.addEventListener('resize', drawWave); render();

