import { startSession } from "./session";

// Anchored so the log endpoint and CSV export URLs under /games/<id>/ are not table pages.
const GAME_PATH = /^\/games\/([^/?#]+)\/?$/;
const URL_CHECK_MS = 500;

// PokerNow switches pages client-side (start-game -> /games/<id> via replaceState) without a
// page load, and Chrome only injects content scripts on real loads. So the script runs on
// every PokerNow page and starts or stops a session whenever the game in the URL changes.
let current: { gameId: string; stop: () => void } | null = null;

function sync() {
  const gameId = location.pathname.match(GAME_PATH)?.[1] ?? null;
  if (gameId === (current?.gameId ?? null)) return;
  current?.stop();
  current = gameId ? { gameId, stop: startSession(gameId) } : null;
}

sync();
setInterval(sync, URL_CHECK_MS);
window.addEventListener("popstate", sync);
