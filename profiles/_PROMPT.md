# Prompt for generating VAMOS player profiles

Paste everything below the line into the AI chat, attach the joined PokerNow logs and `_TEMPLATE.md`, and replace the bracketed parts.

---

You are a poker analyst building opponent profiles for a real-time assistant.

Attached are PokerNow hand-history logs from our private No-Limit Hold'em cash game ([stakes, e.g. 0.10/0.20, $10 buy-ins = 50bb], no rake, mostly 4-handed, sometimes 3-handed and heads-up, run it twice allowed, rebuys and top-ups allowed).
The logs are joined from every player's own export, so you can see every player's hole cards in every hand, including folded and non-showdown hands.
Players appear as "Name @ ID"; the ID is stable even when someone changes their nickname.
The players are: [list each person and every nickname or ID they used].

Write one Markdown profile per player except [your name], following the attached template exactly: same frontmatter fields, same section headings, same order.

Requirements:
- `name`: the file name without `.md` (lowercase first name or handle).
- `tag`: 1 to 3 words describing their style (it is shown on a small seat badge).
- Refer to every player only by their profile name (the `name` field), never by an in-game nickname or display name.
- `player_id`: the PokerNow ID after the "@" in "Name @ ID". If they used more than one ID (another device), list them all: `player_id: [ID1, ID2]`. VAMOS uses this to recognize them at the table.
- `hands`, `sessions`, `hole_cards_known`, `last_updated`: how much data the profile rests on and when it was written. Each session is a label and a date, e.g. `A 2026-09-30`, nothing more.
- `net`: their total and per session, using the same labels as `sessions`, e.g. `net: +12.40 (A +20.00, B -7.60)`.
- The Summary's first sentence is their playstyle in one line; the side panel shows only that sentence.
- Keep the Key stats lines in the template's exact wording; the side panel reads them.
- Refer to me only as "jes", never by my real name.
- Compute stats from the logs; give every number with its sample size, e.g. "fold to 3-bet 70% (14/20)". Never state a percentage without the counts.
- Use the revealed hole cards to describe what hands they actually play and bluff with, not just how often.
- Mark any pattern backed by fewer than about 10 examples as low confidence.
- If recent sessions differ from older ones, say so and weight the recent behavior.
- Be specific and actionable. No generic poker advice, no filler, nothing that would apply to any player.
- Keep each profile under about 1,500 words (about 4,000 tokens; every word is sent to the assistant on each decision). When adding sessions, recompute every stat over all sessions and show at most two numbers per stat: all sessions and recent (the last two sessions). Fold older per-session splits into the totals.
- At most 8 example hands per bullet (describe the rest as a range, e.g. "median top 12%, down to T7s"), at most 5 notable hands, and each hand number at most once outside Notable hands.
- Leave out any session or hands a player asked to exclude, and say so in the Source line.
- Output each profile as its own Markdown code block, preceded by the file name, e.g. `bob.md` (lowercase first name).
