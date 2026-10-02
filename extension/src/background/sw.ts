import {
  type BackendStatus,
  type ContentToWorker,
  HOST_NAME,
  type HostToWorker,
  type PanelToWorker,
  type WorkerToContent,
  type WorkerToPanel,
} from "../shared/protocol";
import { HostRetry } from "./retry";

// The native host lives exactly as long as at least one PokerNow table tab is open: it is
// launched when the first content script connects and the port is closed when the last one
// goes away, which ends the host process (its stdin closes). The open native port is also
// what keeps this worker alive; without it Chrome stops the worker after 30s idle and every
// content and panel port reconnects.
const contentPorts = new Set<chrome.runtime.Port>();
const panelPorts = new Set<chrome.runtime.Port>();
let host: chrome.runtime.Port | null = null;
let status: BackendStatus = { type: "backend", state: "down", detail: "no PokerNow table open" };
const retry = new HostRetry();
let retryTimer: ReturnType<typeof setTimeout> | undefined;

chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error: unknown) => console.error("[VAMOS] side panel behavior", error));

function setStatus(next: BackendStatus) {
  status = next;
  for (const port of contentPorts) port.postMessage(next satisfies WorkerToContent);
  for (const port of panelPorts) port.postMessage(next satisfies WorkerToPanel);
}

function connectHost() {
  clearTimeout(retryTimer);
  retryTimer = undefined;
  setStatus({ type: "backend", state: "connecting" });
  const port = chrome.runtime.connectNative(HOST_NAME);
  host = port;
  port.onMessage.addListener((message: HostToWorker) => {
    if (message.type === "ready") {
      retry.ready(Date.now());
      setStatus({ type: "backend", state: "ready", version: message.version });
    } else if (message.type === "error") {
      setStatus({ type: "backend", state: "down", detail: message.message });
    } else {
      for (const panel of panelPorts) panel.postMessage(message satisfies WorkerToPanel);
    }
  });
  port.onDisconnect.addListener(() => {
    if (host !== port) return;
    host = null;
    const reason = chrome.runtime.lastError?.message ?? "backend exited";
    if (contentPorts.size === 0) {
      setStatus({ type: "backend", state: "down", detail: "no PokerNow table open" });
      return;
    }
    // A table is still open, so the backend should be running: relaunch with backoff.
    const wait = retry.failed(Date.now());
    const seconds = Math.round(wait / 1000);
    setStatus({ type: "backend", state: "down", detail: `${reason} (retrying in ${seconds}s)` });
    retryTimer = setTimeout(connectHost, wait);
  });
  port.postMessage({ type: "hello", extensionVersion: chrome.runtime.getManifest().version });
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === "content") {
    contentPorts.add(port);
    if (host === null && retryTimer === undefined) connectHost();
    port.postMessage(status satisfies WorkerToContent);
    port.onMessage.addListener((message: ContentToWorker) => host?.postMessage(message));
    port.onDisconnect.addListener(() => {
      contentPorts.delete(port);
      if (contentPorts.size > 0) return;
      clearTimeout(retryTimer);
      retryTimer = undefined;
      retry.reset();
      if (host !== null) {
        const closing = host;
        host = null;
        closing.disconnect();
      }
      setStatus({ type: "backend", state: "down", detail: "no PokerNow table open" });
    });
  } else if (port.name === "panel") {
    panelPorts.add(port);
    port.postMessage(status satisfies WorkerToPanel);
    port.onMessage.addListener((message: PanelToWorker) => {
      for (const content of contentPorts) content.postMessage(message satisfies WorkerToContent);
    });
    port.onDisconnect.addListener(() => panelPorts.delete(port));
  }
});
