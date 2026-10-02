// PokerNow draws the hero's decision clock as a bar whose inline width is the share of the
// normal time left; seconds are not in the DOM, so they are estimated from how fast the bar
// shrinks. The time bank bar that follows is not counted.

const MIN_SPAN_MS = 900;

/** The hero's normal-time bar, in percent left, or null when no clock is running. */
export function readTimeBar(root: ParentNode): number | null {
  const bar = root.querySelector<HTMLElement>(
    ".table-player.you-player.decision-current .time-to-talk .normal-time",
  );
  if (!bar) return null;
  const width = Number.parseFloat(bar.style.width);
  return Number.isFinite(width) ? width : null;
}

export class ClockEstimate {
  private first: { at: number; width: number } | null = null;

  reset() {
    this.first = null;
  }

  /** Feeds one reading; returns the seconds left once the bar has moved for long enough. */
  sample(at: number, width: number | null): number | null {
    if (width === null || width <= 0) return null;
    // A bar that grew was refilled for a new decision.
    if (this.first === null || width > this.first.width) {
      this.first = { at, width };
      return null;
    }
    const elapsed = at - this.first.at;
    const shrunk = this.first.width - width;
    if (elapsed < MIN_SPAN_MS || shrunk <= 0) return null;
    return (width / shrunk) * (elapsed / 1000);
  }
}
