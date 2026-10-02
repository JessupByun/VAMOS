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
- `tag`: 1 to 3 words describing their style (it is shown on a small seat badge).
- `aliases`: every nickname they used in these logs.
- Compute stats from the logs; give every number with its sample size, e.g. "fold to 3-bet 70% (14/20)". Never state a percentage without the counts.
- Use the revealed hole cards to describe what hands they actually play and bluff with, not just how often.
- Mark any pattern backed by fewer than about 10 examples as low confidence.
- If recent sessions differ from older ones, say so and weight the recent behavior.
- Be specific and actionable. No generic poker advice, no filler, nothing that would apply to any player.
- Keep each profile under about 1,500 words.
- Output each profile as its own Markdown code block, preceded by the file name, e.g. `bob.md` (lowercase first name).
