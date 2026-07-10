# μArch Review Round 1

Findings: calendar SRAM/replay requires explicit two stages; calendar must not
use BG storage; demotion must retain a remaining-leaf mask; BFM needs a
portable fallback.

## Rebuttal and disposition

All four findings accepted. `uarch.md` documents S0/S1, zero-buffer calendar,
release-once leaf accounting, and the C/RefC-compatible BFM. No rejected
finding remains.
