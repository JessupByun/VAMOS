import { render } from "preact";

import type { BackendStatus, ContentToWorker, WorkerToContent } from "../shared/protocol";
import { ClockEstimate, readTimeBar } from "./dom/clock";
import { readHero, readSeats, seatsKey } from "./dom/seats";
import { heroSeat, readHeroTurn, readLayout } from "./dom/table";
import { startLogPoller } from "./log/poller";
import { createStore } from "./store";
import overlayCss from "./ui/overlay.css?inline";
import { Overlay } from "./ui/overlay";

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;
// Seats are re-read on a timer rather than by observing PokerNow's DOM: it re-renders
// constantly (the decision timer alone every 100ms), and badges must also follow layout.
const SEAT_CHECK_MS = 500;
const ROOT_ID = "vamos-root";

/** Connects one PokerNow game page to the backend; returns a function that tears it down. */
export function startSession(gameId: string): () => void {
  const store = createStore({
    status: { type: "backend", state: "connecting" },
    players: null,
    seats: [],
    heroSeat: null,
    layout: null,
    turn: null,
    facts: null,
    showCard: true,
    showBadges: true,
  });
  let port: chrome.runtime.Port | null = null;
  let stopped = false;
  let retryDelay = RECONNECT_MIN_MS;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  // Set when the backend went away; the next "ready" is then a fresh backend that has none
  // of this game's seats, so they are sent again.
  let backendLost = false;
  // Log pages go out only once the backend has said where to pick the log up (the rows it
  // already holds for this game, from before a refresh or restart).
  let resumed = false;
  let reportedSeats: string | null = null;
  // The turn key the backend last heard about; undefined until it has heard anything.
  let reportedTurn: string | null | undefined;
  // The hero's decision clock, reported once per turn so Claude's deadline can follow it.
  const clock = new ClockEstimate();
  let clockReported = false;
  let clockTurn: string | null = null;

  const status = () => store.get().status;
  const show = (next: BackendStatus) => store.set({ status: next });
  const send = (message: ContentToWorker): boolean => {
    if (port === null) return false;
    port.postMessage(message);
    return true;
  };
  // Pages go out only to a ready backend that has said where to resume, so the cursor never
  // advances past rows the worker had nowhere to deliver.
  const poller = startLogPoller(gameId, (page) => status().state === "ready" && resumed && send(page));

  function reportSeats(force = false) {
    const seats = store.get().seats;
    const hero = readHero(document);
    const key = `${seatsKey(seats)}#${hero ? seatsKey([hero]) : ""}`;
    if (!force && key === reportedSeats) return;
    if (status().state !== "ready") return;
    const players = seats.map(({ id, name }) => ({ id, name }));
    if (send({ type: "seats", gameId, players, hero })) reportedSeats = key;
  }

  function reportTurn(force = false) {
    const table = readHeroTurn(document);
    const key = table && JSON.stringify(table);
    if (key !== store.get().turn) {
      // The rows that led to this turn are usually not fetched yet.
      if (key !== null) poller.soon();
      store.set({ turn: key });
    }
    if (!force && key === reportedTurn) return;
    if (status().state !== "ready") return;
    if (send({ type: "turn", gameId, key: key ?? "", table })) reportedTurn = key;
  }

  function reportClock() {
    const turn = store.get().turn;
    // A new decision can follow the last within one check, with no gap in between.
    if (turn !== clockTurn) {
      clockTurn = turn;
      clock.reset();
      clockReported = false;
    }
    if (turn === null) return;
    if (clockReported || status().state !== "ready") return;
    const secondsLeft = clock.sample(performance.now(), readTimeBar(document));
    if (secondsLeft !== null && send({ type: "clock", gameId, secondsLeft })) clockReported = true;
  }

  const seatTimer = setInterval(() => {
    // Re-render every tick so badges follow their seats when the layout moves.
    store.set({
      seats: readSeats(document),
      heroSeat: heroSeat(document),
      layout: readLayout(document),
    });
    reportSeats();
    reportTurn();
    reportClock();
  }, SEAT_CHECK_MS);
  // PokerNow rescales the whole table with the window; follow at once, not at the next tick.
  const relayout = () => store.set({ seats: readSeats(document), layout: readLayout(document) });
  window.addEventListener("resize", relayout);

  // After the extension is reloaded or removed this script keeps running but can no longer
  // reach the worker; only a page reload injects the new version.
  const retire = () => {
    stopped = true;
    poller.stop();
    clearInterval(seatTimer);
    window.removeEventListener("resize", relayout);
    show({ type: "backend", state: "down", detail: "VAMOS was updated, refresh this tab" });
  };

  // The worker may be stopped (30s idle with no backend running) or restarted, which
  // disconnects this port; reconnecting wakes it and gets a fresh status.
  function connect() {
    if (!chrome.runtime?.id) return retire();
    const next = chrome.runtime.connect({ name: "content" });
    port = next;
    next.onMessage.addListener((message: WorkerToContent) => {
      if (message.type === "backend") {
        retryDelay = RECONNECT_MIN_MS;
        if (message.state === "down") backendLost = true;
        if (message.state !== "ready") resumed = false;
        const becameReady = message.state === "ready" && status().state !== "ready";
        show(message);
        if (message.state === "ready" && backendLost) backendLost = false;
        if (message.state === "ready" && !resumed) send({ type: "resume_request", gameId });
        if (becameReady) {
          reportSeats(true);
          reportTurn(true);
        }
      } else if (message.type === "resume") {
        if (message.gameId === gameId) {
          poller.resume(message.after);
          resumed = true;
        }
      } else if (message.type === "settings") {
        store.set({ showCard: message.showCard !== false, showBadges: message.showBadges !== false });
      } else if (message.type === "players") {
        if (message.gameId === gameId) store.set({ players: message });
      } else if (message.type === "facts") {
        if (message.gameId === gameId) store.set({ facts: message });
      } else if (message.type === "snapshot_request") {
        send({
          type: "dom_snapshot",
          gameId,
          capturedAt: Date.now(),
          html: document.documentElement.outerHTML,
        });
      }
    });
    next.onDisconnect.addListener(() => {
      // Reading the reason marks it handled (it is set when the worker restarts or the page
      // enters the back/forward cache); Chrome reports an unread one as an error.
      void chrome.runtime.lastError;
      if (port === next) port = null;
      if (stopped) return;
      if (!chrome.runtime?.id) return retire();
      // The worker stopping also closed the native port, so the backend restarts too.
      backendLost = true;
      resumed = false;
      show({ type: "backend", state: "connecting" });
      retryTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS);
    });
  }

  connect();

  const assign = (playerId: string, profile: string | null) =>
    send({ type: "assign_identity", playerId, profile });

  // A previous, orphaned copy of this script may have left its overlay behind.
  document.getElementById(ROOT_ID)?.remove();
  // Shadow DOM keeps PokerNow's styles out of the overlay and the overlay's styles out of
  // the page.
  const hostEl = document.createElement("div");
  hostEl.id = ROOT_ID;
  document.body.append(hostEl);
  const shadow = hostEl.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = overlayCss;
  const mount = document.createElement("div");
  shadow.append(style, mount);
  render(<Overlay store={store} onAssign={assign} />, mount);

  return () => {
    stopped = true;
    clearTimeout(retryTimer);
    clearInterval(seatTimer);
    window.removeEventListener("resize", relayout);
    poller.stop();
    port?.disconnect();
    port = null;
    render(null, mount);
    hostEl.remove();
  };
}
