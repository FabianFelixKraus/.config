#!/usr/bin/env python3
"""Innovations GmbH — feature-request handling process. Emits an importable .bpmn."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bpmn_builder import Bpmn

b = Bpmn(defs_id="Definitions_inno", process_id="Process_Inno")

# ---- pools + lanes ----
b.pool("Part_Customer", "Customer", 190, 70, 1760, 60, blackbox=True)
b.pool("Part_Inno", "Innovations GmbH", 190, 150, 1760, 700, process="Process_Inno")
b.lane("Lane_PM", "Product management", 220, 150, 1730, 190,
       ["Start_FR","Task_CreateTicket","GW_UIchanges","Task_UpdateTicket",
        "GW_UIinvolved","Task_Review","GW_Approved","End_Done"])
b.lane("Lane_Designer", "Start-Up designer", 220, 340, 1730, 170,
       ["Task_CreateUISpec","Task_UpdateUISpec"])
b.lane("Lane_Dev", "Development team", 220, 510, 1730, 340,
       ["GW_Backlog","Sub_Rank","GW_Clear","GW_Planned","Sub_Implement","Boundary_Sprint"])

# ---- PM lane (row y=245) ----
b.node("Start_FR","startEvent",250,227,36,36,"Feature request received",evdef="message")
b.node("Task_CreateTicket","task",320,205,100,80,"Create ticket")
b.node("GW_UIchanges","exclusiveGateway",480,220,50,50,"UI changes?",default="F4")
b.node("Task_UpdateTicket","task",1120,205,100,80,"Update ticket")
b.node("GW_UIinvolved","exclusiveGateway",1290,220,50,50,"UI involved?",default="F11")
b.node("Task_Review","task",1560,205,100,80,"Review ticket")
b.node("GW_Approved","exclusiveGateway",1720,220,50,50,"Requirements met?",default="F19")
b.node("End_Done","endEvent",1830,227,36,36,"Feature request implemented")

# ---- Designer lane (row y=425) ----
b.node("Task_CreateUISpec","task",455,385,100,80,"Create UI specification")
b.node("Task_UpdateUISpec","task",1120,385,100,80,"Update UI specification")

# ---- Dev lane (row y=640) ----
b.node("GW_Backlog","exclusiveGateway",620,615,50,50,"Backlog")
b.node("Sub_Rank","adHocSubProcess",730,565,370,150,"Rank ticket")
b.node("Task_Estimate","task",750,600,100,55,"Estimate effort",parent="Sub_Rank")
b.node("Task_Prioritize","task",865,600,100,55,"Prioritize ticket",parent="Sub_Rank")
b.node("Task_AddWork","task",980,600,100,55,"Add technical work items",parent="Sub_Rank")
b.node("GW_Clear","exclusiveGateway",1150,615,50,50,"Ticket & specs clear?",default="F13")
b.node("GW_Planned","exclusiveGateway",1290,615,50,50,"Ticket planned?",default="F14")
b.node("Sub_Implement","subProcess",1400,600,120,80,"Implement ticket",expanded=False)
b.node("Boundary_Sprint","boundaryEvent",1442,662,36,36,"End of sprint reached",
       evdef="timer",timer="P2W",attached_to="Sub_Implement")

# ---- data objects ----
b.data_object("DObj_Ticket"); b.data_object("DObj_UISpec")
b.data_ref("DOR_T_created","Ticket [created]",352,290,36,50,"DObj_Ticket")
b.data_ref("DOR_UI_created","UI specification [created]",487,455,36,50,"DObj_UISpec")
b.data_ref("DOR_T_impl","Ticket [implemented]",1542,735,36,50,"DObj_Ticket")
b.data_ref("DOR_T_done","Ticket [done]",1592,290,36,50,"DObj_Ticket")
b.data_assoc("DA1","Task_CreateTicket","out","DOR_T_created",[(370,285),(370,290)])
b.data_assoc("DA2","Task_CreateUISpec","out","DOR_UI_created",[(505,465),(505,455)])
b.data_assoc("DA3","Sub_Implement","out","DOR_T_impl",[(1510,660),(1560,735)])
b.data_assoc("DA4","Task_Review","out","DOR_T_done",[(1610,285),(1610,290)])

# ---- sequence flows (explicit waypoints; loops routed via a bottom channel) ----
b.flow("F1","Start_FR","Task_CreateTicket",wps=[(286,245),(320,245)])
b.flow("F2","Task_CreateTicket","GW_UIchanges",wps=[(420,245),(480,245)])
b.flow("F3","GW_UIchanges","Task_CreateUISpec","yes",cond="uiChanges",
       wps=[(505,270),(505,425),(455,425)])
b.flow("F4","GW_UIchanges","GW_Backlog","no",wps=[(530,245),(600,245),(600,640),(620,640)])
b.flow("F5","Task_CreateUISpec","GW_Backlog",wps=[(505,465),(505,640),(620,640)])
b.flow("F6","GW_Backlog","Sub_Rank",wps=[(670,640),(730,640)])
b.flow("F7","Sub_Rank","GW_Clear",wps=[(1100,640),(1150,640)])
b.flow("F8","GW_Clear","Task_UpdateTicket","unclear",cond="unclear",
       wps=[(1175,615),(1090,615),(1090,245),(1120,245)])
b.flow("F9","Task_UpdateTicket","GW_UIinvolved",wps=[(1220,245),(1290,245)])
b.flow("F10","GW_UIinvolved","Task_UpdateUISpec","yes",cond="uiInvolved",
       wps=[(1315,270),(1315,425),(1220,425)])
b.flow("F11","GW_UIinvolved","GW_Backlog","no",
       wps=[(1340,245),(1360,245),(1360,790),(645,790),(645,665)])
b.flow("F12","Task_UpdateUISpec","GW_Backlog",
       wps=[(1220,425),(1240,425),(1240,775),(645,775),(645,665)])
b.flow("F13","GW_Clear","GW_Planned","clear",wps=[(1200,640),(1290,640)])
b.flow("F14","GW_Planned","Sub_Implement","planned",wps=[(1340,640),(1400,640)])
b.flow("F15","GW_Planned","GW_Backlog","not planned",cond="notPlanned",
       wps=[(1315,665),(1315,760),(645,760),(645,665)])
b.flow("F16","Sub_Implement","Task_Review",wps=[(1520,640),(1610,640),(1610,285)])
b.flow("F17","Boundary_Sprint","GW_Backlog",wps=[(1460,698),(1460,745),(645,745),(645,665)])
b.flow("F18","Task_Review","GW_Approved",wps=[(1660,245),(1720,245)])
b.flow("F19","GW_Approved","End_Done","approved",wps=[(1770,245),(1830,245)])
b.flow("F20","GW_Approved","GW_Backlog","rejected",cond="rejected",
       wps=[(1745,270),(1745,800),(645,800),(645,665)])

# ---- message flow ----
b.message("M1","Part_Customer","Start_FR",wps=[(268,130),(268,227)])

out = os.path.join(os.path.dirname(__file__), "innovations-feature-request.bpmn")
with open(out, "wb") as fh: fh.write(b.build())
print("wrote", out)
