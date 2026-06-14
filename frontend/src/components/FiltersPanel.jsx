import React, { useState, useEffect, useCallback, useRef } from "react";
import { Filter, X, ChevronDown, RotateCcw } from "lucide-react";
import * as api from "../api";

function FiltersPanel({ addToast, onFiltersChange }) {
  const [filters, setFilters] = useState({
    folders: [], mtime_min_days: null, mtime_max_days: null, artist_mode: null, artists: [],
  });
  const [subfolders, setSubfolders] = useState([]);
  const [folderTicks, setFolderTicks] = useState([]);
  const [sliderMaxDays, setSliderMaxDays] = useState(1825);
  const [mtimeMin, setMtimeMin] = useState(null);
  const [mtimeMax, setMtimeMax] = useState(null);
  const [artistInput, setArtistInput] = useState("");
  const [showFolder, setShowFolder] = useState(true);
  const [showRecency, setShowRecency] = useState(true);
  const [showArtist, setShowArtist] = useState(true);
  const [dragState, setDragState] = useState({ active: false, fromIdx: 0, toIdx: 0, mode: false });
  const [sliderDrag, setSliderDrag] = useState({ active: false, handle: null });
  const [tooltipValue, setTooltipValue] = useState(null);
  const [hoveredTick, setHoveredTick] = useState(null);
  const debounceRef = useRef(null);
  const filtersRef = useRef(filters);
  const dragActiveRef = useRef(false);
  const dragValuesRef = useRef({ min: 0, max: 0 });
  const trackRef = useRef(null);
  const activeTrackRef = useRef(null);
  filtersRef.current = filters;

  useEffect(() => {
    api.fetchSubfolders().then((d) => setSubfolders(d.subfolders || [])).catch(() => {});
    api.fetchFolderTicks().then((d) => {
      setFolderTicks(d.ticks || []);
      setSliderMaxDays(d.slider_max_days || 1825);
    }).catch(() => {});
    api.fetchRecommendationFilters().then((d) => {
      setFilters(d);
      setMtimeMin(d.mtime_min_days);
      setMtimeMax(d.mtime_max_days);
      setArtistInput((d.artists || []).join(", "));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  const doSave = useCallback((updated) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        await api.setRecommendationFilters(updated);
        setFilters(updated);
        onFiltersChange?.(updated);
      } catch (e) {
        addToast?.("Failed to save filters", "error");
      }
    }, 300);
  }, [addToast, onFiltersChange]);

  // ── Folder grid: click-drag-select ──────────────────────

  const applyDragRange = useCallback((fromIdx, toIdx, mode) => {
    const lo = Math.min(fromIdx, toIdx);
    const hi = Math.max(fromIdx, toIdx);
    const cur = new Set(filtersRef.current.folders || []);
    for (let i = lo; i <= hi; i++) {
      const name = subfolders[i]?.name;
      if (!name) continue;
      if (mode) cur.add(name);
      else cur.delete(name);
    }
    const next = Array.from(cur).sort();
    const updated = { ...filtersRef.current, folders: next };
    setFilters(updated);
    doSave(updated);
  }, [subfolders, doSave]);

  const handleGridMouseDown = useCallback((e, idx) => {
    e.preventDefault();
    const name = subfolders[idx]?.name;
    if (!name) return;
    const cur = filtersRef.current.folders || [];
    const mode = !cur.includes(name);
    dragActiveRef.current = true;
    setDragState({ active: true, fromIdx: idx, toIdx: idx, mode });
  }, [subfolders]);

  const handleGridMouseEnter = useCallback((idx) => {
    if (!dragActiveRef.current) return;
    setDragState(prev => ({ ...prev, toIdx: idx }));
  }, []);

  useEffect(() => {
    if (!dragState.active) return;
    const handleUp = () => {
      dragActiveRef.current = false;
      applyDragRange(dragState.fromIdx, dragState.toIdx, dragState.mode);
      setDragState({ active: false, fromIdx: 0, toIdx: 0, mode: false });
    };
    window.addEventListener("mouseup", handleUp);
    return () => window.removeEventListener("mouseup", handleUp);
  }, [dragState, applyDragRange]);

  const handleSingleToggle = useCallback((name) => {
    const cur = filtersRef.current.folders || [];
    const next = cur.includes(name) ? cur.filter((f) => f !== name) : [...cur, name];
    const updated = { ...filtersRef.current, folders: next };
    setFilters(updated);
    doSave(updated);
  }, [doSave]);

  // ── Seek-bar slider helpers ────────────────────────────

  const valueFromEvent = useCallback((e, trackEl) => {
    if (!trackEl) return 0;
    const rect = trackEl.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    return Math.round(pct * sliderMaxDays);
  }, [sliderMaxDays]);

  const handleTrackDown = useCallback((e, handle) => {
    activeTrackRef.current = e.currentTarget;
    const v = valueFromEvent(e, activeTrackRef.current);
    const min = mtimeMin ?? 0;
    const max = mtimeMax ?? sliderMaxDays;
    let newMin = min, newMax = max;
    if (handle === "min") { newMin = Math.min(v, max); }
    else { newMax = Math.max(v, min); }
    dragValuesRef.current = { min: newMin, max: newMax };
    setMtimeMin(newMin);
    setMtimeMax(newMax);
    setSliderDrag({ active: true, handle });
    setTooltipValue(handle === "min" ? newMin : newMax);
    debouncedMtimeSave(newMin, newMax);
  }, [mtimeMin, mtimeMax, sliderMaxDays, valueFromEvent]);

  const debouncedMtimeSave = useCallback((min, max) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const saveMin = min === 0 ? null : min;
      const saveMax = max >= sliderMaxDays ? null : max;
      const updated = { ...filtersRef.current, mtime_min_days: saveMin, mtime_max_days: saveMax };
      setFilters(updated);
      doSave(updated);
    }, 150);
  }, [sliderMaxDays, doSave]);

  useEffect(() => {
    if (!sliderDrag.active) return;
    const handle = sliderDrag.handle;
    const handleMove = (e) => {
      const el = activeTrackRef.current;
      if (!el) return;
      const v = valueFromEvent(e, el);
      const vals = dragValuesRef.current;
      let newMin = vals.min, newMax = vals.max;
      if (handle === "min") { newMin = Math.min(v, vals.max); }
      else { newMax = Math.max(v, vals.min); }
      dragValuesRef.current = { min: newMin, max: newMax };
      setMtimeMin(newMin);
      setMtimeMax(newMax);
      setTooltipValue(handle === "min" ? newMin : newMax);
    };
    const handleUp = (e) => {
      const el = activeTrackRef.current;
      if (!el) return;
      const v = valueFromEvent(e, el);
      const vals = dragValuesRef.current;
      let newMin = vals.min, newMax = vals.max;
      if (handle === "min") { newMin = Math.min(v, vals.max); }
      else { newMax = Math.max(v, vals.min); }
      dragValuesRef.current = { min: newMin, max: newMax };
      debouncedMtimeSave(newMin, newMax);
      setSliderDrag({ active: false, handle: null });
      setTooltipValue(null);
    };
    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [sliderDrag.active, sliderDrag.handle, sliderMaxDays, valueFromEvent, debouncedMtimeSave]);

  const handleTrackHover = useCallback((e) => {
    if (!sliderDrag.active && trackRef.current) {
      const v = valueFromEvent(e, trackRef.current);
      setTooltipValue(v);
    }
  }, [sliderDrag.active, valueFromEvent]);

  const handleTrackLeave = useCallback(() => {
    if (!sliderDrag.active) setTooltipValue(null);
  }, [sliderDrag.active]);

  // ── Artist handlers ─────────────────────────────────────

  const handleArtistBlur = () => {
    const artists = artistInput.split(",").map((a) => a.trim()).filter(Boolean);
    const mode = artists.length > 0 ? (filters.artist_mode || "include") : null;
    const updated = { ...filters, artists, artist_mode: mode };
    setFilters(updated);
    doSave(updated);
  };

  const setArtistMode = (mode) => {
    const updated = { ...filters, artist_mode: mode };
    setFilters(updated);
    doSave(updated);
  };

  // ── Clear all ───────────────────────────────────────────

  const clearAll = async () => {
    try {
      await api.clearRecommendationFilters();
      const def = { folders: [], mtime_min_days: null, mtime_max_days: null, artist_mode: null, artists: [] };
      setFilters(def);
      setMtimeMin(null);
      setMtimeMax(null);
      setArtistInput("");
      onFiltersChange?.(def);
      addToast?.("All filters cleared", "success");
    } catch (e) {
      addToast?.("Failed to clear filters", "error");
    }
  };

  const hasActive = filters.folders?.length > 0 || filters.mtime_min_days != null || filters.mtime_max_days != null || filters.artist_mode;

  const recMin = mtimeMin ?? 0;
  const recMax = mtimeMax ?? sliderMaxDays;

  // ── Tick computation ────────────────────────────────────

  let ticks = [];
  for (const t of folderTicks) {
    const num = parseInt(t.folder.replace("song ", ""), 10);
    if (!isNaN(num) && num % 5 === 0) {
      ticks.push(t);
    }
  }

  return (
    <div style={{ userSelect: "none" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 14px 4px" }}>
        <span style={{ fontSize: "12px", fontWeight: 500, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: "6px" }}>
          <Filter size={12} /> Filters
          {hasActive && <span style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "4px", background: "rgba(72,187,120,0.15)", color: "#4ade80" }}>active</span>}
        </span>
        {hasActive && (
          <button onClick={clearAll} style={{ fontSize: "10px", padding: "3px 8px", borderRadius: "4px", background: "#1e0808", border: "1px solid #3a1010", color: "var(--danger)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: "4px" }}>
            <RotateCcw size={10} /> Clear all
          </button>
        )}
      </div>

      {/* ── Folder multi-select — 5-col grid + drag ─────── */}
      <div className="set-row" style={{ flexDirection: "column", alignItems: "stretch", borderBottom: "1px solid var(--border-subtle)" }}>
        <div className="set-hdr" onClick={() => setShowFolder(!showFolder)} style={{ padding: "8px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ color: "var(--text-primary)", fontSize: "12px", fontWeight: 500 }}>Folders</span>
          {filters.folders?.length > 0 && (
            <span style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "4px", background: "rgba(72,187,120,0.15)", color: "#4ade80" }}>{filters.folders.length}</span>
          )}
          <div style={{ flex: 1 }} />
          <ChevronDown size={12} style={{ color: "var(--text-muted)", transform: showFolder ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {showFolder && (
          <div style={{ padding: "0 14px 10px" }}>
            <div style={{ fontSize: "10px", color: "var(--text-muted)", marginBottom: "6px" }}>Select folders. Click to toggle; click-drag to select a range.</div>
            {subfolders.length === 0 ? (
              <div style={{ fontSize: "11px", color: "var(--text-muted)", padding: "4px 0" }}>No subfolders found</div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "3px", marginBottom: "6px" }}>
                {subfolders.map((sf, idx) => {
                  const sel = (filters.folders || []).includes(sf.name);
                  return (
                    <div key={sf.name}
                      onMouseDown={(e) => handleGridMouseDown(e, idx)}
                      onMouseEnter={() => handleGridMouseEnter(idx)}
                      onClick={(e) => { if (!dragState.active) handleSingleToggle(sf.name); }}
                      style={{
                        padding: "5px 2px", borderRadius: "5px", fontSize: "10px", cursor: "pointer", textAlign: "center",
                        background: sel ? "rgba(72,187,120,0.12)" : "var(--bg-elevated)",
                        border: `1px solid ${sel ? "rgba(72,187,120,0.3)" : "var(--border)"}`,
                        color: sel ? "#4ade80" : "var(--text-secondary)",
                        transition: "background 0.1s",
                      }}>
                      <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sf.name}</div>
                      <div style={{ fontSize: "8px", opacity: 0.5 }}>{sf.count}</div>
                    </div>
                  );
                })}
              </div>
            )}
            {filters.folders?.length > 0 && (
              <button onClick={() => { const updated = { ...filters, folders: [] }; setFilters(updated); doSave(updated); }}
                style={{ fontSize: "10px", padding: "3px 8px", borderRadius: "4px", background: "#1e0808", border: "1px solid #3a1010", color: "var(--danger)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: "4px" }}>
                <X size={10} /> Clear folders
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Recency — seek-bar dual sliders + ticks ─────── */}
      <div className="set-row" style={{ flexDirection: "column", alignItems: "stretch", borderBottom: "1px solid var(--border-subtle)" }}>
        <div className="set-hdr" onClick={() => setShowRecency(!showRecency)} style={{ padding: "8px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ color: "var(--text-primary)", fontSize: "12px", fontWeight: 500 }}>Recency</span>
          {(filters.mtime_min_days != null || filters.mtime_max_days != null) && (
            <span style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "4px", background: "rgba(72,187,120,0.15)", color: "#4ade80" }}>
              {filters.mtime_min_days ?? 0}d – {filters.mtime_max_days ?? "∞"}d
            </span>
          )}
          <div style={{ flex: 1 }} />
          <ChevronDown size={12} style={{ color: "var(--text-muted)", transform: showRecency ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {showRecency && (
          <div style={{ padding: "0 14px 14px" }}>
            <div style={{ fontSize: "10px", color: "var(--text-muted)", marginBottom: "10px" }}>
              Restrict by song age (file modification date). Hover a tick mark to see folder dates.
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>

              {/* Min age slider */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: "var(--text-muted)", marginBottom: "4px" }}>
                  <span>Min age (oldest allowed)</span>
                  <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{recMin}d</span>
                </div>
                <div style={{ position: "relative", height: "16px" }}
                  onMouseMove={handleTrackHover} onMouseLeave={handleTrackLeave}>
                  <div ref={trackRef}
                    onMouseDown={(e) => handleTrackDown(e, "min")}
                    style={{ position: "absolute", top: "50%", height: "3px", transform: "translateY(-50%)", left: 0, right: 0, borderRadius: "999px", background: "var(--border)", cursor: "pointer" }}>
                    {/* Fill bar */}
                    <div style={{ position: "absolute", height: "100%", borderRadius: "999px", width: `${(recMin / sliderMaxDays) * 100}%`, background: "linear-gradient(90deg, #3ab8b0, var(--accent))" }}>
                      {/* Ball */}
                      <div style={{
                        position: "absolute", right: "-6px", top: "50%", transform: "translateY(-50%)",
                        width: sliderDrag.active && sliderDrag.handle === "min" ? "14px" : "10px",
                        height: sliderDrag.active && sliderDrag.handle === "min" ? "14px" : "10px",
                        borderRadius: "50%", background: "var(--accent)",
                        boxShadow: sliderDrag.active && sliderDrag.handle === "min" ? "0 0 12px rgba(78,204,196,0.5)" : "none",
                        transition: "width 0.1s, height 0.1s, box-shadow 0.1s, opacity 0.2s",
                        opacity: 1,
                      }} />
                    </div>
                    {/* Ticks */}
                    {ticks.map((t) => {
                      const pct = (t.days_ago / sliderMaxDays) * 100;
                      const isHovered = hoveredTick === t.folder;
                      return (
                        <div key={t.folder} style={{ position: "absolute", left: `${pct}%`, top: "-4px", transform: "translateX(-50%)" }}
                          onMouseEnter={() => setHoveredTick(t.folder)}
                          onMouseLeave={() => setHoveredTick(null)}>
                          <div style={{ width: "1px", height: "11px", background: "var(--border-strong)", opacity: 0.35 }} />
                          {isHovered && (
                            <div style={{
                              position: "absolute", bottom: "14px", left: "50%", transform: "translateX(-50%)",
                              padding: "2px 6px", borderRadius: "4px", fontSize: "9px", whiteSpace: "nowrap",
                              background: "var(--bg-elevated)", border: "1px solid var(--border)",
                              color: "var(--text-secondary)", pointerEvents: "none", zIndex: 10,
                            }}>
                              {t.earliest_date} • {t.folder}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {/* Drag tooltip */}
                  {sliderDrag.active && sliderDrag.handle === "min" && tooltipValue != null && (
                    <div style={{
                      position: "absolute", top: "-28px", left: `${(tooltipValue / sliderMaxDays) * 100}%`, transform: "translateX(-50%)",
                      padding: "2px 8px", borderRadius: "4px", fontSize: "10px", fontVariantNumeric: "tabular-nums",
                      background: "var(--bg-elevated)", border: "1px solid var(--border)",
                      color: "var(--accent)", pointerEvents: "none", whiteSpace: "nowrap",
                      boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
                    }}>
                      {tooltipValue >= sliderMaxDays ? "∞" : tooltipValue + "d"}
                    </div>
                  )}
                  {/* Hover tooltip (not dragging) */}
                  {!sliderDrag.active && tooltipValue != null && (
                    <div style={{
                      position: "absolute", top: "-28px", left: `${(tooltipValue / sliderMaxDays) * 100}%`, transform: "translateX(-50%)",
                      padding: "2px 8px", borderRadius: "4px", fontSize: "10px", fontVariantNumeric: "tabular-nums",
                      background: "var(--bg-elevated)", border: "1px solid var(--border)",
                      color: "var(--text-muted)", pointerEvents: "none", whiteSpace: "nowrap",
                      boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
                    }}>
                      {tooltipValue >= sliderMaxDays ? "∞" : tooltipValue + "d"}
                    </div>
                  )}
                </div>
              </div>

              {/* Max age slider */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "10px", color: "var(--text-muted)", marginBottom: "4px" }}>
                  <span>Max age (newest allowed)</span>
                  <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{recMax >= sliderMaxDays ? "∞" : recMax + "d"}</span>
                </div>
                <div style={{ position: "relative", height: "16px" }}
                  onMouseMove={handleTrackHover} onMouseLeave={handleTrackLeave}>
                  <div data-track onMouseDown={(e) => handleTrackDown(e, "max")}
                    style={{ position: "absolute", top: "50%", height: "3px", transform: "translateY(-50%)", left: 0, right: 0, borderRadius: "999px", background: "var(--border)", cursor: "pointer" }}>
                    <div style={{ position: "absolute", height: "100%", borderRadius: "999px", width: `${(recMax / sliderMaxDays) * 100}%`, background: "linear-gradient(90deg, #3ab8b0, var(--accent))" }}>
                      <div style={{
                        position: "absolute", right: "-6px", top: "50%", transform: "translateY(-50%)",
                        width: sliderDrag.active && sliderDrag.handle === "max" ? "14px" : "10px",
                        height: sliderDrag.active && sliderDrag.handle === "max" ? "14px" : "10px",
                        borderRadius: "50%", background: "var(--accent)",
                        boxShadow: sliderDrag.active && sliderDrag.handle === "max" ? "0 0 12px rgba(78,204,196,0.5)" : "none",
                        transition: "width 0.1s, height 0.1s, box-shadow 0.1s, opacity 0.2s",
                        opacity: 1,
                      }} />
                    </div>
                    {/* Ticks (same positions as min) */}
                    {ticks.map((t) => {
                      const pct = (t.days_ago / sliderMaxDays) * 100;
                      const isHovered = hoveredTick === t.folder;
                      return (
                        <div key={t.folder} style={{ position: "absolute", left: `${pct}%`, top: "-4px", transform: "translateX(-50%)" }}
                          onMouseEnter={() => setHoveredTick(t.folder)}
                          onMouseLeave={() => setHoveredTick(null)}>
                          <div style={{ width: "1px", height: "11px", background: "var(--border-strong)", opacity: 0.35 }} />
                          {isHovered && (
                            <div style={{
                              position: "absolute", bottom: "14px", left: "50%", transform: "translateX(-50%)",
                              padding: "2px 6px", borderRadius: "4px", fontSize: "9px", whiteSpace: "nowrap",
                              background: "var(--bg-elevated)", border: "1px solid var(--border)",
                              color: "var(--text-secondary)", pointerEvents: "none", zIndex: 10,
                            }}>
                              {t.earliest_date} • {t.folder}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {sliderDrag.active && sliderDrag.handle === "max" && tooltipValue != null && (
                    <div style={{
                      position: "absolute", top: "-28px", left: `${(tooltipValue / sliderMaxDays) * 100}%`, transform: "translateX(-50%)",
                      padding: "2px 8px", borderRadius: "4px", fontSize: "10px", fontVariantNumeric: "tabular-nums",
                      background: "var(--bg-elevated)", border: "1px solid var(--border)",
                      color: "var(--accent)", pointerEvents: "none", whiteSpace: "nowrap",
                      boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
                    }}>
                      {tooltipValue >= sliderMaxDays ? "∞" : tooltipValue + "d"}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Artist allowlist/denylist ───────────────────── */}
      <div className="set-row" style={{ flexDirection: "column", alignItems: "stretch", borderBottom: "none" }}>
        <div className="set-hdr" onClick={() => setShowArtist(!showArtist)} style={{ padding: "8px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ color: "var(--text-primary)", fontSize: "12px", fontWeight: 500 }}>Artists</span>
          {filters.artist_mode && (
            <span style={{ fontSize: "10px", padding: "1px 6px", borderRadius: "4px", background: "rgba(72,187,120,0.15)", color: "#4ade80" }}>
              {filters.artist_mode === "include" ? "only" : "exclude"} ({filters.artists?.length})
            </span>
          )}
          <div style={{ flex: 1 }} />
          <ChevronDown size={12} style={{ color: "var(--text-muted)", transform: showArtist ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
        </div>
        {showArtist && (
          <div style={{ padding: "0 14px 14px" }}>
            <div style={{ fontSize: "10px", color: "var(--text-muted)", marginBottom: "8px" }}>Include or exclude specific artists.</div>
            {filters.artist_mode && (
              <div style={{ display: "flex", gap: "4px", marginBottom: "8px" }}>
                <button onClick={() => setArtistMode("include")} style={{ padding: "4px 10px", borderRadius: "5px", fontSize: "10px", cursor: "pointer", border: "none",
                  background: filters.artist_mode === "include" ? "rgba(72,187,120,0.15)" : "var(--bg-elevated)",
                  color: filters.artist_mode === "include" ? "#4ade80" : "var(--text-muted)",
                  border: `1px solid ${filters.artist_mode === "include" ? "rgba(72,187,120,0.3)" : "var(--border)"}` }}>
                  Include only
                </button>
                <button onClick={() => setArtistMode("exclude")} style={{ padding: "4px 10px", borderRadius: "5px", fontSize: "10px", cursor: "pointer", border: "none",
                  background: filters.artist_mode === "exclude" ? "rgba(240,120,136,0.15)" : "var(--bg-elevated)",
                  color: filters.artist_mode === "exclude" ? "#f07888" : "var(--text-muted)",
                  border: `1px solid ${filters.artist_mode === "exclude" ? "rgba(240,120,136,0.3)" : "var(--border)"}` }}>
                  Exclude
                </button>
              </div>
            )}
            <input type="text" value={artistInput} onChange={(e) => setArtistInput(e.target.value)} onBlur={handleArtistBlur}
              onKeyDown={(e) => { if (e.key === "Enter") handleArtistBlur(); }}
              placeholder={filters.artist_mode ? "e.g. Alan Walker, Adib Sin" : "Click a mode above first..."}
              disabled={!filters.artist_mode}
              style={{ width: "100%", padding: "6px 10px", borderRadius: "5px", fontSize: "11px",
                background: filters.artist_mode ? "var(--bg-elevated)" : "var(--bg-page)",
                border: "1px solid var(--border)", color: "var(--text-primary)", outline: "none",
                opacity: filters.artist_mode ? 1 : 0.4, boxSizing: "border-box" }} />
            {!filters.artist_mode && (
              <div style={{ marginTop: "6px", display: "flex", gap: "4px" }}>
                <button onClick={() => { setFilters({ ...filters, artist_mode: "include" }); doSave({ ...filters, artist_mode: "include" }); }}
                  style={{ padding: "4px 10px", borderRadius: "5px", fontSize: "10px", cursor: "pointer", border: "1px solid var(--border)", background: "var(--bg-elevated)", color: "var(--text-muted)" }}>
                  Include only
                </button>
                <button onClick={() => { setFilters({ ...filters, artist_mode: "exclude" }); doSave({ ...filters, artist_mode: "exclude" }); }}
                  style={{ padding: "4px 10px", borderRadius: "5px", fontSize: "10px", cursor: "pointer", border: "1px solid var(--border)", background: "var(--bg-elevated)", color: "var(--text-muted)" }}>
                  Exclude
                </button>
              </div>
            )}
            {filters.artists?.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: "3px", marginTop: "6px" }}>
                {filters.artists.map((a) => (
                  <span key={a} style={{ fontSize: "10px", padding: "2px 6px", borderRadius: "4px",
                    background: "rgba(72,187,120,0.08)", border: "1px solid rgba(72,187,120,0.15)", color: "#4ade80" }}>{a}</span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default FiltersPanel;
