import React, { useState, useEffect } from "react";
import { Plus, Trash2, Edit3 } from "lucide-react";
import * as api from "../api";

export default function CollectionsList({ onSelect }) {
  const [collections, setCollections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const load = async () => {
    try {
      const data = await api.fetchCollections();
      setCollections(data.collections || []);
    } catch (e) {}
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await api.createCollection(newName.trim(), newDesc.trim());
      setNewName("");
      setNewDesc("");
      setShowCreate(false);
      load();
    } catch (e) {}
  };

  const handleDelete = async (e, id) => {
    e.stopPropagation();
    try {
      await api.deleteCollection(id);
      load();
    } catch (e) {}
  };

  if (loading) {
    return <div style={{ padding: "14px", fontSize: "12px", color: "var(--text-muted)" }}>Loading...</div>;
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{collections.length} collection{collections.length !== 1 ? "s" : ""}</div>
        <button onClick={() => setShowCreate(true)}
          style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, padding: "4px 10px", borderRadius: 4, background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", cursor: "pointer" }}>
          <Plus size={10} /> New
        </button>
      </div>

      {showCreate && (
        <div style={{ padding: "8px 10px", marginBottom: 10, borderRadius: 6, background: "var(--bg-elevated)", border: "1px solid var(--border)" }}>
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Collection name"
            style={{ width: "100%", padding: "6px 8px", marginBottom: 6, borderRadius: 4, background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: 11, outline: "none" }} />
          <input value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="Description (optional)"
            style={{ width: "100%", padding: "6px 8px", marginBottom: 6, borderRadius: 4, background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-primary)", fontSize: 11, outline: "none" }} />
          <div style={{ display: "flex", gap: 4 }}>
            <button onClick={handleCreate} style={{ padding: "4px 10px", borderRadius: 4, background: "var(--accent-muted)", border: "1px solid var(--border)", color: "var(--accent)", fontSize: 10, cursor: "pointer" }}>Create</button>
            <button onClick={() => setShowCreate(false)} style={{ padding: "4px 10px", borderRadius: 4, background: "var(--bg-surface)", border: "1px solid var(--border)", color: "var(--text-muted)", fontSize: 10, cursor: "pointer" }}>Cancel</button>
          </div>
        </div>
      )}

      {collections.length === 0 && !showCreate && (
        <div style={{ padding: "20px 14px", textAlign: "center", fontSize: 12, color: "var(--text-muted)" }}>
          No collections yet. Create one to organize your songs.
        </div>
      )}

      {collections.map((c) => (
        <div key={c.id} onClick={() => onSelect?.(c)}
          style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", marginBottom: 4, borderRadius: 6, cursor: "pointer", background: "var(--bg-surface)", border: "1px solid var(--border-subtle)" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</div>
            {(c.description || c.song_count > 0) && (
              <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 1 }}>
                {c.description ? `${c.description} · ` : ""}{c.song_count} song{c.song_count !== 1 ? "s" : ""}
              </div>
            )}
          </div>
          <button onClick={(e) => handleDelete(e, c.id)} title="Delete collection"
            style={{ background: "none", border: "none", cursor: "pointer", padding: 2, color: "var(--text-muted)" }}>
            <Trash2 size={11} />
          </button>
        </div>
      ))}
    </div>
  );
}
