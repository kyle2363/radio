import React, { useState, useEffect } from "react";
import * as api from "../api";

function DailyReport() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchReport(); }, []);

  const fetchReport = async () => {
    try { setReport(await api.fetchDailyReport()); } catch (e) {}
    setLoading(false);
  };

  if (loading) return <div className="text-gray-500 text-sm">Loading report...</div>;
  if (!report || report.total_songs === 0) {
    return (
      <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Today's Report</h3>
        <p className="text-sm text-gray-600 text-center py-4">{report?.message || "No listening today yet"}</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800 space-y-4">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Today's Report</h3>
      <div className="grid grid-cols-4 gap-2">
        <div className="bg-gray-800 rounded-lg p-2 text-center">
          <div className="text-base font-bold text-white">{report.total_songs}</div>
          <div className="text-[10px] text-gray-500">Songs</div>
        </div>
        <div className="bg-gray-800 rounded-lg p-2 text-center">
          <div className="text-base font-bold text-green-400">{report.listen_minutes}m</div>
          <div className="text-[10px] text-gray-500">Listen time</div>
        </div>
        <div className="bg-gray-800 rounded-lg p-2 text-center">
          <div className="text-base font-bold text-white">{Math.round(report.completion_rate * 100)}%</div>
          <div className="text-[10px] text-gray-500">Completion</div>
        </div>
        <div className="bg-gray-800 rounded-lg p-2 text-center">
          <div className="text-base font-bold text-red-400">{report.hearted}</div>
          <div className="text-[10px] text-gray-500">Hearts</div>
        </div>
      </div>
      {report.top_artists?.length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-1">Top Artists Today</div>
          <div className="flex flex-wrap gap-1">
            {report.top_artists.map((a, i) => <span key={i} className="px-2 py-0.5 rounded bg-gray-800 text-xs text-gray-400">{a.artist} ({a.plays})</span>)}
          </div>
        </div>
      )}
      {report.mood_distribution && Object.keys(report.mood_distribution).length > 0 && (
        <div>
          <div className="text-xs text-gray-500 mb-1">Mood Today</div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(report.mood_distribution).map(([mood, count]) => <span key={mood} className="px-2 py-0.5 rounded bg-gray-800 text-xs text-gray-400">{mood}: {count}</span>)}
          </div>
        </div>
      )}
    </div>
  );
}

export default DailyReport;
