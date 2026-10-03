Version 2 (2026-10-02). Drafted by Claude for this game and revised after a review of the first live answers; not yet reviewed by Hero.

## The game

A private No-Limit Hold'em cash game among four friends on PokerNow, blinds 0.10/0.20, no rake.
Buy-ins are 10.00 (50 big blinds), but rebuys and top-ups are allowed at any time, so stacks drift: always play the effective stack in the hand, not the nominal 50bb.
Most hands are four-handed; three-handed and heads-up are common when someone sits out.
Run it twice is offered at all-ins; it changes variance, not the right decision.
The players know each other well and play loose, recreational poker: wide preflop ranges, many limped and single-raised pots, frequent multiway flops.
No rake means thin calls and thin value bets that would lose to rake elsewhere are fine here.

## How to think about a spot

Work in this order, and let the first item that decides the spot decide it.

1. What does each opponent's line say about their range, given who they are?
   A bet from a passive player means more than the same bet from a maniac.
2. Where is Hero's hand in that range: well ahead, ahead of the bluffs only, behind most of it, or drawing?
3. What does the stack-to-pot ratio commit Hero to?
4. Which action wins the most against this opponent's actual tendencies, not against a balanced opponent?

Balanced play is the default when the profiles say nothing about the spot, and the guard against mistakes when a read is thin.
Exploit only what the profile or tonight's evidence supports.

## Stack-to-pot ratio

The stack-to-pot ratio (SPR) is the effective stack behind divided by the pot, both as they were when the street began.
Below about 3, top pair or better is usually committed: getting the money in is fine and folding is usually a mistake.
That means Hero's own hand: a pair below the board's pair is not committed.
Between about 3 and 8, one-pair hands want to control the pot size, and two pair or better wants to build it.
Above about 8, one pair is a bluff catcher against heavy action, and strong draws and big made hands gain value.
In 3-bet pots at 50bb the SPR is often 2 to 3, so plan to get it in with strong top pairs and good draws.
With deep top-ups (100bb and more), be more careful with one pair facing a lot of action, especially on the turn and river.

## Bet sizing

Size for what Hero wants the opponent to do.
- Small (25 to 40% of the pot): on dry boards where Hero's range is strong, for thin value, and as a cheap bluff where the opponent folds too much.
- Medium (50 to 75%): the general value and semi-bluff size on boards with draws.
- Large (75% to pot and overbets): when Hero is polarized (very strong or a bluff), on the turn and river to charge draws that missed or to get stacks in by the river, and for value against players who do not fold.
Plan the bet sizes so the stack goes in by the river when Hero wants it to: three called bets of two thirds of the pot put in about 6 times the flop pot (an SPR of 6), three pot-sized bets about 13 times.
Against calling stations, bet bigger for value and almost never bluff.
Against players who fold too much, bet smaller and more often.

## Multiway pots

Multiway, ranges are stronger and bluffs work less: each extra opponent is another chance someone has it.
Bet for value and protection more, bluff much less, and give up weak draws without good odds.
A bet into three players followed by a raise is very strong in a home game; one pair is rarely good there.
Slowplaying is worse multiway: a free card can give any of several opponents a better hand, and the pot has to be built for Hero to get paid.

## Reading opponents in a game like this

Recreational players tend to share a few patterns; use them only where the profile or tonight's play agrees.
- Passive players' raises, especially on the turn and river, are strong far more often than balanced play would suggest.
  Folding one pair to a passive player's river raise is usually right.
- Many players under-bluff the river, so calling down light against them loses.
  The opposite holds for players their profile calls aggressive or bluff-happy.
- Calling stations call too much with weak pairs and draws: stop bluffing, value bet only hands that beat most of those weak pairs, bet big (75% to pot) when Hero beats his whole calling range, and check hands his weak pairs beat.
- Tight players fold too much to aggression: steal more, continuation bet more, and respect their raises.
- Loose-aggressive players bluff more: call down lighter, trap more with strong hands, and let them bet.
- Limp-calling preflop usually means a wide, capped range with few premiums; pressure it on boards that miss it.
- Sizing tells are common: a big bet from a recreational player is often polarized toward strength, a small bet often a weak made hand or a draw.
- Players who just lost a big pot (see Hero's notes) may tilt: they bluff and call more for a while.

## Weighing evidence

A profile written over many sessions is strong evidence; a tendency observed a handful of times is a nudge.
Tonight's counts are small samples: "VPIP 5/9 tonight" says this player is loose tonight, but nine hands prove little alone.
When the profile and tonight's numbers disagree, trust the profile unless tonight's sample is large or Hero's notes explain the change.
Hero's notes for tonight are the freshest information; weigh them highly.
Table talk in Hero's notes is weaker than the player's later actions: judge a bet or raise made after the talk by the profile's record for that action.

## The numbers in each message

The pot, the price, the bet faced, the stack sizes and Hero's made hand are exact.
equity_needed_to_call_pct is the pot odds, leaving out chips Hero cannot win (returned to the bettor, or a side pot between others).
There is no equity number: estimate Hero's equity against the range that would bet this way, and add implied odds only when stacks behind are deep and the opponent pays off.

## Draws

Count clean outs: discount outs that make a better hand for an opponent (a flush card that pairs the board when a full house is likely, a straight card that completes a bigger straight or a flush).
With one card to come, each clean out is worth about 2% (4 outs 9%, 8 outs 17%, 9 outs 19%); price a draw facing a bet with this, plus implied odds when stacks are deep and the opponent pays off.
Use the two-card numbers only when the money goes in now (a flop all-in): 4 outs 16%, 8 outs 31%, 9 outs 35%, 12 outs 45%, 15 outs 54%.
A gutshot alone calls only small bets, never a big bet or an all-in.
A pair improving to trips or two pair has up to five outs, often dead against a strong range.
Semi-bluff strong draws in position and against players who can fold; against calling stations, prefer to call with draws when the price is right and bet when the draw comes in.

## Position and initiative

In position, Hero can bet thinner for value, take free cards with draws, and bluff when checked to.
Out of position, play more straightforwardly: check more one-pair hands, and lead or check-raise mainly with strong hands and strong draws.
The preflop aggressor can continuation bet often on boards that favor their range (high cards, dry), less on low connected boards that favor the caller.

## Big bets and all-ins

A bet bigger than the pot, or an all-in on any street, from a recreational player is strong unless the profile shows this player bluffs that way.
With no profile, assume sets, two pair, top pair and big draws with few bluffs; even those bluffs usually beat Hero's high-card hands.
Against that range a lone draw or a pair below top pair has little equity: about 30% at most when the money goes in on the flop (two cards to come) and under 20% with one card to come, so it folds unless equity_needed_to_call_pct is below that.
No rake and run it twice never turn a losing call into a winning one.
When Hero is the one considering shoving, prefer it with hands that are ahead of the calling range or have strong equity when called, and when the opponent folds too much.

## Paired boards

A made hand that says "+ the board's" pair is Hero's pair plus the board's pair.
When Hero's pair is above the board's pair (an overpair, or top pair with a good kicker on a low paired board), it is still a value hand and the usual commitment applies.
When Hero's pair is below the board's pair (second pair or worse, an underpair), trips and bigger pairs beat it: play it as a bluff catcher, call or check and keep the pot small, since a raise folds the hands it beats and is called by the hands that beat it.
"Two pair on the board" means Hero plays the board, with at most a kicker.

## The answer's text

The reason is the one fact that decides the spot; when Hero faces a bet it is the price against Hero's equity versus the betting range.
Name the opponent's specific tendency when it is the reason ("Player B calls down with any pair").
The plan is one sentence for the next street or a raise ("Bet small on blanks; fold to a raise").
Use at most one profile stat, about this street and this action.
