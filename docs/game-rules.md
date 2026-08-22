# Game rules

This document describes the behavior implemented by the engine. The code in
`mafia_game.gameplay` remains the source of truth.

## Setup

A roster contains 6–12 uniquely named players. Roles are shuffled from the game
seed, making a run reproducible.

| Players | Mafia | Detective | Doctor | Civilians |
| ---: | ---: | ---: | ---: | ---: |
| 6 | 1 | 1 | 1 | 3 |
| 7 | 2 | 1 | 1 | 3 |
| 8 | 2 | 1 | 1 | 4 |
| 9 | 2 | 1 | 1 | 5 |
| 10 | 3 | 1 | 1 | 5 |
| 11 | 3 | 1 | 1 | 6 |
| 12 | 3 | 1 | 1 | 7 |

- Civilians, the Detective, and the Doctor form the Town team.
- Mafia members know one another from the start.
- Night 1 establishes private roles and teammates; no actions occur.
- Eliminated players no longer speak, vote, or use role actions.
- A player's role is revealed when they are eliminated or killed.

## Day

Each Day starts with a configurable number of discussion laps. The first speaker
rotates by living seat between Days. A player may speak within the configured
word limit or pass; if every living player passes during a lap, discussion ends
early.

After discussion, every living player submits a hidden ballot for another living
player or **No elimination**. Ballots are revealed together and a plurality
decides the result.

- A unique top choice is resolved immediately.
- A tie starts one runoff between the tied choices.
- Tied players give a defense before the runoff vote.
- No elimination remains available in the runoff only when it was part of the
  original tie.
- A tied runoff eliminates nobody.

## Night

Living Mafia members first receive a private planning turn. They then submit
hidden ballots for a living non-Mafia player or **No attack**. A unique plurality
selects the attack; a tie selects no target.

At the same time:

- The Detective may investigate one other living player they have not previously
  investigated. The private result is **Mafia** or **Not Mafia**.
- The Doctor may protect any living player, including themselves, except the
  target of their immediately preceding protection. Passing is legal and resets
  that restriction.
- If the Doctor protects the Mafia target, nobody dies.

All Night choices are locked before any outcome is revealed.

## Winning

The Town wins as soon as no living Mafia remain. The Mafia wins as soon as the
number of living Mafia is greater than or equal to the number of other living
players. Win conditions are checked after every Day elimination and Night
resolution.

## Invalid or failed agent turns

An isolated recoverable speech failure becomes a pass. Structured actions must
contain exactly one `target_seat` field and name a legal target (or `null` when
the no-action choice is legal). By default, the engine sends one repair prompt
after malformed output; a second malformed response safely falls back to no
action. Timed-out or broken provider sessions are discarded and recreated on
the next turn.

Permanent provider errors—such as invalid credentials, insufficient credit, or
an invalid request—abort immediately. Recoverable failures trip a circuit
breaker once as many consecutive model requests have failed as there are living
model players. A successful model response resets that counter.

A game also aborts when an entire Day and its following Night eliminate nobody
for `max_stalemate_days` consecutive Days (three by default). This bounds valid
but unproductive passing and prevents unlimited model calls.

## Information boundaries

Every event has an explicit visibility class:

| Visibility | Recipients |
| --- | --- |
| `public` | Every player and both replay views. |
| `player:N` | Only that seat; included in omniscient replay. |
| `mafia` | Mafia players; included in omniscient replay. |
| `omniscient` | Omniscient replay only. |
| `internal` | Persistence and diagnostics only; never shown in replay. |

The public replay view and default search exclude private information. The
omniscient replay view includes role and Night-action events, but still omits
internal provider diagnostics.
