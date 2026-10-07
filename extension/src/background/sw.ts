import {
  type BackendStatus,
  type HandInfo,
  type ContentToWorker,
  HOST_NAME,
  type HostToWorker,
  type LogStatus,
  type PanelToWorker,
  type WorkerToContent,
  type WorkerToPanel,
} from "../shared/protocol";
import { HostRetry, hostDown } from "./retry";
import { GameTabs } from "./tabs";

// The native host lives as long as a PokerNow table tab is open: it is launched when the
// first content script connects and the port is closed a short grace after the last one goes
// away, which ends the host process (its stdin closes). The grace lets a refreshed tab find
// the same backend, with tonight's state, instead of a fresh one. The open native port is
// also what keeps this worker alive; without it Chrome stops the worker after 30s idle and
// every content and panel port reconnects.
const contentPorts = new Set<chrome.runtime.Port>();
const panelPorts = new Set<chrome.runtime.Port>();
let host: chrome.runtime.Port | null = null;
let status: BackendStatus = { type: "backend", state: "down", detail: "no PokerNow table open" };
const retry = new HostRetry();
let retryTimer: ReturnType<typeof setTimeout> | undefined;
const HOST_GRACE_MS = 15000;
let closeTimer: ReturnType<typeof setTimeout> | undefined;
// What a side panel needs when it opens, replayed to it: the latest settings and costs,
// and per table the players, the current hand, and every message about that hand's
// decisions (Claude's answers by node, then the latest, which says whether one is live).
const latest = new Map<string, WorkerToPanel>();
interface TableMemory {
  players?: WorkerToPanel;
  hand?: HandInfo;
  history?: WorkerToPanel;
  decisions: Map<string, WorkerToPanel>;
  last?: WorkerToPanel;
}
const tables = new Map<string, TableMemory>();
// Which tab polls each game's log, and whose reports of the table reach the backend.
const tabs = new GameTabs<chrome.runtime.Port>();
// Per game, why its log poller is failing, for tabs and panels that open meanwhile.
const logTrouble = new Map<string, LogStatus>();

function table(gameId: string): TableMemory {
  let memory = tables.get(gameId);
  if (!memory) {
    memory = { decisions: new Map() };
    tables.set(gameId, memory);
  }
  return memory;
}

function remember(message: HostToWorker) {
  if (message.type === "settings" || message.type === "costs" || message.type === "api_status") {
    latest.set(message.type, message);
  }
  else if (message.type === "players") table(message.gameId).players = message;
  else if (message.type === "history") table(message.gameId).history = message;
  else if (message.type === "hand") {
    const memory = table(message.gameId);
    if (memory.hand?.key !== message.key) memory.decisions.clear();
    memory.hand = message;
  } else if (message.type === "facts") {
    const memory = table(message.gameId);
    const node = message.facts?.node;
    if (node && message.facts?.advice && "state" in message.facts.advice) memory.decisions.set(node, message);
    memory.last = message;
  }
}

function replay(port: chrome.runtime.Port) {
  for (const message of latest.values()) port.postMessage(message);
  for (const message of logTrouble.values()) port.postMessage(message);
  for (const memory of tables.values()) {
    if (memory.players) port.postMessage(memory.players);
    if (memory.hand) port.postMessage(memory.hand);
    if (memory.history) port.postMessage(memory.history);
    const nodes = [...memory.decisions.keys()].sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
    for (const node of nodes) port.postMessage(memory.decisions.get(node));
    if (memory.last) port.postMessage(memory.last);
  }
}

chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error: unknown) => console.error("[VAMOS] side panel behavior", error));

/** Tells the side panel and the game's other tabs why its log is failing, or that it is not. */
function setLogStatus(status: LogStatus, from?: chrome.runtime.Port) {
  if (status.trouble) logTrouble.set(status.gameId, status);
  else logTrouble.delete(status.gameId);
  for (const port of contentPorts) if (port !== from) port.postMessage(status satisfies WorkerToContent);
  for (const port of panelPorts) port.postMessage(status satisfies WorkerToPanel);
}

function setStatus(next: BackendStatus) {
  status = next;
  for (const port of contentPorts) port.postMessage(next satisfies WorkerToContent);
  for (const port of panelPorts) port.postMessage(next satisfies WorkerToPanel);
}

function connectHost() {
  clearTimeout(retryTimer);
  retryTimer = undefined;
  // A new backend starts with no tables: nothing remembered from the last one is current.
  tables.clear();
  setStatus({ type: "backend", state: "connecting" });
  const port = chrome.runtime.connectNative(HOST_NAME);
  host = port;
  let started = false;
  port.onMessage.addListener((message: HostToWorker) => {
    if (message.type === "ready") {
      started = true;
      retry.ready(Date.now());
      setStatus({
        type: "backend",
        state: "ready",
        version: message.version,
        recording: message.recording,
      });
    } else {
      // Where to pick the log up goes to the one tab polling that game.
      if (message.type === "resume") {
        tabs.poller(message.gameId)?.postMessage(message satisfies WorkerToContent);
        return;
      }
      remember(message);
      // Each content script keeps only its own game's players and facts; the settings say
      // whether the card is shown.
      if (message.type === "players" || message.type === "facts" || message.type === "settings") {
        for (const content of contentPorts) content.postMessage(message satisfies WorkerToContent);
      }
      for (const panel of panelPorts) panel.postMessage(message satisfies WorkerToPanel);
    }
  });
  port.onDisconnect.addListener(() => {
    // Read first: an unread reason is reported by Chrome even when this port is stale.
    const reason = chrome.runtime.lastError?.message ?? "backend exited";
    if (host !== port) return;
    host = null;
    if (contentPorts.size === 0) {
      setStatus({ type: "backend", state: "down", detail: "no PokerNow table open" });
      return;
    }
    // A table is still open, so the backend should be running: relaunch with backoff.
    const wait = retry.failed(Date.now());
    setStatus(hostDown(reason, started, wait));
    retryTimer = setTimeout(connectHost, wait);
  });
  port.postMessage({ type: "hello", extensionVersion: chrome.runtime.getManifest().version });
}

// The last table tab went away and none came back within the grace: stop the backend.
function closeHost() {
  closeTimer = undefined;
  if (contentPorts.size > 0) return;
  clearTimeout(retryTimer);
  retryTimer = undefined;
  retry.reset();
  tables.clear();
  if (host !== null) {
    const closing = host;
    host = null;
    closing.disconnect();
  }
  setStatus({ type: "backend", state: "down", detail: "no PokerNow table open" });
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === "content") {
    contentPorts.add(port);
    clearTimeout(closeTimer);
    closeTimer = undefined;
    if (host === null && retryTimer === undefined) connectHost();
    port.postMessage(status satisfies WorkerToContent);
    const settings = latest.get("settings");
    if (settings?.type === "settings") port.postMessage(settings satisfies WorkerToContent);
    for (const trouble of logTrouble.values()) port.postMessage(trouble satisfies WorkerToContent);
    port.onMessage.addListener((message: ContentToWorker) => {
      if (message.type === "resume_request") {
        // Only one tab per game polls; another waits until that one goes.
        if (tabs.claim(port, message.gameId)) host?.postMessage(message);
        return;
      }
      if (message.type === "log_status") {
        if (tabs.poller(message.gameId) === port) setLogStatus(message, port);
        return;
      }
      if (message.type === "turn" && message.table !== null) {
        // The hero's turn began here: the polling tab fetches the rows that led to it now.
        const poller = tabs.poller(message.gameId);
        if (poller !== undefined && poller !== port) {
          poller.postMessage({ type: "poll_soon", gameId: message.gameId } satisfies WorkerToContent);
        }
      }
      if (tabs.passes(port, message)) host?.postMessage(message);
    });
    port.onDisconnect.addListener(() => {
      // Reading the reason marks it handled; Chrome reports it otherwise (e.g. "moved into
      // back/forward cache" when the user navigates away from the table).
      void chrome.runtime.lastError;
      contentPorts.delete(port);
      const handover = tabs.close(port);
      if (handover) {
        setLogStatus({ type: "log_status", gameId: handover.gameId, trouble: null });
        // Another tab on the game polls in its place, from where the backend's rows end.
        if (handover.next !== undefined) {
          host?.postMessage({ type: "resume_request", gameId: handover.gameId } satisfies ContentToWorker);
        }
      }
      if (contentPorts.size > 0) return;
      clearTimeout(closeTimer);
      closeTimer = setTimeout(closeHost, HOST_GRACE_MS);
    });
  } else if (port.name === "panel") {
    panelPorts.add(port);
    port.postMessage(status satisfies WorkerToPanel);
    replay(port);
    port.onMessage.addListener((message: PanelToWorker) => {
      if (message.type === "snapshot_request") {
        for (const content of contentPorts) content.postMessage(message satisfies WorkerToContent);
      } else {
        host?.postMessage(message);
      }
    });
    port.onDisconnect.addListener(() => {
      void chrome.runtime.lastError;
      panelPorts.delete(port);
    });
  }
});
