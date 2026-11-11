export const MOODS = ['neutral', 'chill', 'energetic', 'focus', 'melancholy', 'discovery'];

const TRACKS = {
  neutral: [
    { id: 'open-window', title: 'Open Window', artist: 'Mira Sol', duration: 224, tone: 196, color: '#5a8d91', energy: .46, valence: .53, bpm: 88, note: 'soft keys · even light' },
    { id: 'quiet-form', title: 'Quiet Form', artist: 'Fieldwork', duration: 208, tone: 207.65, color: '#718b9a', energy: .48, valence: .51, bpm: 92, note: 'gentle pulse · level ground' },
    { id: 'between-days', title: 'Between Days', artist: 'Noah Vale', duration: 237, tone: 185, color: '#789a8b', energy: .45, valence: .55, bpm: 86, note: 'warm strings · unhurried' },
    { id: 'evening-shape', title: 'Evening Shape', artist: 'Mira Sol', duration: 215, tone: 220, color: '#658395', energy: .49, valence: .5, bpm: 90, note: 'rounded notes · easy drift' },
  ],
  chill: [
    { id: 'glass-tide', title: 'Glass Tide', artist: 'Mira Sol', duration: 228, tone: 174.61, color: '#b8d9c8', energy: .34, valence: .61, bpm: 68, note: 'soft piano · warm air' },
    { id: 'slow-orbit', title: 'Slow Orbit', artist: 'Fieldwork', duration: 196, tone: 146.83, color: '#8caab2', energy: .29, valence: .49, bpm: 72, note: 'tape haze · low tide' },
    { id: 'blue-hour', title: 'Blue Hour', artist: 'Noah Vale', duration: 243, tone: 196, color: '#c7a9b7', energy: .3, valence: .42, bpm: 74, note: 'distant keys · dusk' },
    { id: 'still-water', title: 'Still Water', artist: 'Mira Sol', duration: 211, tone: 164.81, color: '#b5c6a2', energy: .32, valence: .57, bpm: 70, note: 'slow pulse · open space' },
  ],
  focus: [
    { id: 'soft-machines', title: 'Soft Machines', artist: 'Juniper North', duration: 254, tone: 220, color: '#a3c6bd', energy: .55, valence: .54, bpm: 98, note: 'steady motion · no edges' },
    { id: 'paper-sky', title: 'Paper Sky', artist: 'Cedar Room', duration: 217, tone: 246.94, color: '#c8bda0', energy: .5, valence: .59, bpm: 102, note: 'muted rhythm · clear desk' },
    { id: 'parallel-lines', title: 'Parallel Lines', artist: 'Juniper North', duration: 231, tone: 207.65, color: '#9faebd', note: 'quiet pattern · level light' },
    { id: 'small-hours', title: 'Small Hours', artist: 'Cedar Room', duration: 203, tone: 233.08, color: '#c2a9a1', note: 'hushed synth · deep work' },
  ],
  melancholy: [
    { id: 'fading-blue', title: 'Fading Blue', artist: 'Noah Vale', duration: 241, tone: 164.81, color: '#8295b8', energy: .31, valence: .29, bpm: 76, note: 'distant piano · after rain' },
    { id: 'last-light', title: 'Last Light', artist: 'Ari June', duration: 223, tone: 174.61, color: '#8793a7', energy: .33, valence: .34, bpm: 80, note: 'hushed strings · soft gray' },
    { id: 'small-blue', title: 'Small Blue', artist: 'Noah Vale', duration: 251, tone: 155.56, color: '#7885ad', energy: .28, valence: .3, bpm: 72, note: 'low keys · long shadows' },
    { id: 'far-shore', title: 'Far Shore', artist: 'Ari June', duration: 230, tone: 185, color: '#93a0b5', energy: .35, valence: .38, bpm: 78, note: 'slow guitar · the quiet end' },
  ],
  discovery: [
    { id: 'green-comet', title: 'Green Comet', artist: 'Lumen Park', duration: 221, tone: 261.63, color: '#b6c58f', note: 'bright guitar · new paths' },
    { id: 'borrowed-light', title: 'Borrowed Light', artist: 'Ari June', duration: 239, tone: 293.66, color: '#d0a982', note: 'loose drums · first listen' },
    { id: 'other-weather', title: 'Other Weather', artist: 'Lumen Park', duration: 208, tone: 277.18, color: '#93b7b6', note: 'strange chords · open road' },
    { id: 'wild-fig', title: 'Wild Fig', artist: 'Ari June', duration: 226, tone: 311.13, color: '#c6a7ac', note: 'warm bass · left turn' },
  ],
  energetic: [
    { id: 'sunroom-run', title: 'Sunroom Run', artist: 'Golden Form', duration: 218, tone: 329.63, color: '#d4ae72', energy: .74, valence: .71, bpm: 122, note: 'bright pulse · easy lift' },
    { id: 'open-circuit', title: 'Open Circuit', artist: 'Nico West', duration: 232, tone: 349.23, color: '#85b5a4', note: 'rounded drums · forward' },
    { id: 'golden-mile', title: 'Golden Mile', artist: 'Golden Form', duration: 207, tone: 311.13, color: '#c69774', note: 'sunlit bass · long stride' },
    { id: 'light-years', title: 'Light Years', artist: 'Nico West', duration: 244, tone: 369.99, color: '#94a9c4', note: 'wide synths · lift off' },
  ],
};

export function tracksForMood(mood) { return [...(TRACKS[mood] || TRACKS.chill)]; }
export function createStation(mood = 'chill') {
  const queue = tracksForMood(mood);
  return { mood, queue, currentId: queue[0].id, likedIds: [], vibeLocked: false, mix: 0 };
}
export function setMood(state, mood) {
  if (state.vibeLocked) return state;
  if (!MOODS.includes(mood)) return state;
  const queue = tracksForMood(mood);
  return { ...state, mood, queue, currentId: queue[0].id, mix: 0 };
}
export function nextTrack(state) {
  const index = state.queue.findIndex(track => track.id === state.currentId);
  return { ...state, currentId: state.queue[(index + 1) % state.queue.length].id };
}
export function toggleLike(state, id = state.currentId) {
  const likedIds = state.likedIds.includes(id) ? state.likedIds.filter(item => item !== id) : [...state.likedIds, id];
  return { ...state, likedIds };
}
export function toggleVibeLock(state) { return { ...state, vibeLocked: !state.vibeLocked }; }
export function newMix(state) {
  const queue = [...state.queue.slice(1), state.queue[0]];
  return { ...state, queue, currentId: queue[0].id, mix: state.mix + 1 };
}
export function currentTrack(state) { return state.queue.find(track => track.id === state.currentId) || state.queue[0]; }
