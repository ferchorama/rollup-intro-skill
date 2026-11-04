# Line selection

The edit is decided by the audio. Picture follows.

## Hard gates (a line that fails any gate is dead — do not "fix it in the edit")

| Gate | Threshold | How to check |
|---|---|---|
| Single continuous take | no internal cut | word timings: no gap > **0.35 s** between consecutive words |
| No filler | no `um / uh / er / ah / y'know / I mean / like` at the start or inside | verbatim pass, then listen |
| Complete utterance | starts and ends on a clause boundary | read the line cold; it must stand alone |
| Meaning intact | no reordering, no splicing of distant moments | compare against the surrounding 10 s |
| Not a prediction | no price, return, timeline or performance claim | flag to the guest's comms if it is |
| Delivery | energy, pace, conviction | rank the survivors; do not take the first cleaner line |

## Sentence-boundary rule

Never cut a host question internally. A guest answer may be trimmed only at a **clause
boundary** and only if the remainder still parses. If it does not parse, pick another take —
these sessions are 20-30 minutes long and always contain a spare.

## Finding candidates fast

1. Transcribe with word timings.
2. Flatten to one word stream with `[start, end, text]`.
3. Split at terminal punctuation into candidate spans.
4. Reject any span with an internal gap > 0.35 s or a filler token.
5. Filter by spoken duration: hook 3.5-6.5 s, question 3.5-6.5 s, answer 4-7 s, button 3.5-5 s.
6. Print them sorted by time. Read them aloud mentally and shortlist 3× what you need.

## Beat sheet that works for this show (30-34 s)

| Beat | Content | Target |
|---|---|---|
| 1 | guest hook — a claim, not a description | 3.5-6.5 s |
| 2 | host A question | 3.5-6.5 s |
| 3 | guest answer to it | 4-7 s |
| 4 | host B question | 3.5-6.5 s |
| 5 | dip to black → title card (name + title) | 2.5-3 s |
| 6 | guest button — the vision line | 4-5 s |
| 7 | logo sting | 1.5-2 s |

Hosts appear in beats 2 and 4: never save a host for the last third.

**Strict alternation (2026-11-03):** never two consecutive beats by the same speaker. With one
host the order is guest / host / guest / host / guest; a 4-beat hook > host > host > button is
NOT allowed (put a guest line between the host beats, or drop to hook > host > button).

## What makes a hook

- a **claim** the guest is willing to defend in one breath ("X is not enough to do Y")
- contrary to the obvious take, but not contrarian for sport
- forward-looking and serious; never a joke
- no numbers that need context

Weak hooks, in order of temptation: a process description ("it's going to happen slowly but
surely"), a product name, a tag question ("…right?"), a fragment starting mid-clause.

## Ranking the survivors

Score each 1-5 on: **energy** (does the voice lift), **pace** (is it tight or meandering),
**conviction** (does it land or hedge). Sum, sort. Take the top for the recommended set, and
keep two alternates so the client has a real choice.

Deliver three sets, each with a one-line reason and one recommendation. If a set only works
because of a subtitle, it does not work.

## Added 2026-10-30
- **Hook compliance (rule 028):** flag hooks about price, returns or performance; always cut
  a safer backup hook.
- **Single host (rule 020):** both host beats may come from one host; say so. They must not be
  consecutive (strict alternation, 2026-11-03).
- **Out-points from energy (rule 027):** never from the ASR word end; ≈45 ms fade.
- **Timing targets (rule 029):** hook ≈3.68 s, title card ≈19.52 s, end card ≈29.0 s,
  total ≈33.5 s.
- **Who said it:** if the transcript has no speakers, run `introkit.py diarize` before
  choosing lines; confirm the chosen ones with `voiceprint`.
