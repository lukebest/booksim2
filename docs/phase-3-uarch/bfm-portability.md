# Trial-2 BFM portability decision

Trial 2 approves the portable C cycle BFM as the SystemC substitute because a
SystemC runtime is not available in this workspace. This is an
`AGENT_ASSUMED` portability decision, not a claim that C is SystemC.

The substitute loads versioned JSON vectors, instantiates the complete 6×8
topology, applies H=7/V=9 link and one-cycle PE-ramp delays, scores terminal
ejections, and reports measured makespan. Run:

```sh
make -C bfm test_calendars
```

**Tier A:** reduce/allreduce vectors exercise gather/forward and
`CAL_OP_PE_HANDOFF` tags. The router performs **no** arithmetic; PE-local
compute is outside the BFM datapath (handoff counter only). There is no
three-cycle combine pipeline to prove.
