import React from "react";

export default function HeaderButton({ children, onClick, title, className = "", ...props }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`w-8 h-8 flex items-center justify-center bg-transparent border-none rounded-md text-[var(--text-muted)] cursor-pointer relative transition-all duration-100 hover:bg-white/[0.08] active:scale-95 ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
