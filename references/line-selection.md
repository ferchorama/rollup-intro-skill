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

**Start with `signals` (read-only, seconds):**

```
introkit.py transcribe --src EP.mp4 --out work/ep.json --formats json --enroll guest=S:D --enroll andy=S:D ...
introkit.py signals --transcript work/ep.json --speaker guest [--top 12] [--out work/ep.signals.json]
```

It splits the guest's words into sentences, scores 1-3 sentence spans of 3-10 s and prints
about 12 non-overlapping candidates (JSON + table): **E** energy (RMS of the span vs the
episode's speech level, plus loud-peak share), **P** pause before the line (1 s = full), **Q**
ends on a question, **W** contrast / power words, **N** numbers, **L** length fit (best 4.5-8 s),
a hook-type hint and flags (weak opener, ends on a conjunction, starts / ends mid-sentence,
fillers). Weights: E .28, P .12, Q .08, W .22, N .12, L .18, minus flag penalties. Speakers
come from the transcript labels; with an unlabelled transcript pass `--enroll` voices (the
spans are voiceprinted) or it lists every speaker and says so. An .srt works but its times are
approximate. It is a shortlist, not a decision: in/outs still come from the audio (`vet`),
the choice from the scoring below. Kash Razzaghi 2026-11-12: 6 of its top 12 were lines a
human had already picked (swipe/built, surpass, fraction, new class, foundation/gas).

Manual fallback:

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

**Open with the guest (user rule, 2026-11-13):** intros always open with the guest, never
with a host. Beat 1 is the guest hook. `assemble` WARNs if the first beat is not the guest.

## What makes a hook

**Viral rule (2026-11-16, user rule):** a hook must be provocative, surprising or quotable and
make sense to a stranger with no context: a contrarian claim, a confession, a stake, a vivid
image. Reject bland, definitional or generic lines however clean they are. Canonical reject:
"AI is a technology, crypto is a technology" (true, flat, no tension). Canonical keep (Lior
B2): "Nobody's incentivized to share or monetize their own data, because then that just means
I'm giving up my secrets." Apply the same test to the host lines and the button. Order: guest
hook first, strict speaker alternation, equal host lines per host.

- a **claim** the guest is willing to defend in one breath ("X is not enough to do Y")
- contrary to the obvious take, but not contrarian for sport
- forward-looking and serious; never a joke
- no numbers that need context

Weak hooks, in order of temptation: a process description ("it's going to happen slowly but
surely"), a product name, a tag question ("…right?"), a fragment starting mid-clause.

## Openers (what a line may start on) — `vet` WARNs on these

A beat must start where a stranger can follow it. Do not start on:
- **a weak opener:** "yeah", "yes", "so", "and", "but", "or", "because", "well", "okay", "also",
  "plus", or a filler ("um", "uh"). Move the in-point to the next clause;
- **a bare pronoun** ("he", "she", "they", "it", "that", "this", "these", "those", "that's",
  "they're") unless the referent is inside the same line;
- **a back-reference:** "as I said", "like I mentioned", "as we discussed", "to your point",
  "you mentioned", "going back to" (anywhere in the line: it points at something the viewer
  never saw).

`vet` prints `edit: starts on ...` for the beat's first piece (pieces `b`, `c` of a split beat
are not checked). It is a WARN: it never moves an in-point.

## Where a line may stop — `vet` WARNs on these

`vet` scores the gap after every beat's last word (column `cut after`): **speaker change** 0.9,
**sentence end** 0.75-0.95 (longer pause, higher), **pause** >= 0.6 s without punctuation 0.55,
**breath** 0.2-0.6 s 0.4, **mid-clause** < 0.2 s 0.1. It WARNs when the line ends mid-sentence
(anything below sentence end) or on a conjunction / function word ("and", "but", "so",
"because", "which", "of", "the"...): the D3 "accelerant for digital assets and" problem. Split
pieces inside one beat show `SPLIT`, continuous joins `JOIN` (judged by ear). This sits on top
of the acoustic-end out-point and never changes it. Pass `--transcript` (speaker-labelled
JSON) so a speaker change after the out-word is seen.

## Scoring checklist (every hook candidate, before choosing A/B/C)

Score each candidate hook 0-5 on four axes, compared against the OTHER candidates (re-read
the whole list and recalibrate: a 5 is the best line in this episode, not in the abstract):

| Axis | 5 means |
|---|---|
| **Hook strength** | the first 3 s alone make you want the next 3 s |
| **Curiosity** | opens a question the episode answers (why / how / what next) |
| **Clarity out of context** | a stranger understands every word: no unresolved "it", no jargon that needs setup |
| **Shareability** | someone would clip and post it: quotable, specific, defensible |

Total /20 (x 1.25 for /25). Give each a **hook type**: bold claim, number / stat, contrarian,
prediction, story, question, definition / reframe. Rules:
- **A, B and C open on three different hook types** (e.g. A number/stat, B prediction, C
  contrarian), each the best-scoring line of its type;
- a hook with clarity <= 2 is out, whatever its total;
- record the scores (optional) in sets.json on the beat:
  `"hook": {"type": "number/stat", "strength": 5, "curiosity": 4, "clarity": 4, "share": 5}`
  (assemble ignores the key; it documents the choice for LINE_SETS.md).

`signals` gives `type_hint` and the raw features; the scores above are judgement.

## Stranger test (once per set, before `frame` / `build`; text only)

`vet` ends with `SCRIPT (stranger test ...)`: the set as plain text, one line per beat. Read
it once as someone who never saw the episode and check:
1. **Unresolved references:** any he / it / that / "as I said" whose referent is not in the set?
2. **Chopped beats:** does any beat start or end mid-thought (vet `edit:` lines help)?
3. **Weakest beat:** which one would you drop? If it is obvious, replace it now.
4. **The closer lands:** does the last line finish a thought and sound like an ending?
5. **Host lines alone:** does each host question make sense with no earlier context?
Fix by choosing other spans, then re-vet. No render is needed for this step.

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
