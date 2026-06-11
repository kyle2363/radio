import React, { useState, useEffect, useRef } from "react";
import * as api from "../api";
import { Plus, Play, Trash2, ChevronRight } from "lucide-react";

export default function QueueContextMenu({ x, y, songId, songTitle, onClose, onAddToCollection, onPromote, onRemove }) {
  const [collections, setCollections] = useState([]);
  const [showCollections, setShowCollections] = useState(false);
  const [addingTo, setAddingTo] = useState(null);
  const menuRef = useRef(null);

  useEffect(() => {
    api.fetchCollections().then((data) => {
      setCollections(data.collections || []);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const handleAddToCollection = async (collectionId) => {
    setAddingTo(collectionId);
    try {
      await onAddToCollection(songId, collectionId);
    } catch (e) {}
    setAddingTo(null);
    onClose();
  };

  const menuX = Math.min(x, window.innerWidth - 220);
  const menuY = Math.min(y, window.innerHeight - 200);

  return (
    <div ref={menuRef} className="fixed z-[200] rounded-lg py-1 shadow-xl backdrop-blur-xl"
      style={{
        left: menuX, top: menuY, minWidth: "200px",
        background: "var(--bg-overlay)",
        border: "1px solid var(--border)",
      }}>
      <div className="px-3 py-2 text-xs font-medium truncate" style={{ color: "var(--text-primary)", borderBottom: "1px solid var(--border-subtle)" }}>
        {songTitle || "Song"}
      </div>

      {onPromote && (
        <button onClick={() => { onPromote(songId); onClose(); }} className="ctx-item">
          <Play size={12} />
          <span>Play now</span>
        </button>
      )}

      {onRemove && (
        <button onClick={() => { onRemove(songId); onClose(); }} className="ctx-item">
          <Trash2 size={12} />
          <span>Remove from queue</span>
        </button>
      )}

      <div style={{ borderTop: "1px solid var(--border-subtle)", margin: "2px 0" }} />

      <button onClick={() => setShowCollections(!showCollections)} className="ctx-item">
        <Plus size={12} />
        <span>Add to collection</span>
        <ChevronRight size={10} style={{ marginLeft: "auto", transform: showCollections ? "rotate(90deg)" : "", transition: "transform 0.15s" }} />
      </button>

      {showCollections && (
        <div style={{ maxHeight: "180px", overflowY: "auto" }}>
          {collections.length === 0 && (
            <div className="px-3 py-2 text-xs" style={{ color: "var(--text-muted)" }}>No collections yet</div>
          )}
          {collections.map((c) => (
            <button key={c.id} onClick={() => handleAddToCollection(c.id)} className="ctx-item ctx-sub"
              disabled={addingTo === c.id}>
              <span style={{ fontSize: "11px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {addingTo === c.id ? "Adding..." : c.name}
              </span>
              {c.song_count > 0 && (
                <span className="text-[10px] ml-auto flex-shrink-0" style={{ color: "var(--text-muted)" }}>{c.song_count}</span>
              )}
            </button>
          ))}
        </div>
      )}

      <style>{`
        .ctx-item {
          display: flex; align-items: center; gap: 8px; width: 100%;
          padding: 7px 12px; font-size: 11px; text-align: left;
          background: none; border: none; cursor: pointer;
          color: var(--text-secondary); transition: background 0.1s;
        }
        .ctx-item:hover { background: rgba(255,255,255,0.05); color: var(--text-primary); }
        .ctx-item:disabled { opacity: 0.4; cursor: default; }
        .ctx-sub { padding-left: 24px; font-size: 11px; }
        .ctx-sub:hover { background: rgba(78,204,196,0.08); }
      `}</style>
    </div>
  );
}
