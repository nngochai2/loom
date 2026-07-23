import * as React from "react";
import type { ToastProps } from "@/components/ui/toast";

const TOAST_LIMIT = 3;
const TOAST_REMOVE_DELAY = 5000;

type ToasterToast = ToastProps & {
  id: string;
  title?: React.ReactNode;
  description?: React.ReactNode;
};

type State = { toasts: ToasterToast[] };

let count = 0;
function genId() {
  count = (count + 1) % Number.MAX_SAFE_INTEGER;
  return count.toString();
}

type Listener = (state: State) => void;
const listeners: Listener[] = [];
let memoryState: State = { toasts: [] };

function dispatch(action: { type: "ADD"; toast: ToasterToast } | { type: "DISMISS"; id: string }) {
  if (action.type === "ADD") {
    memoryState = { toasts: [action.toast, ...memoryState.toasts].slice(0, TOAST_LIMIT) };
  } else {
    memoryState = { toasts: memoryState.toasts.filter((t) => t.id !== action.id) };
  }
  listeners.forEach((listener) => listener(memoryState));
}

export function toast(props: Omit<ToasterToast, "id">) {
  const id = genId();
  dispatch({ type: "ADD", toast: { ...props, id } });
  setTimeout(() => dispatch({ type: "DISMISS", id }), TOAST_REMOVE_DELAY);
  return id;
}

export function useToast() {
  const [state, setState] = React.useState<State>(memoryState);

  React.useEffect(() => {
    listeners.push(setState);
    return () => {
      const index = listeners.indexOf(setState);
      if (index > -1) listeners.splice(index, 1);
    };
  }, []);

  return { ...state, toast, dismiss: (id: string) => dispatch({ type: "DISMISS", id }) };
}
