export const meta = {
  name: 'update-profiles',
  description: 'Update the opponent profiles from prepared session data: write, check against every hand, correct',
  whenToUse: 'After scripts/prepare_profile_update.py has written a work folder; run from the update-profiles skill.',
  phases: [
    { title: 'Write', detail: 'one writer per player' },
    { title: 'Check', detail: 'an adversarial checker per player' },
    { title: 'Correct', detail: 'apply the corrections that hold up' },
  ],
}

// args: { repo, work, date, players: [{ key, sessions, notes, create }] }
//   work: the folder scripts/prepare_profile_update.py wrote (sessions.json, <player>.stats.json,
//         <player>.digest.md); each writer saves <work>/new/<player>.md there.
//   create: true for a new profile (no current file), built from every game in the work folder.
const { repo, work, date, players } = args

const COMMON = (p) => `Context: VAMOS is jes's real-time poker assistant for a private PokerNow home game (NLH, blinds 0.10/0.20, 50bb buy-ins, rebuys and top-ups, 2 to 5 players). VAMOS owns the opponent profiles in ${repo}/profiles/; they are the source of truth about each player, and jes wants depth that can be relied on: exact stats, writing about playstyle and tendencies, and the exploits that work.
Files (read them in full; page through long ones):
- The rulebook: ${repo}/profiles/_GUIDELINES.md. Follow it exactly, including Size and compaction.
- ${p.create ? `No profile exists yet: this is a new profile named "${p.key}", created at jes's request.` : `The current profile: ${repo}/profiles/${p.key}.md (its totals cover the sessions listed in its frontmatter).`}
- Exact counts for the new sessions: ${work}/${p.key}.stats.json ("stats_by_session" per session split ring and heads-up, "stats_all_new_sessions" summed; each value "pct% (n/d)"; hands and net per session; open sizes; bet sizes as % of pot; hands where the player showed both cards). The keys follow the rulebook's definitions.
- Every hand the player played in the new sessions: ${work}/${p.key}.digest.md (hands they only folded preflop are left to the counts), named by session label and hand number.
- The sessions: ${work}/sessions.json.
Rules: write only the one output file named below; never edit anything in ${repo}. Never write a real name, PokerNow nickname or display name: only profile names and "jes". Standard poker terms only. No em dashes.`

const WRITE = (p) => `${COMMON(p)}

Player: ${p.key}. New sessions: ${p.sessions}. ${p.notes ?? ''}

Write the ${p.create ? 'new' : 'updated'} profile to ${work}/new/${p.key}.md (create the folder if needed), dated ${date}.
- ${p.create ? 'Build every section from the counts and hands.' : 'Keep everything of value from the current profile, revised where the new evidence says so (keep the old read with dates when behavior changed).'}
- Key stats: ${p.create ? 'totals are the new counts.' : 'new total = the current total plus the new ring counts for the same stat (add numerators and denominators).'} Recent = the last two ring sessions. Heads-up hands only in the Heads-up section, per opponent.
- Read every hand in the digest; every read needs counts or named hands; mark thin evidence as low confidence.
- Frontmatter and Changelog as the rulebook says (sessions, games, net, hands, hole_cards_known, last_updated ${date}).
Return a short summary: the new headline stats, the biggest changes to the read, and anything you were unsure about.`

const CHECK = (p) => `${COMMON(p)}

A writer ${p.create ? 'created' : 'updated'} ${p.key}'s profile at ${work}/new/${p.key}.md. Find everything wrong with it; be adversarial and exhaustive; do not edit any file.
Check: every number (totals = current total + new ring counts; recent; heads-up; rounding; frontmatter sums); every hand cited exists in the digest and is described correctly (cards, action, sizes, result); shown cards match "shows:" lines and nothing is guessed; the rulebook (frontmatter, section order and headings, the headline lines' exact wording, samples on every number, low-confidence marks, heads-up apart, exclusions, names, no em dashes, size and compaction); losses of value from the current profile; important reads in the digest or counts that the profile misses.
Return a numbered list of concrete corrections (what is wrong, the evidence, the fix), most important first.`

const CORRECT = (p, review) => `${COMMON(p)}

A checker reviewed ${p.key}'s profile at ${work}/new/${p.key}.md and found:
${review}

Verify each correction against the files yourself; apply every one that holds up and reject any that does not (say why). Rewrite ${work}/new/${p.key}.md in place, then confirm its frontmatter parses: python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]).read().split('---')[1])" ${work}/new/${p.key}.md
Return: the corrections applied, the ones rejected and why, the final headline stats, and a one-line "what changed" for jes.`

const results = await pipeline(
  players,
  (p) => agent(WRITE(p), { label: `write:${p.key}`, phase: 'Write', effort: 'high' }),
  (written, p) => agent(CHECK(p), { label: `check:${p.key}`, phase: 'Check', effort: 'high' }).then((review) => ({ written, review })),
  (r, p) => agent(CORRECT(p, r.review), { label: `correct:${p.key}`, phase: 'Correct', effort: 'high' })
    .then((final) => ({ player: p.key, written: r.written, final })),
)
return results.filter(Boolean)
