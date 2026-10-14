---
name: update-profiles
description: Update VAMOS's opponent profiles from the games played since the last update, or create a new profile, on the Claude subscription you use with Claude Code (never the API key VAMOS uses at the table). Use when the user asks to update the profiles, absorb a session, or create a profile for a player.
---

# Update the opponent profiles

VAMOS owns `profiles/<name>.md` and keeps them current under the rulebook `profiles/_GUIDELINES.md` (read it first).
This runs here in Claude Code on the user's subscription: never call the Claude API and never run anything that does.

## 1. Prepare

```bash
uv run python scripts/prepare_profile_update.py
```

It finds the saved games in `data/games/` that no profile has absorbed and gives each a session label (the next letters after the last one used; labels live in `data/profile-sessions.json`).
It works out who played from each profile's `player_id` and the phone accounts in `data/identity.json`.
It writes a work folder under `data/profile-work/` with, for each player to update:
- the exact counts (`<player>.stats.json`), split by session, ring and heads-up;
- every hand they played (`<player>.digest.md`);
- `sessions.json` and `players.json` (the workflow's input).

It prints the work folder, the new sessions, the players to update, and any accounts it could not attribute.

- **Unknown accounts:** ask the user who each one was (show the nickname, the game's date and the hands played). Then record the answer and run the prepare step again:
  - a friend's phone or guest account: `--match <account>=<profile>` (written to `data/identity.json`);
  - a player without a profile: leave them out; nothing is needed;
  - a test or a game that should not count: `--exclude <game> "<reason>"`.
- **No new games:** tell the user the profiles are already up to date, and stop.

## 2. Write, check and correct

Run the saved workflow with the work folder's `players.json`:

```
Workflow({ name: "update-profiles", args: { repo: "<repo path>", work: "<work folder>", date: "<today, YYYY-MM-DD>", players: <players.json> } })
```

For each player it writes the updated profile, checks every number and every hand cited against the counts and the hand records, and applies the corrections that hold up.
The results land in `<work folder>/new/`.

Without the Workflow tool, run the same three steps yourself for each player, one subagent each (the Agent tool), using the `WRITE`, `CHECK` and `CORRECT` prompts in `.claude/workflows/update-profiles.js` with the same arguments: write the profile, have a second subagent check it, then a third apply the corrections that hold up.

## 3. Install

```bash
uv run python scripts/prepare_profile_update.py --install <work folder>
```

This step:
- checks each new file with VAMOS's own profile reader (frontmatter, the panel's headline stats);
- saves the previous version and the new one in `data/profile-history/<name>/`;
- installs the new file in `profiles/`.

VAMOS picks the new profiles up the next time it needs them, even mid-session.

## 4. Report

For each player, tell the user in a line or two what changed in the read and the new headline stats.
Mention anything a checker could not resolve.

## Creating a profile

Only when the user asks, under the name the user chooses:

```bash
uv run python scripts/prepare_profile_update.py --create <name> --accounts <account>[,<account>]
```

The account with the most hands becomes `player_id`; any others go into `data/identity.json`.
The prepare step gathers every saved game those accounts played.
Then run the workflow with that player marked `create: true` (players.json already says so), and install as above.
