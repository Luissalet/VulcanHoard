// React glue for copilot-context.js: a view publishes what it shows and registers how to reload itself.
import { useEffect, useRef } from "react";
import { addRefreshHandler, setViewState } from "./copilot-context.js";

// `snapshot` is plain data (compared as JSON, so a new object with the same content does not re-register); null hides the key.
export function useCopilotView(key, snapshot) {
  const json = JSON.stringify(snapshot ?? null);
  useEffect(() => {
    setViewState(key, JSON.parse(json));
    return () => setViewState(key, null);
  }, [key, json]);
}

// `reload` always runs with the latest props and state; it must not remount the view or reset typed fields.
export function useCopilotRefresh(reload) {
  const ref = useRef(reload);
  ref.current = reload;
  useEffect(() => addRefreshHandler(() => ref.current?.()), []);
}
