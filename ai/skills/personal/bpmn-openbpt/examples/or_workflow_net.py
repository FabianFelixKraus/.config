#!/usr/bin/env python3
"""OR-gateway workflow net (translational semantics: OR = XOR over the non-empty
subsets, the multi-element subset being an AND block). Emits native .ptn + .pnml."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from petrinet_builder import PetriNet

pn = PetriNet("OR gateway - workflow net", model_id="model_1")

# entry
pn.place("p_i", "i", 78, 300, tokens=1)
pn.transition("t_assess", "Assess order", 170, 300)
pn.place("p2", "p2", 262, 300)
# subset {Reserve} — top
pn.transition("t_tauR", "reserve only", 360, 150, silent=True)
pn.place("p_r1", "reserve chosen", 470, 150)
pn.transition("t_res1", "Reserve stock", 580, 150)
# subset {Deliver} — bottom
pn.transition("t_tauD", "deliver only", 360, 450, silent=True)
pn.place("p_d1", "deliver chosen", 470, 450)
pn.transition("t_del1", "Arrange delivery", 580, 450)
# subset {Reserve, Deliver} — middle AND block
pn.transition("t_tauRD", "both (fork)", 360, 300, silent=True)
pn.place("p_r1b", "reserve chosen", 470, 250)
pn.place("p_d1b", "deliver chosen", 470, 350)
pn.transition("t_res2", "Reserve stock", 580, 250)
pn.transition("t_del2", "Arrange delivery", 580, 350)
pn.place("p_r2b", "reserved", 690, 250)
pn.place("p_d2b", "delivered", 690, 350)
pn.transition("t_sync", "sync (join)", 800, 300, silent=True)
# merge + confirm + end
pn.place("p_pj", "pj", 900, 300)
pn.transition("t_confirm", "Confirm", 992, 300)
pn.place("p_o", "o", 1084, 300)

for s, t in [
    ("p_i", "t_assess"), ("t_assess", "p2"),
    ("p2", "t_tauR"), ("t_tauR", "p_r1"), ("p_r1", "t_res1"), ("t_res1", "p_pj"),
    ("p2", "t_tauD"), ("t_tauD", "p_d1"), ("p_d1", "t_del1"), ("t_del1", "p_pj"),
    ("p2", "t_tauRD"), ("t_tauRD", "p_r1b"), ("t_tauRD", "p_d1b"),
    ("p_r1b", "t_res2"), ("t_res2", "p_r2b"), ("p_d1b", "t_del2"), ("t_del2", "p_d2b"),
    ("p_r2b", "t_sync"), ("p_d2b", "t_sync"), ("t_sync", "p_pj"),
    ("p_pj", "t_confirm"), ("t_confirm", "p_o"),
]:
    pn.arc(s, t)

here = os.path.dirname(__file__)
pn.save(os.path.join(here, "or-workflow-net.ptn"), os.path.join(here, "or-workflow-net.pnml"))
print("wrote or-workflow-net.ptn + .pnml  |  places:",
      sum(1 for n in pn.nodes.values() if n["kind"] == "place"),
      "transitions:", sum(1 for n in pn.nodes.values() if n["kind"] == "trans"),
      "arcs:", len(pn.arcs))
