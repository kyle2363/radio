import React from "react";

function Skeleton({ className = "", width, height }) {
  return (
    <div
      className={`animate-pulse bg-gray-800 rounded ${className}`}
      style={{ width, height }}
    />
  );
}

export function SkeletonCard() {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-6 space-y-4">
      <div className="flex items-center gap-5">
        <Skeleton className="w-24 h-24 rounded-lg flex-shrink-0" />
        <div className="flex-1 space-y-3">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-4 w-32" />
          <div className="flex gap-2 mt-2">
            <Skeleton className="h-5 w-16 rounded-md" />
            <Skeleton className="h-5 w-16 rounded-md" />
            <Skeleton className="h-5 w-16 rounded-md" />
          </div>
        </div>
      </div>
    </div>
  );
}

export function SkeletonQueue() {
  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <Skeleton className="h-3 w-20 mb-4" />
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="flex items-center justify-between">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3 w-16" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function SkeletonRow({ count = 5 }) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50 overflow-hidden">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 last:border-b-0">
          <Skeleton className="w-4 h-4 rounded flex-shrink-0" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3.5 w-40" />
            <Skeleton className="h-3 w-28" />
          </div>
          <Skeleton className="h-3 w-10" />
        </div>
      ))}
    </div>
  );
}

export function SkeletonStats() {
  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800 space-y-4">
      <Skeleton className="h-3 w-24" />
      <div className="grid grid-cols-3 gap-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="bg-gray-800 rounded-lg p-3 text-center space-y-2">
            <Skeleton className="h-6 w-12 mx-auto" />
            <Skeleton className="h-3 w-16 mx-auto" />
          </div>
        ))}
      </div>
    </div>
  );
}

export default Skeleton;
