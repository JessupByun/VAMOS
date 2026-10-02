import { useEffect, useState } from "preact/hooks";

import type { BackendStatus, Players } from "../shared/protocol";
import type { SeatElement } from "./dom/seats";

export interface ViewState {
  status: BackendStatus;
  players: Players | null;
  seats: SeatElement[];
}

export interface Store {
  get: () => ViewState;
  set: (patch: Partial<ViewState>) => void;
  subscribe: (listener: () => void) => () => void;
}

export function createStore(initial: ViewState): Store {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set: (patch) => {
      state = { ...state, ...patch };
      for (const listener of listeners) listener();
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export function useStore(store: Store): ViewState {
  const [, setVersion] = useState(0);
  useEffect(() => {
    const unsubscribe = store.subscribe(() => setVersion((n) => n + 1));
    // State may have changed between the first render and this subscription.
    setVersion((n) => n + 1);
    return unsubscribe;
  }, [store]);
  return store.get();
}
