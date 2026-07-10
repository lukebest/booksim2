# Trial-1 BFM portability decision

Trial 1 approves the portable C cycle BFM as the SystemC substitute because a
SystemC runtime is not available in this workspace. This is an
`AGENT_ASSUMED` portability decision, not a claim that C is SystemC.

The substitute is acceptable for the Trial-1 calendar-replay intent because it
loads the versioned JSON vectors, instantiates the complete 6×8 topology,
applies H=7/V=9 link and one-cycle PE-ramp delays, scores terminal ejections,
and reports measured makespan. Run:

```sh
make -C bfm test_calendars
```

The C replayer does not replace an eventual SystemC/TLM integration or prove
the full tagged, three-cycle arithmetic pipeline. Its reduce/allreduce vectors
exercise Tier-B `COMBINE_*` slot decoding and structural paths; numerical
operand correctness and pipeline reservations require dedicated Phase-4/5
tests.
