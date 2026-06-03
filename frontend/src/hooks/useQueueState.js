import { useState, useCallback, useRef } from "react";
import * as api from "../api";
import { useSharedRadio } from "./useRadioState";

export default function useQueueState(currentSongRef) {
  const [queue, setQueue] = useState([]);
  const [queueRebuilding, setQueueRebuilding] = useState(false);
  const [whyReason, setWhyReason] = useState("");

  const { addToast } = useSharedRadio();

  const handlePromoteSong = useCallback(async (songId) => {
    const order = queue.map((item) => item.song_id).filter((id) => id !== null);
    const idx = order.indexOf(songId);
    if (idx <= 0) return;
    order.splice(idx, 1);
    order.unshift(songId);
    try {
      const res = await api.reorderQueue(order);
      if (Array.isArray(res?.queue)) {
        setQueue(res.queue);
      } else {
        const itemMap = {};
        const nonSongItems = queue.filter(item => item.song_id == null);
        queue.forEach(item => { itemMap[item.song_id] = item; });
        const reorderedSongs = order.map(id => itemMap[id]).filter(Boolean);
        const remainingSongs = queue.filter(item => item.song_id != null && !order.includes(item.song_id));
        setQueue([...nonSongItems, ...reorderedSongs, ...remainingSongs]);
      }
    } catch (e) {
      addToast("Failed to reorder queue", "error");
    }
  }, [queue, addToast, setQueue]);

  return {
    // State
    queue, queueRebuilding, whyReason,
    // Actions
    handlePromoteSong,
    // Setters (for WebSocket/poll handlers)
    setQueue, setQueueRebuilding, setWhyReason,
  };
}
