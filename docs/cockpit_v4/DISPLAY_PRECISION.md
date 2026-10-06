# V4 display precision policy

The rule in one line: **CreditProbe computes the canonical value, CreditProbe
chooses how many decimals a reader sees, and the analyst may send either the
canonical value or that value correctly rounded — nothing else.**

## Why the policy exists

A live run computed exposure at default by sector correctly and had its
answer refused:

```
asserted   40599.17
canonical  40599.1736630513815
```

`40,599.17` is how a credit officer writes that number. What refused it was a
relative tolerance of 1e-9, introduced with a comment claiming it absorbed a
rounded display value. It does not: rounding five significant digits to two
places moves the figure by **9.02e-8** relative, ninety times that bound.

Widening the tolerance would have been the wrong fix. At 1e-6, a figure wrong
by more than SAR 0.04 million passes on this book, and at 1e-4 one wrong by
SAR 4 million does. Presentation would have been bought with arithmetic.

## Canonical vs display

| | |
| --- | --- |
| **Canonical** | Full precision, computed by CreditProbe from executed evidence. Stored, recomputed against, never rounded away. |
| **Display** | The canonical value quantized to a permitted precision, `ROUND_HALF_UP`. What a reader sees. |

Validation:

```
expected_display = quantize(canonical, allowed_precision)
asserted ∈ { canonical, expected_display }
```

A cross-check is valid as the canonical value itself, or as that value
correctly rounded to any number of places: the analyst may round its own
working however it likes, and that is a question about whether the number is
RIGHT. How the figure is written is a separate question and the display
class answers it.

A third number that merely *rounds* to the right answer — `40599.1699`, which
rounds to `40599.17` and is not this value at any precision — is still
refused. Rounding to something correct is not being it.

The published figure is rendered from the canonical verdict, not from the
analyst's string, so what reaches the reader is CreditProbe's rounding of
CreditProbe's arithmetic.

## Precision by unit

Read from the **unit**, never from a claim's name: `total_ead` and
`ead_share` differ by unit, not by spelling.

| Unit class | Decimals | Governed | Example |
| --- | --- | --- | --- |
| Money (`SAR million`) | 0 | yes | `SAR 40,599 million` |
| Percent | 2 | yes | `25.25%` |
| Probability (`probability_0_1`) | 2 | yes | `4.33%` |
| Percentage point | 2 | yes | `1.31 pp` |
| Ratio | 2 | yes | `1.57x` |
| Count | 0 | yes | `59` |
| Categorical | — | yes | Stage 2, BBB+ |
| Unknown unit | 2 | no | `1.57` |

**Governed means the class decides and nobody else does.** An analyst may
send `display_precision`; for a governed class it changes nothing. It is
ignored mechanically — never refused — because refusing it would send a
correct analysis back for a model turn to alter two characters of
presentation.

This replaces a wider policy under which money permitted `0, 1, 2, 3`. That
let one answer read `SAR 7,013.12 million` in its prose above a table and a
chart reading `SAR 7,013 million`: three renderings of one cell, all legal,
on one screen. A display class anyone may override is not a policy, it is a
default.

`UNKNOWN` is the one class not governed, and for a reason: nothing named the
unit, so there is no business rule to apply and the declaration is the only
signal available. It is still bounded, and machine precision still never
reaches a reader.

A metric that genuinely needs different places gets them by being classified
differently — a coverage ratio is not an amount — or by a governed rule added
to `PERMITTED` and `DECIMALS`, in one place, deliberately.

## Currency and scale

The V4 demonstration book is a **Saudi** corporate portfolio.

- Reporting currency: **SAR**
- Canonical amount scale: **million**
- Canonical evidence stays in SAR million. An executive display may scale a
  large total to `SAR 12.5bn`, but the evidence underneath does not move.

Scale is read, never assumed. `SAR million` and `SAR bn` are the same currency
at a thousandfold difference, and the pair is checked together — a rounding
fix must never admit a 1,000× monetary error or a 100× percentage one.
`percent`, `percentage point` and `ratio` are three units, not one.

## Zero and notation

No user-facing value is ever in scientific notation. `0E+12` displays as
`0.00`. Negative zero is normalised: a movement of `-0.001` reads as
`SAR 0.00 million`, never `SAR -0.00 million`, which would read as a loss too
small to name rather than as nothing.

## Tables and charts

A table names an artifact and columns and is served the stored rows, so it
cannot misreport a number; its columns are checked against the artifact. A
chart's columns are checked the same way, and where an answer claims a
ranking and publishes a chart, the charted measure must actually be ordered.

Table, chart and narrative reconcile because all three are formatted from the
same canonical values at the same declared precision.
