import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(__file__))
from db import database

conn = database.get_connection()

clusters = conn.execute(
    "SELECT cluster_id, COUNT(*) as cnt FROM songs GROUP BY cluster_id ORDER BY cluster_id"
).fetchall()

lines = []
lines.append(f"=== Cluster Export — {sum(c['cnt'] for c in clusters)} songs across {len(clusters)} clusters ===")
lines.append("")

for c in clusters:
    cid = c["cluster_id"]
    cnt = c["cnt"]
    lines.append(f"{'='*80}")
    lines.append(f"  Cluster {cid:2d}  —  {cnt:3d} songs")
    lines.append(f"{'='*80}")

    songs = conn.execute(
        "SELECT artist, title, energy, valence, bpm FROM songs WHERE cluster_id = ? ORDER BY artist, title",
        (cid,)
    ).fetchall()
    for s in songs:
        artist = s["artist"] or "?"
        title = s["title"] or "?"
        bpm = s["bpm"] if s["bpm"] else "—"
        lines.append(f"    {artist}  —  {title}  (E={s['energy']:.2f}, V={s['valence']:.2f}, BPM={bpm})")

    lines.append("")

conn.close()

out_path = os.path.join(os.path.dirname(__file__), "..", "cluster_export.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Exported {sum(c['cnt'] for c in clusters)} songs to {out_path}")
