import React, { useRef, useEffect, useMemo } from "react";

const MOOD_HUES = { energetic: 28, melancholy: 215, chill: 168, focus: 275, neutral: 210, discovery: 140 };

function getColors(song) {
  const bh = MOOD_HUES[song.mood] ?? 200;
  const h = Math.round(bh + ((song.valence || 0.5) - 0.5) * 20);
  const sa = Math.round(66 + (song.energy || 0.5) * 28);
  return {
    primary: `hsl(${h},${sa}%,63%)`,
    secondary: `hsl(${(h + 44) % 360},${Math.max(sa - 13, 42)}%,79%)`,
    bg: `hsl(${h},20%,7%)`,
  };
}

function hslToHsla(hsl, a) {
  return hsl.replace("hsl(", "hsla(").replace(")", `,${a})`);
}

function getHue(hsl) {
  return parseInt(hsl.match(/hsl\((\d+)/)?.[1] ?? "200");
}

const _instanceGeo = new Map();
let _nextInstanceId = 0;
const DEFAULT_SIZE = 210;

function drawOrbit(ctx, song, tick, colors, audioRms, geo) {
  let a = 2, b = 3;
  if (song.bpm < 82) { a = 1; b = 2; }
  else if (song.bpm > 115) { a = 3; b = 4; }
  const rmsMod = (audioRms || 0.5) * 0.25;
  const amp = 72 * (0.44 + ((song.energy || 0.5) + rmsMod) * 0.52);
  const delta = tick * 0.18;
  const { cx, cy, size } = geo;

  ctx.lineWidth = 1.2;
  ctx.strokeStyle = hslToHsla(colors.primary, 0.3);
  ctx.beginPath();
  for (let i = 0; i <= 500; i++) {
    const tt = (i / 500) * Math.PI * 2;
    const x = cx + amp * Math.sin(a * tt + delta);
    const y = cy + amp * Math.sin(b * tt);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.stroke();

  const sp = ((song.bpm || 120) / 60) * 0.52;
  const ht = (tick * sp) % (Math.PI * 2);
  for (let i = 40; i >= 0; i--) {
    const ta = ht - i * 0.036;
    const f = 1 - i / 40;
    ctx.beginPath();
    ctx.arc(
      cx + amp * Math.sin(a * ta + delta),
      cy + amp * Math.sin(b * ta),
      1.4 + f * 2.6, 0, Math.PI * 2
    );
    ctx.fillStyle = hslToHsla(colors.primary, f * 0.88);
    ctx.fill();
  }
  ctx.beginPath();
  ctx.arc(
    cx + amp * Math.sin(a * ht + delta),
    cy + amp * Math.sin(b * ht),
    4.5, 0, Math.PI * 2
  );
  ctx.fillStyle = colors.secondary;
  ctx.fill();
}

function drawPulse(ctx, song, tick, colors, audioRms, geo) {
  const N = 48;
  const baseR = 27;
  const rmsMod = (audioRms || 0.5) * 0.6;
  const maxE = 60 * (0.28 + ((song.energy || 0.5) + rmsMod * 0.4) * 0.72);
  const sp = ((song.bpm || 120) / 60) * 1.65;
  const bh = getHue(colors.primary);
  const { cx, cy } = geo;

  for (let i = 0; i < N; i++) {
    const angle = (i / N) * Math.PI * 2 - Math.PI / 2;
    const seed = Math.sin(i * 127.1 + 3.4) * 0.5 + 0.5;
    const wave = Math.sin(tick * sp + i * (Math.PI * 2 / N) * 1.5) * 0.5 + 0.5;
    const rmsResp = audioRms != null ? audioRms * 0.4 : 0.0;
    const h = baseR + (seed * 0.2 + wave * 0.4 + rmsResp * 0.4) * maxE;
    const fh = (bh + (i / N) * 58) % 360;
    ctx.strokeStyle = `hsla(${Math.round(fh)},73%,66%,0.9)`;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(angle) * baseR, cy + Math.sin(angle) * baseR);
    ctx.lineTo(cx + Math.cos(angle) * h, cy + Math.sin(angle) * h);
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.arc(cx, cy, baseR * 0.58, 0, Math.PI * 2);
  ctx.fillStyle = hslToHsla(colors.primary, 0.18);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(cx, cy, baseR * 0.58, 0, Math.PI * 2);
  ctx.strokeStyle = hslToHsla(colors.primary, 0.55);
  ctx.lineWidth = 1;
  ctx.stroke();
}

function drawFloat(ctx, song, tick, colors, audioRms, geo) {
  const rmsMod = (audioRms || 0.5) * 0.3;
  const N = 2 + Math.round(((song.energy || 0.5) + rmsMod) * 4);
  const sp = ((song.bpm || 120) / 60) * 0.38;
  const bh = getHue(colors.primary);
  const { cx, cy } = geo;

  ctx.globalCompositeOperation = "lighter";
  for (let i = 0; i < N; i++) {
    const phase = (i / N) * Math.PI * 2;
    const oR = (0.1 + (i / N) * 0.28) * 96;
    const angle = tick * sp * (1 + i * 0.2) + phase;
    const x = cx + Math.cos(angle) * oR;
    const y = cy + Math.sin(angle) * oR * 0.7;
    const r = Math.max((0.28 - i * 0.034) * 96, 9);
    const oh = (bh + i * 42) % 360;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, `hsla(${oh},82%,70%,0.46)`);
    g.addColorStop(0.55, `hsla(${oh},72%,62%,0.14)`);
    g.addColorStop(1, `hsla(${oh},62%,56%,0)`);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = g;
    ctx.fill();
  }
  ctx.globalCompositeOperation = "source-over";
}

const DRAW_FNS = [drawOrbit, drawPulse, drawFloat];

export default function WaveformViz({ song, styleIndex = 0, audioRms, size }) {
  const instanceId = useRef(++_nextInstanceId);
  const canvasRef = useRef(null);
  const tickRef = useRef(0);
  const rafRef = useRef(null);
  const prevSongIdRef = useRef(null);

  const geo = useMemo(() => {
    const s = size && size > 0 ? size : DEFAULT_SIZE;
    return { size: s, cx: s / 2, cy: s / 2 };
  }, [size]);

  useEffect(() => {
    _instanceGeo.set(instanceId.current, geo);
    return () => _instanceGeo.delete(instanceId.current);
  }, [geo]);

  useEffect(() => {
    if (song?.id !== prevSongIdRef.current) {
      tickRef.current = 0;
      prevSongIdRef.current = song?.id;
    }
  }, [song?.id]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = geo.size * dpr;
    canvas.height = geo.size * dpr;
    ctx.scale(dpr, dpr);

    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const draw = () => {
      const s = song || { energy: 0.5, valence: 0.5, bpm: 120, mood: "neutral" };
      const c = getColors(s);
      ctx.clearRect(0, 0, geo.size, geo.size);
      ctx.fillStyle = c.bg;
      ctx.fillRect(0, 0, geo.size, geo.size);
      const instGeo = _instanceGeo.get(instanceId.current) || geo;
      DRAW_FNS[styleIndex](ctx, s, tickRef.current, c, audioRms, instGeo);
      tickRef.current += 0.016;
      if (!prefersReducedMotion) {
        rafRef.current = requestAnimationFrame(draw);
      }
    };

    draw();
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [song, styleIndex, geo, audioRms]);

  return (
    <canvas ref={canvasRef} width={geo.size} height={geo.size} className="rounded-xl block" style={{ width: geo.size, height: geo.size }} />
  );
}
