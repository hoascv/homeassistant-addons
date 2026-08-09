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
