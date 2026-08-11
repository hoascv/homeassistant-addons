# Test fixtures

`street.jpg` and `street_later.jpg` are frames 0 and 40 of `samples/data/vtest.avi`
from the [OpenCV repository](https://github.com/opencv/opencv), which is licensed
Apache-2.0. They are four seconds apart, which is what makes them useful twice
over: the scene contains people, a van and a car for the detector, and the gap
between them is real movement for the motion gate.

Real frames rather than synthetic images on purpose. A generated shape proves the
plumbing runs but not that preprocessing is right — get the letterbox padding or
the input scaling wrong and a synthetic test still passes while every real
detection collapses.

## No photographs of faces, ever

Face identification arrived in 1.11.0 and there is still **no picture of anybody's
face in this repository**, deliberately. Two reasons, and the second is the one
that settles it:

- Consent and provenance. `lena.jpg`, the historical default, fails on both, and
  research face sets (LFW, CASIA-WebFace) carry research-only terms — the same
  bar that ruled out AGPL model weights rules those out too.
- **An enrolled face is a biometric template.** Committing one would put it in
  git history permanently, in a public repository, where no later deletion can
  reach it.

So the face tests work without one:

- The **matching maths** — cosine, the margin rule, best-of-prints, the
  never-guess rule — is tested with vectors written by hand, the same way the
  YOLOX decode is.
- The **models** are exercised on `street.jpg`, whose one face-like region is 8×10
  px. That is genuinely useful: the most important assertion in the suite is that
  a distant street frame yields people and *no face worth identifying*, which is
  what a driveway camera actually looks like.
- **Enrolment** upscales a crop of that region until it clears a lowered floor.
  It is not a real face; it does not need to be. What is being tested is that a
  vector is produced, stored, matched and deleted.
- The **orchestration** — retry, budget, one write per visit — uses a fake
  identifier and no model at all.

Numbers from real faces belong in DOCS.md, measured on the operator's own camera
through `/api/faces/probe`. The images stay on their machine, exactly as the nine
driveway frames behind the nano-vs-tiny decision did.
