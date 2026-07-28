#!/usr/bin/env python3
"""Blood-sample lab process as a P/T net with weighted arcs, multi-instance.
Batch of 5 -> weight-5 arcs; two centrifuges -> resource place with 2 tokens."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from petrinet_builder import PetriNet

pn = PetriNet("Blood-sample lab process (weighted P/T net)")

pn.place("p_arrived", "patient arrived", 80, 200, tokens=10)   # tokens = patients (instances)
pn.transition("t_draw", "Draw blood & send", 185, 200)
pn.place("p_lab", "sample at lab", 300, 200)
pn.transition("t_start", "Place 5 & start centrifuge", 420, 200)
pn.place("p_run", "centrifuging", 555, 200)
pn.transition("t_out", "Take samples out", 670, 200)
pn.place("p_cent", "centrifuged", 800, 200)
pn.transition("t_analyze", "Analyze sample", 910, 200)
pn.place("p_ana", "analyzed", 1020, 200)
pn.transition("t_ok", "Send results", 1130, 130)
pn.transition("t_err", "Send error notification", 1130, 270)
pn.place("p_done", "terminated", 1260, 200)
pn.place("p_cf", "centrifuges available", 545, 360, tokens=2)  # resource: 2 centrifuges

pn.arc("p_arrived", "t_draw")
pn.arc("t_draw", "p_lab")
pn.arc("p_lab", "t_start", weight=5)          # batch of five  (weighted)
pn.arc("p_cf", "t_start")                     # take one free centrifuge
pn.arc("t_start", "p_run")
pn.arc("p_run", "t_out")
pn.arc("t_out", "p_cent", weight=5)           # five samples come back out (weighted)
pn.arc("t_out", "p_cf")                        # return the centrifuge
pn.arc("p_cent", "t_analyze")
pn.arc("t_analyze", "p_ana")
pn.arc("p_ana", "t_ok")
pn.arc("p_ana", "t_err")
pn.arc("t_ok", "p_done")
pn.arc("t_err", "p_done")

here = os.path.dirname(__file__)
pn.save(os.path.join(here, "blood-sample-net.ptn"), os.path.join(here, "blood-sample-net.pnml"))
print("wrote blood-sample-net.ptn + .pnml | places:",
      sum(1 for n in pn.nodes.values() if n["kind"] == "place"),
      "transitions:", sum(1 for n in pn.nodes.values() if n["kind"] == "trans"),
      "arcs:", len(pn.arcs))
