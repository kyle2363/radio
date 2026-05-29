import { useState, useEffect, useRef } from "react";

export default function useTransition(isOpen, duration = 200) {
  const [shouldRender, setShouldRender] = useState(isOpen);
  const [animClass, setAnimClass] = useState("");
  const timerRef = useRef(null);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);

    if (isOpen) {
      setShouldRender(true);
      setAnimClass("enter");
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setAnimClass("enter-active"));
      });
    } else {
      setAnimClass("exit");
      requestAnimationFrame(() => {
        requestAnimationFrame(() => setAnimClass("exit-active"));
      });
      timerRef.current = setTimeout(() => {
        setShouldRender(false);
        setAnimClass("");
      }, duration);
    }

    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [isOpen, duration]);

  return { shouldRender, animClass };
}
