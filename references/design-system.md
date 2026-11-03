# Design system

Measured off the reference render and the brand assets in the project folder.

## Canvas

- 1920 × 1080, 30.000 fps, progressive, BT.709, `yuv420p`.
- Master audio 48 kHz stereo, −14 LUFS integrated, ≤ −1.0 dBTP.

## Type

| Role | Face | Used for | Colour |
|---|---|---|---|
| Claim | **Anton** | the loud line: `IS NOT ENOUGH`, `MASSIVE` | white `#FFFFFF` |
| Keyword | **Anton** | one word per line, inline with the white | gold `#E8B23A` |
| Connective | **Playfair Display Italic** | the quiet spoken phrase around the claim | cream `#F2E7D5` |
| Clause / credit | **Barlow Condensed Bold** | supporting clause, name/title, tagline | white or gold |
| Small print | Barlow Condensed 30 px | venue credit under the title card | dim `#C6C2BA` |

**The rule that matters:** mix faces *inside one spoken sentence*. A single line of speech
runs `playfair italic → anton white → anton gold → barlow` in that order, because that is how
the reference builds emphasis. Never set one face per line for the whole cut.

Sizes that held at 1080p: claim 150-186 px, keyword 150-186 px, connective 44-58 px,
clause 58-78 px, credit 54-56 px.

## Line breaking — forbidden moves

- no hyphenation, ever
- no word split across lines
- no vertical or rotated type
- no line that must shrink below ~60 % of its intended size to fit — reframe instead

Fit by **paging**: show one phrase at a time and wrap at balanced word boundaries inside a
`maxw` derived from the measured face box. Never split or hyphenate a word; shrink only a
single word that cannot fit. (Superseded 2026-10-30: "shrink the line, never wrap".)

### Sizes (1920×1080, approved Zach Pandl v2, measured)

| Face | Em size | Measured |
|---|---|---|
| Playfair Display Italic (connective) | 110–130 px | x-height 58 / 63 / 69 px at 110 / 120 / 130 |
| Anton (claim, white; keyword gold) | 146–230 px | cap 124 / 167 / 197 px at 146 / 195 / 230 |
| Barlow Condensed Bold (support) | ≈81–101 px | ≈1.3× the earlier 62–78 px |

Reference IMG_9231 @ 15.0 s measured Playfair x-height 34 px, Anton cap 133 px white /
171 px gold, left margin 122 px. The user asked for bigger script type than the reference,
so v2 Playfair is ~1.7–2.0× the reference; keep Anton cap heights ≥ the reference.

## Layout

- Type sits on the side **opposite the speaker**, computed from the measured face box.
- Left blocks: `x = 96`, max width 900 px. Right blocks: right edge `x = W − 96`, max width 660 px.
- Vertical centre 520-540 px; blocks are centred on that line, leading `line_height + 28 px`.
- If two faces sit either side, move the block to the dark band **below** the faces.

## Scrim

A horizontal + bottom gradient is what makes white type survive a busy plate:

```
ramp  = clip((0.68 − x/W) / 0.68) ^ 1.1        # left side
        clip((x/W − 0.36) / 0.64) ^ 1.1        # right side
alpha = 0.88 · ramp + 0.30 · clip((y/H − 0.52)/0.48) ^ 1.3
alpha = min(alpha, 0.95)
```

**Keep the scrim's peak alpha below 245/255.** At 255 it is indistinguishable from text to an
alpha-based QC mask and will corrupt every measurement.

## Motion

Per word, timed to its spoken onset:

```
p      = ease_out_back((t − t_word) / 0.30)
scale  = (1 − amp) + amp · p          # amp 0.20 normal, 0.30 keyword
rise   = (1 − p) · 30 px
slide  = (1 − p) · (−16 px | +12 px)  # alternate by word index
alpha  = clamp((t − t_word) / 0.12)
drift  = −1.1 · t                     # block drifts up over the shot
```

Cards: 0.045× push over their duration, text revealed in 0.30 s steps, gold rule expanding
from 0 → 160 px.

Transitions: **dip to black, 10 frames (0.333 s)**, never a crossfade.

## Cards

**Title card** — navy gradient `#080B14 → #101522` with a warm gold glow, colour portrait
feathered on one side (never grayscale: a B&W portrait on black reads as a memorial), name in
Anton, gold rule, `CO-FOUNDER, <COMPANY>` in Barlow, venue credit bottom-left.

**End card** — same navy family, brand logo 780 px wide with a 1.03× push, tagline in gold
Barlow. Do not end on white-on-black.

## Colour discipline

One accent only. Gold marks exactly one word per line, the rule under the name, and the
tagline. Everything else is white or cream on a darkened plate.
