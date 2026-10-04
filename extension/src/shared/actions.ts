/** What kind of move an action reads as, e.g. "RAISE TO 0.60 (3bb)" or "call 40%", for its color. */
export type ActionKind = "fold" | "check" | "call" | "bet" | "raise" | "allin";

export function actionKind(text: string | null | undefined): ActionKind | null {
  const t = (text ?? "").trim().toLowerCase();
  if (t.startsWith("all in")) return "allin";
  for (const kind of ["fold", "check", "call", "bet", "raise"] as const) {
    if (t.startsWith(kind)) return kind;
  }
  return null;
}

/** The CSS class that colors an action: red folds, green checks and calls, amber bets and raises, pink all-ins. */
export function actionClass(text: string | null | undefined): string {
  const kind = actionKind(text);
  return kind ? `act-${kind}` : "";
}
