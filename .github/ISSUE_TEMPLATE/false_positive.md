---
name: False positive
about: The proofreader flagged a note that is actually correct
title: "[FP] "
labels: false-positive
---

**Which rule fired**
The `detector` field of the suggestion, e.g. `enharmonic_spelling`.

**Where**
Part, measure, staff, voice — the suggestion prints all four.

**What it said, and why it is wrong**
Paste the suggestion, then say what the page actually shows.

**The music**
A few bars around it, in any form: a screenshot of the scan, an excerpt of the MusicXML, or just
the pitches. If the piece is public domain, attaching it is ideal.

---

False positives are the highest-priority class of bug in this project — higher than missed
errors. A rule that fires on correct music is worse than no rule, because it teaches people to
stop reading the panel.
