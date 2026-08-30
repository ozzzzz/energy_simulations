# Sim-1 — architecture and realisation

> Документ описывает то, что реально построено. Где реализация разошлась с
> исходным планом — [§9](#9-changed-from-the-original-plan).

Code: [`app/simulations/sim1/`](../app/simulations/sim1/).
Run: `uv run app sim1-run --scenario normal --open`
(or any scenario from [§7](#7-scenarios)).

---

## 1. What it is

A small AI data centre: 4 GB300 NVL72 racks, 540 kW of IT, 690 kW at the meter,
behind a fully redundant (2N) electrical chain and a liquid cooling plant.

| | |
|---|---|
| IT | 4 racks × 135 kW nominal / 155 kW peak = **540 / 620 kW** |
| Cooling (electrical) | 119 kW at nominal — chiller 81, air 18, pumps 20 |
| Facility total | **688 kW** → **PUE 1.27** |
| Per side | 1000 kW transformer · 750 kW UPS · 60 kWh battery · 800 kW PDU |
| Generator | 1 × 800 kW, 30 s start, 4000 L tank |
| Cooling plant | 600 kW liquid + 100 kW air, 5000 L loop |
| Users | 152 requests/s at the daily peak, 420 tokens each, 8 s latency budget |

Three things separate it from a spreadsheet:

1. **Load comes from users.** Requests arrive, become tokens of work, become a power
   ask per rack. Throughput is read back from the power the racks actually drew.
   Unserved work queues; past 8 s it is dropped. So a chiller trip costs **25.9 % of
   user requests**, not just "the loop reached 87 °C".
2. **Cooling is inside the electrical model.** Chillers and pumps draw from the same
   PDUs as the racks. PUE is computed, not assumed.
3. **Every component is a separate object with its own state.** Each can fail on its
   own terms — overload, overheat, failed start, empty tank. Nothing is a constant.

The site is sized deliberately at the edge, so the margins are worth stating plainly:

| Margin | Value | Meaning |
|---|---|---|
| One side at nominal load | 88 % of UPS nameplate | survives |
| One side at IT peak | 100.5 % | overload → bypass |
| Chiller vs liquid heat at IT peak | 600 vs 558 kW | 42 kW spare |
| Traffic vs serving capacity at the daily peak | 152 vs 190 req/s | 80 % used |
| Battery autonomy | ~10 min on 2N, ~5 min on one side | enough to start a genset |
| Generator vs facility load | 800 vs 688 kW | 112 kW spare |

---

## 2. How it works

### 2.1 Two kinds of quantity

| | **State** | **Flows** |
|---|---|---|
| Examples | charge, fuel, temperatures, timers, queue | kW, tokens/s |
| Has memory? | yes | no |
| Handled by | each component advances its own, every tick | solved inside the tick |

**State is simulated.** Every component owns its own and moves it forward itself. No
component writes to state it does not own.

| Component | Owns |
|---|---|
| Battery | state of charge |
| Generator | crank timer, run hours, fuel, start attempts |
| Transformer | winding temperature, overload timer |
| ATS | transfer timer, mains-stable timer |
| UPS | mode, overload timer, seconds on battery |
| Rack | temperature, throttle/shutdown state |
| Coolant loop | water temperature |
| Room / CRAH | air temperature |
| Chiller | controller setpoint |
| Queue | backlog of unserved work |

**Flows are solved.** Electricity has no travel time. If the racks draw 500 kW, the
transformer carries 500 kW in the same instant. So flows are not integrated forward —
they are made to balance within each tick:

```
sources  ==  loads  +  losses
```

Heat is the exception: it does propagate slowly, so all thermal coupling is
integrated forward one tick at a time, like state.

### 2.2 One tick, step by step

A **normal** tick — nothing broken, nothing failing. `normal` scenario, t = 14.5 h
(the daily traffic peak), dt = 60 s. Every number is from the run and pinned by
`test_worked_example.py`.

**Step 1 — the equipment reports what it can supply.** Asked before any demand
exists. Each component takes the number from above and applies its own limit.
Tightest link wins.

```
utility A    1500.0 kW
transformer   990.5 kW   cap 1000, less full-load loss
ATS           990.5 kW   a switch, passes it through
UPS A         937.5 kW   750 nameplate × 1.25 overload rating
PDU A         796.0 kW   cap 800, less 0.5 % loss     <- tightest link

side A: 796 kW      side B: 796 kW
```

Nothing changes state here. Asking first is what makes failover free: a side that
just died reports zero, so it is given zero work, and the other side takes
everything — same tick, no failover code.

**Step 2 — the load states what it wants.**

```
145.7 requests/s arriving
× 420 tokens          =    61,190 tokens/s
÷ 2 user-facing racks =    30,595 tokens/s each
→ as power            =    123.3 kW  (rack-2, rack-3)

rack-1, rack-4 run training on their own schedule:  136.6 + 139.8 kW
                                        IT total  =  523.0 kW

cooling asks too — its pumps and compressors are electrical load.
It sizes itself from the loop temperature at the end of the last
tick (the one lagged number in the model, §2.4):
  chiller 76.2 + pumps 20.0 + air 18.0         =  114.2 kW

                                    total wanted =  637.2 kW
split in proportion to capacity (796 : 796)    =  318.6 kW per side
```

Still nothing committed.

**Step 3 — power is handed out.** 637.2 kW wanted against 1592 kW available, so
everyone gets what they asked. Only this step changes state.

```
side A:  grid 334.1 kW
         − 2.4  transformer loss
         −11.5  UPS conversion loss
         − 1.6  PDU loss
        = 318.6 kW delivered        battery untouched, 100 %
side B:  identical.                 31.0 kW lost in total
```

**Step 4 — the load consumes it, physics runs.**

```
cooling paid first (§2.6):   114.2 kW
IT gets the rest:            523.0 kW      nothing curtailed

every watt drawn becomes heat; each rack's temperature is then solved
  rack-2: draws 123.3 kW, sheds 119.6 kW, sits at 41.7 °C
          the extra 3.7 kW warmed the rack's own metal
  rack-1: sheds 1.6 kW more than it drew — cooling after a checkpoint dip

heat into cooling:  463.8 kW liquid (90 %) + 51.5 kW air (10 %)
work done:          145.7 requests/s — all of it, queue empty
loop:               chiller rejects 457.0 kW (76.2 kW × COP 6), loop sits
                    at 17.9 °C, just under its 18 °C setpoint
```

**Books.**

```
in    668.2 kW
out   523.0 IT + 114.2 cooling + 31.0 losses  =  668.2 kW
                                   difference =  0.000 kW
PUE = 668.2 / 523.0 = 1.278
```

In code these four steps are `probe`, `request`, `deliver` and the consume block, in
[`facility.py`](../app/simulations/sim1/facility.py).

### 2.3 Design choices

| Choice | Alternative | Why not the alternative |
|---|---|---|
| Solve flows inside the tick | Lag each component by one tick | Sources and sinks disagree by up to **617 kW** the moment a failure lands (measured). Kills the conservation check. Also models a delay electricity does not have. |
| Solve flows inside the tick | Iterate to a fixed point | Adds "failed to converge" as a failure mode indistinguishable from a modelled one. |
| 2N, two sides | N, one chain | Failover, side overload and single-point-of-failure become real instead of described. |
| Cooling draws from the PDUs | Cooling as a separate subsystem | PUE becomes computed, and a chiller trip perturbs the electrical picture. |
| Adaptive dt | Fixed 1 s | A week at 1 s is 604,800 ticks. Affordable (~1 min) but 60× the data for no gain outside events. |
| Adaptive dt | Fixed 60 s | A 30 s generator start cannot be represented. |
| Analytic thermal solve | Explicit Euler | Euler needs dt < 350 s here and oscillates near the limit. |
| Plain objects + SimPy clock | SimPy processes | Every unit test would need an `Environment`. |

Lag is not wrong in general — sim-0 uses it, correctly, because sim-0 has one lagged
number and no fast dynamics.

### 2.4 The one lagged number

The chiller sizes its demand from the coolant loop temperature at the end of the
previous tick. Two reasons:

- A real chiller's capacity control has tens of seconds of dead time, so the lag is
  **more** accurate than an instant response.
- It cuts the only circular dependency left: cooling load → facility load →
  available power → IT load → heat → cooling load.

It also makes cooling degrade gradually rather than clipping:

```
chiller capacity exceeded
  → loop temperature rises
  → each rack's ΔT to its coolant shrinks
  → the heat a rack can shed falls
  → rack temperature rises → throttle
  → serving capacity falls → requests queue → requests dropped
```

No `min()` anywhere in that chain.

### 2.5 Tick length

Fixed 60 s cannot show a 30 s generator start. Fixed 1 s over a week is 604,800
ticks. So dt varies: coarse by default, fine inside windows around scheduled events,
and ticks land exactly on event times. A week is ~10,000 ticks; the interesting ten
minutes get 1-second resolution.

Consequence: **every aggregate is `Σ p·dt`**, never `mean × count`. Anything counting
rows is wrong, including percentiles.

### 2.6 Priorities

1. **Cooling before IT.** In a brownout the pumps keep turning and IT falls to zero.
   Losing the chillers cooks the hall; losing GPU-seconds does not.
2. **Battery recharge last.** Otherwise two 100 kW chargers trip a freshly started
   generator.

### 2.7 Conservation

Every tick, every row:

```
grid_a + grid_b + generator + battery_out  ==  IT + cooling + losses + battery_charge
```

Worst residual across all nine scenarios: **2.3e-13 kW**. The visualization prints
`in − out` in the corner. `test_facility.py` asserts it per row, including heat stored
in the loop, the room and the racks — the term that would otherwise let the lagged
number hide a real error.

---

## 3. Where the load comes from

```
daily curve + jitter + surges
        │  152 requests/s at peak
        ▼
   × 420 tokens per request
        │
        ▼
   scheduler: work → power ask per rack
        │  capped if the rack is throttling or down
        ▼
   racks draw, or are curtailed
        │
        ▼
   power → throughput served
        │
        ▼
   served │ queued │ dropped past 8 s
```

**Power ↔ throughput** is linear above an idle floor and invertible. A rack draws
`idle_kw` at zero load and `peak_kw` at full throughput. Consequence: peak power and
peak throughput are the same point, so **a rack at nominal draw is already at 85 % of
what it can serve**.

**Two rack types:**

| | Interactive | Batch |
|---|---|---|
| Driven by | user traffic | its own schedule |
| Lost capacity means | requests dropped | work deferred (kWh backlog) |
| Default build | 2 racks | 2 racks |

**Queue:** backlog in tokens, latency by Little's law, abandonment past
`slo_latency_s`. When nothing is being served, latency is undefined and reported as
`null` — `JSON.parse` rejects bare `Infinity`, which would load the page blank.

---
## 4. Components

**The power path.** Each stage either changes the form of the energy, or keeps it
flowing when the stage above fails.

```
utility 20 kV ─ transformer ─ ATS ─ UPS(+battery) ─ PDU ─ rack PSU ─ GPU
     source      20kV→400V   choose   bridge &      split    to DC     work
                             source   condition
                                └── generator (long outages)
```

**The heat path.** Nothing destroys energy. Every watt into a rack comes out as heat,
which is why cooling capacity — not floor space — limits a modern hall.

```
GPU ─ cold plate ─ CDU ─ facility loop ─ chiller / dry cooler ─ outside air
                    │
   the ~10 % not on a cold plate ─ room air ─ CRAH ─ outside air
```

Water carries ~3,500× more heat per unit volume than air. That is why liquid cooling
exists at these densities.

Below, per component: what it is **for**, its numbers, what to **buy**, and what it
does in **normal** operation. How each one fails is collected separately in
[§5](#5-what-goes-wrong). Vendor names are product families — check current
catalogues for ratings. What the model omits is in [§8](#8-not-modelled).

---

### 4.1 Utility feed — `electrical/grid.py`

**Does.** Brings grid power in at medium voltage. Everything downstream either
conditions it or substitutes for it. Two feeds because one is a single point of
failure — and they should come from *different substations*.

**Numbers.** 1500 kW per feed · 20 kV · brownout state at 65 % capacity, 85 % voltage.

**Buy.** MV service from the utility plus MV switchgear: ring main units, protection
relays, metering. Usually the longest lead time on the project.
> ABB UniGear · Schneider PIX / SM6 / RM6 · Siemens 8DJH / NXPLUS C · Eaton Xiria ·
> Ormazabal CGM. Relays: SEL, Schneider Easergy, ABB Relion, Siemens SIPROTEC.

**Normally.** Both feeds sit online at full voltage, each carrying half the site.
Its `drawn_kw` is what the tariff bills.

---

### 4.2 Transformer — `electrical/transformer.py`

**Does.** Steps 20 kV down to 400 V. Without it you cannot connect racks to the grid.
Passive and reliable — but it is the first place ~1 % of your energy becomes heat, and
overloading it cooks the winding insulation. That failure is slow and cumulative, not
a clean trip.

**Numbers.** 1000 kW · no-load loss 1.5 kW · full-load copper loss 8 kW (99.2 % at
nominal) · winding overheats at 140 °C · trips instantly at 150 % · rides moderate
overload for 30 min.

**Buy.** Cast-resin dry-type, 1000–1250 kVA, 20 kV → 400 V, indoors next to the LV
board. Dry-type is normal inside a DC: no oil to contain, better fire behaviour.
Oil-filled is cheaper if it can sit outdoors.
> Siemens **GEAFOL** · Schneider **Trihal** · ABB **RESIBLOC** · Hitachi Energy ·
> Eaton · Legrand/Zucchini · TMC Transformers · Trafo Elettro.

**Normally.** Carries ~334 kW, loses 2.4 kW of it, winding sits a few degrees above
ambient. Loss grows with the *square* of load, so it climbs faster than the load
does.

---

### 4.3 ATS — `electrical/ats.py`

**Does.** Picks utility or generator, automatically. Break-before-make: it
disconnects one source before connecting the other, because back-feeding the utility
from your generator would kill a lineman. **The resulting dead interval is why the
UPS exists.**

**Numbers.** transfer 1.0 s · returns to utility only after 60 s of stable mains.

**Buy.** Open-transition ATS sized for the full side load, or interlocked breakers
with a controller. Closed-transition avoids the break, at more money and more
protection complexity.
> ASCO (Schneider) **7000 series** · Socomec **ATyS** · Eaton ATC / **Power Xpert** ·
> Cummins **OTPC** · Generac **RTS** · Kohler. Breaker pair: ABB Emax 2, Schneider
> Masterpact MTZ, Siemens 3WA + Deep Sea or ComAp controller.

**Normally.** Sits on `on_primary` and does nothing at all. It has no effect on a
healthy run beyond passing power through.

---

### 4.4 UPS — `electrical/ups.py`

**Does.** Two jobs.
1. **Bridge** — carry the load from battery between losing the utility and the
   generator taking over.
2. **Condition** — rebuild the AC waveform from scratch, so utility sags, spikes and
   harmonics never reach the IT gear.

Cost of that insurance: **3.5 % of every kWh, forever**. Largest avoidable line in
your PUE.

**Numbers.** 750 kW · rectifier 98.5 % × inverter 98 % = **96.5 %** · bypass 99.9 % ·
delivers **125 %** short-term · trips at 150 % · 60 s hold timer at overload.

**Buy.** Modular three-phase VFI (double-conversion), 750–800 kW, `n+1` power modules
so one can be swapped live. 96–97 % is state of the art.
> Schneider **Galaxy VX / VL** · Vertiv **Liebert Trinergy Cube / EXL S1** · Eaton
> **93PM / 9395** · ABB **DPA 250 S4 / MegaFlex** · Huawei **UPS5000** · Delta
> **DPH / Ultron** · Mitsubishi **9900** · Riello Multi Power · Legrand Trimod.

**Normally.** `online`, around 40 % loaded, converting at 96.5 % — so ~11.5 kW of
the 334 kW passing through it becomes heat. Battery at 100 %, untouched.

---

### 4.5 Battery — `electrical/battery.py`

**Does.** Buys time — enough seconds to start a generator and transfer to it. Not to
run the site. Five minutes is the modern norm because gensets start in 10–30 s.
Beyond that it is wasted capital unless you have no generator.

**Numbers.** 60 kWh per side · usable to 5 % SoC · discharge ≤ 800 kW, charge ≤ 100 kW
· 96 % each way.

Each side holds ~5 min at full *site* load. On 2N each side carries half → **~10 min**
total. Lose a side → back to 5.

**Buy.** Lithium-ion cabinets: a third of the VRLA footprint, 10–15 year life,
integrated BMS. LFP for safety and cycle life, NMC for density.
> Matched to the UPS: Vertiv **HPL** · Schneider **Galaxy Li-ion** · Eaton
> **xStorage** · Huawei **SmartLi** · Delta. Cells: Samsung SDI, LG, CATL, EVE,
> Narada. VRLA: EnerSys **PowerSafe / DataSafe**, C&D, Yuasa, Leoch.

**Normally.** Full, idle, and reporting remaining autonomy **every tick** anyway —
"if the grid dropped right now, 9.4 minutes". That continuous figure is more useful
than one that only appears during an outage.

---

### 4.6 Generator + fuel — `electrical/generator.py`

**Does.** Answers a long outage. Battery buys minutes; this buys hours, limited by
the tank. What matters is not its rating but **whether it starts** — a genset that
fails to start is worth nothing. There is deliberately only **one** here: the single
point of failure in an otherwise 2N site.

**Numbers.** 800 kW · 30 s start · 98 % success, 3 attempts 10 s apart · 10 s output
ramp · 5 min cooldown · fuel `8.0 + 0.24 × kW` L/h ≈ 200 L/h at full load
(**0.25 L/kWh**) · 4000 L ≈ 20 h.

**Buy.** Standby-rated diesel, 800–1000 kVA, with radiator, exhaust and silencer, day
tank plus bulk storage, and a controller that detects mains failure itself. NFPA 110
Level 1 wants rated voltage within **10 s**; 30 s here is conservative. A real 2N site
buys two.
> Caterpillar **C32 / 3512** · Cummins **QSK / C-series** · MTU Onsite Energy
> **Series 4000** · Kohler-SDMO · FG Wilson · Generac Industrial · Himoinsa ·
> Atlas Copco. Controllers: Deep Sea, ComAp InteliGen, Woodward easYgen.

**Normally.** `stopped`. Full tank, no fuel burn, no output. It is only asked to run
when both sides lose their mains.

---

### 4.7 PDU / busway — `electrical/pdu.py`

**Does.** Splits one large feed into many rack circuits, each with its own breaker,
so a fault in one rack does not take the hall. Busway lets you move rack power
without re-pulling cable — rack density changes faster than buildings do.

**Numbers.** 800 kW · 99.5 % · trips at 160 % · 120 s hold. A separate
`cord_limit_kw` caps what one side may deliver.

**Buy.** Overhead busway with tap-off boxes, fed from a switchboard or RPP. Inside the
rack it is a busbar with PSU shelves, not a traditional rack PDU. Dual-corded end to
end: A and B into separate shelves.
> Busway: **Starline** (Legrand) · Anord Mardix · Eaton Pow-R-Way · Schneider
> Canalis · Siemens. Floor PDUs: Vertiv **Liebert PPC/PPA** · Schneider
> **EcoStruxure Row PDU** · Eaton. Metering: Raritan, ServerTech, APC.

**Normally.** Passes ~319 kW at 99.5 %, well inside its 800 kW rating. A well-sized
distribution layer is invisible.

---

### 4.8 Rack — `rack.py`

**Does.** The thing you built the data centre for; everything else is overhead. In a
GB300 NVL72 the **rack is the unit of compute** — 72 GPUs on one NVLink domain act as
one large accelerator, which is why you buy a rack, not servers. It turns electricity
into computation and, at effectively 100 %, into heat.

It also protects itself: too hot → slow down (throttle), still too hot → stop
(shutdown). Those two thresholds are where a cooling problem becomes a compute
problem.

**Numbers.** 135 kW nominal / 155 kW peak · idle 15 % of nominal (20.25 kW) · 40,000
tokens/s at peak · throttle 85 °C to 60 % draw, shutdown 95 °C, recover 70 °C with
5 °C hysteresis · sits ~30 °C above coolant at peak, time constant ~174 s · 90 % of
heat to liquid, 10 % to air.

**Buy.** **NVIDIA GB300 NVL72** — 72 Blackwell Ultra GPUs + 36 Grace CPUs, liquid
cooled, sold as an integrated rack. Cold plates on GPUs, CPUs, NVSwitch; the rest
(optics, storage, DPUs, PSU loss) goes to air. Published power 120–140 kW per rack
depending on configuration.
> Integrators: **Dell** (PowerEdge XE / IR7000) · **HPE** · **Supermicro** ·
> **Lenovo** · Foxconn/Ingrasys · Wiwynn · Quanta/QCT · Gigabyte · ASUS · ZT Systems.
> Comparable density: AMD Instinct MI355X, Google TPU (cloud only), Cerebras, Groq.

**Normally.** Draws exactly what it is granted — 123 kW for a user-facing rack at the
daily peak, ~137 kW for one running training. Sits around 42–45 °C, well under the
85 °C throttle point, shedding almost all of its heat to the coolant.

---

### 4.9 Workload and users — `workload.py`, `demand.py`

**Does.** Supplies the demand — the only thing that makes any of the sizing above
meaningful. Requests arrive on a daily cycle with correlated jitter; each carries
tokens of work; a scheduler turns that into a power ask per rack.

Jitter is correlated, not white: real load wanders over minutes, and uncorrelated
noise averages away exactly the excursions that stress a power chain.

**Numbers.** 76 req/s per interactive rack at the 14:00 peak (152 for the default
two) · 35 % of that at 02:00 · ±8 % jitter, 300 s time constant · 420 tokens per
request · 8 s latency budget · batch catch-up over an hour.

**Buy.** Software. **Measure tokens/s per rack and tokens/request on your own model
and traffic** — do not take these.
> Serving: **vLLM** · **TensorRT-LLM** + **Dynamo** / Triton · **SGLang** · TGI ·
> Ray Serve · KServe. Gateways: Envoy / Istio, LiteLLM, Kong. Measurement: NVIDIA
> **GenAI-Perf**, vLLM's benchmark harness, k6, Locust. Batch: Slurm,
> Kubernetes + Kueue, Run:ai, Volcano.

**Normally.** Traffic rises and falls across the day, peaking at 14:00 and troughing
at 02:00. Everything offered is served in the tick it arrives; the queue stays
empty.

---

### 4.10 CDU and pumps — `cooling/cdu.py`

**Does.** Sits between two water loops: the clean loop into the racks' cold plates,
and the facility loop going outdoors. It exists for **isolation** — a facility-loop
leak must never reach three million dollars of GPUs — plus pressure and temperature
control at the rack. It also holds the pumps that move the heat.

Small in kW, decisive in effect: heat transfer scales with flow, so losing pump power
does not degrade cooling proportionally — it stops it, while the chiller carries on
compressing nothing.

**Numbers.** 20 kW of pump power at design flow.

**Buy.** Liquid-to-liquid CDU with redundant pumps, filtration and a plate heat
exchanger. NVL72 reference designs use ~1.3 MW units; four racks at 550 kW is one
mid-size unit, or a pair for N+1.
> **CoolIT** CHx / AHx · **Vertiv** XDU1350 · **Motivair** MCDU · **Boyd** ·
> **nVent** · Stulz CyberCool CMU · LiquidStack · Delta · Envicool · Chilldyne
> (negative-pressure, leak-safe). Couplings: CPC, Staubli, Parker.

**Normally.** Pumps draw their full 20 kW, flow is at 100 %, and the rack-to-coolant
heat transfer runs at its design conductance.

---

### 4.11 Coolant loop — `cooling/loop.py`

**Does.** Moves heat from the cold plate to where it can be rejected. Its **volume is
also a thermal flywheel** — the stored water is what buys you minutes when cooling
fails. At 540 kW in and nothing out this loop climbs **1.55 °C/min**, so a chiller trip
takes ~20 minutes to collapse the rack ΔT. Halve the volume and the story becomes a
cliff.

**Numbers.** 5000 L (≈20,900 kW·s/°C) · supply 20 °C, floor 12 °C · loop heat capacity
rate 60 kW/°C.

**Buy.** Pipework (stainless or HDPE), a buffer tank sized for exactly that
ride-through, expansion vessel, side-stream filtration, dosing pot, and treated water
or PG25 if there is freeze risk. Leak detection under the floor and at every manifold.
> Heat exchangers: **Alfa Laval** · **Kelvion** · SWEP · API. Pumps: **Grundfos** ·
> KSB · Wilo · Armstrong · Xylem. Valves: Belimo, Siemens, Danfoss. Water treatment:
> Nalco (Ecolab), Kurita. Leak detection: TTK, RLE, nVent Raychem.

**Normally.** Sits within a degree of its 18 °C setpoint, taking ~464 kW in and
handing the same out. Supply-to-return spread is about 8 °C.

---

### 4.12 Chiller / heat rejection — `cooling/chiller.py`

**Does.** **The exit door for energy.** Everything else in the cooling chain moves
heat around; this is the only thing that gets it out of the building.

It is also where cooling costs real electricity — most of the gap between PUE 1.0 and
1.27. Its draw comes off the same PDUs as the racks, so a chiller trip moves the
electrical picture both ways at once: ~90 kW of draw freed, heat removal collapsed.

**Numbers.** 600 kW removal · COP 6.0 · 18 °C setpoint · proportional control with a
120 s lag.

**Buy.** An 18 °C supply is warm-water territory, so in practice this is *dry coolers
with adiabatic assist* most of the year plus a **trim chiller** for hot hours — which
is where COP 6+ comes from. A conventional 7 °C plant sits at COP 3–5. 600 kW ≈ 170
tons refrigeration.
> Dry coolers / free cooling: **Vertiv Liebert AFC** · **Güntner** · **Kelvion** ·
> Thermokey · Alfa Laval. Chillers: **Carrier AquaForce** · **Trane Series R /
> Sintesis** · **York YVAA / YZ** · **Daikin Applied** · Swegon · Climaveneta · LG ·
> Midea. Cooling towers: Baltimore Aircoil, SPX Marley, Evapco.

**Normally.** Draws ~76 kW to reject ~457 kW, modulating smoothly with the load. The
120 s control lag is not cosmetic: without it the cooling↔power feedback oscillates
every two ticks whenever the site sits exactly at capacity.

---

### 4.13 Air side — `cooling/crah.py`

**Does.** Handles what the cold plates do not — optics, DPUs, storage, PSU losses,
about 10 % of rack power — and keeps room air in spec for those parts.

Room air has very little thermal mass, so when the CRAHs fall behind the temperature
moves in *minutes* and drags the racks' blended sink temperature up with it.

**Numbers.** 100 kW · COP 3.0, worse than the chiller's — which is exactly why liquid
cooling is worth the plumbing · 24 °C setpoint · 1000 m³ room · small leakage to
outdoors.

**Buy.** Chilled-water CRAHs or in-row units sized for the residual air load only. In
a liquid-cooled hall this is a fraction of what an air-cooled hall needs — the whole
economic argument for DLC.
> **Vertiv Liebert CRV / PDX / DSE** · **Stulz CyberAir** · **Schneider Uniflair** ·
> Rittal LCP · Munters · Airedale · Systemair.

**Normally.** Draws ~18 kW to move the ~52 kW of air-side heat, holding the room near
its 24 °C setpoint. Room temperature is blended into the racks' cooling sink by
capture rate, so it matters even when the water is fine.

---

### 4.14 Monitoring and cost — `telemetry.py`, `kpis.py`, `economics.py`

**Does.** You cannot operate what you cannot see, or justify what you cannot cost.
Each component publishes a flat telemetry dict; the facility row is assembled by
prefixing them. Discrete transitions are *derived* from the frames, not emitted by
components, so the event log cannot disagree with the time series.

Four cost lines, four different questions: grid energy at a time-of-use tariff,
diesel, curtailed compute as opportunity cost, downtime as an SLA step.

**Numbers.** $0.09/kWh off-peak, $0.19/kWh 08:00–20:00 · $1.10/L diesel · $3.00/kWh
unserved compute · $500/min while the hall draws nothing.
> The quoted $9,000/min downtime figure is a hyperscale number, absurd for four racks.

**Buy.** DCIM for assets and capacity, BMS for the mechanical plant, power metering
wherever you want a defensible number.
> DCIM: **Vertiv Trellis** · **Schneider EcoStruxure IT** · Nlyte · Sunbird ·
> Hyperview · NetBox. BMS: Siemens Desigo, Schneider EcoStruxure Building, JCI
> Metasys, Ignition. Metering: **Schneider PowerLogic** · Socomec **Diris** ·
> Janitza · Accuenergy. Environmental: Vutlan, AKCP, Raritan.

**Normally.** Produces the three frames every chart and KPI is built from, plus the
event log the timeline reads.

---
## 5. What goes wrong

Every component can fail on its own terms. One table, one row per failure mode.

| Component | Trigger | What happens | Seen in |
|---|---|---|---|
| Utility feed | goes offline | that side's capacity drops to whatever its battery can give; if **both** sides lose mains, the generator is asked to start | `grid_outage_*` |
| Utility feed | brownout | capacity falls to 65 %; the UPS tops up the shortfall from battery | — |
| Transformer | sustained overload | rides it for 30 min, then trips | — |
| Transformer | winding reaches 140 °C | overheats and stops; latched | — |
| Transformer | external trip | that side's capacity → 0 immediately | `side_a_lost` |
| ATS | mid-transfer | output is **zero** for 1 s — the gap the UPS exists to cover | `grid_outage_gen_ok` |
| UPS | overload past 60 s | transfers to **bypass**: load survives, battery protection silently lost | `side_a_lost_at_peak` |
| UPS | no input, battery flat | goes `offline`; that side delivers nothing | `grid_outage_gen_fail` |
| Battery | reaches 5 % cutoff | stops discharging; protects the cells, ends the ride-through | `grid_outage_gen_fail` |
| Generator | fails to start | retries twice, then latches `failed` — no further attempts | `grid_outage_gen_fail` |
| Generator | tank runs dry | latches `failed` mid-run | — |
| PDU | cord limit binds | IT is curtailed even though the surviving UPS has headroom | `undersized_cords` |
| Rack | 85 °C | throttles to 60 % draw; serving capacity falls with it | `cooling_failure` |
| Rack | 95 °C | emergency shutdown, draw → 0; recovers below 70 °C | `cooling_failure` |
| CDU pumps | lose power | flow collapses, so cooling stops almost entirely while the chiller keeps compressing | — |
| Chiller | trips | heat removal → 0, and its own ~76 kW of draw disappears from the power stack | `cooling_failure` |
| Queue | offered work > serving capacity | latency climbs to the 8 s budget, then requests are dropped | `user_surge` |

Rows marked "—" are modelled and reachable, but no shipped scenario scripts them.

### Failures that cross components

Most interesting failures are not one component breaking. They are a chain.

**Cooling loss becomes lost revenue.** No hard limit anywhere in this sequence — each
arrow is a physical consequence of the one before it:

```
chiller trips
  → loop temperature rises (1.55 °C/min at 540 kW with nothing leaving)
  → each rack's ΔT to its coolant shrinks
  → the heat a rack can shed falls
  → rack temperature rises
  → 85 °C: throttle to 60 % → serving capacity falls
  → offered work now exceeds capacity → queue builds
  → 8 s budget exceeded → requests dropped
  → 95 °C: shutdown → capacity zero → 100 % dropped
```

**Losing a side quietly spends its battery.** A transformer trip does not summon the
generator, because the other side still has full capacity — a defensible policy. But
UPS A now has no input, so it carries its share from battery until the cells hit
cutoff (~10 minutes), and only then does side B pick up everything.

**An overload now kills you later.** A UPS pushed past 100 % transfers to bypass
rather than dropping the load. The site looks fine. It is now running with no battery
between it and the grid, so the *next* utility event is the one that takes it down.

### Two readings that look wrong and are not

**A saturated queue reads as empty.** Requests past the latency budget are abandoned,
not held. Queue depth hits zero at the exact moment the drop rate is worst, and
latency pins at the budget. Read the drop rate there, not the queue.

**A brownout shows IT at zero while the pumps still turn.** Cooling is paid before IT
by design (§2.6). Losing the chillers cooks the hall; losing GPU-seconds does not.

---
## 6. Reference build

| | Value | Source |
|---|---|---|
| Racks | 4 × 135 kW nominal / 155 kW peak | `SiteConfig.rack_*` |
| IT load | **540 kW** nominal, **620 kW** peak | 4 × above |
| Liquid heat at nominal | 486 kW → 81 kW electrical | 90 % capture, COP 6 |
| Air heat at nominal | 54 kW → 18 kW electrical | 10 % residual, COP 3 |
| Pumps | 20 kW | fixed |
| **Cooling electrical** | **119 kW** nominal, 134 kW peak | computed |
| UPS output | 659 kW nominal, 754 kW peak | IT + cooling |
| UPS loss | ~23 kW | 96.5 % double conversion |
| Transformer loss | ~5 kW | quadratic copper term |
| **Facility total** | **688 kW** → **PUE 1.27** | computed |
| Per side | 1000 kW tx · 750 kW UPS · 60 kWh battery · 800 kW PDU | |
| Generator | 1 × 800 kW, 30 s, 4000 L | 112 kW spare |
| Cooling plant | 600 kW liquid + 100 kW air, 5000 L loop | |
| Traffic | 152 req/s at peak, 420 tokens each | 80 % of capacity |
| Serving capacity | 190 req/s across two racks | 40,000 tokens/s per rack |

`design_margins()` computes this from the config, so it cannot drift from the code.

---

## 7. Scenarios

| Scenario | Shows | Users hit | Incident at | Run |
|---|---|---|---|---|
| `normal` | steady state; 50/50 split, PUE 1.27, reserves untouched | none | — | 7 d @ 60 s |
| `grid_outage_gen_ok` | both feeds drop → battery → 30 s crank → ATS transfers → diesel → recharge | **none** | 10 min | 1 h @ 10 s |
| `grid_outage_gen_fail` | three failed starts → latched failure → batteries to cutoff → hall dark | 56.2 % dropped | 5 min | 35 min @ 5 s |
| `user_surge` | four user-facing racks, traffic ×1.5. Power and cooling hold; **throughput** binds | 9.3 % dropped | 15 min | 2 h @ 15 s |
| `load_spike` | power-and-cooling question with users out of the way: four batch racks at peak | n/a | 15 min | 2 h @ 15 s |
| `cooling_failure` | chiller trips → loop +1.5 °C/min → ΔT collapses → throttle → shutdown → restored | 25.9 % dropped | 15 min | 3 h @ 30 s |
| `side_a_lost` | transformer A trips; UPS A drains its battery, then B carries the site at 92 % | none | 15 min | 90 min @ 15 s |
| `side_a_lost_at_peak` | purely electrical: all batch at peak. UPS B >100 % → **bypass** | n/a | 10 min | 1 h @ 10 s |
| `undersized_cords` | 2N on paper (cords at 60 % of peak): a side loss curtails IT | 20.9 % dropped | 15 min | 90 min @ 15 s |

### What each one asks

Every scenario holds the reference build of §6 fixed and changes exactly one thing —
what fails, when it fails, and how much traffic is on the site at that moment. That is
what makes the KPIs comparable across the set: a number moved because of the failure,
not because the build was different.

**`normal` — what does a healthy week cost and look like?** No incident. It exists to
be the control: traffic follows the day, two racks train through the night, both sides
sit under half their UPS nameplate, PUE averages 1.29 and nothing is dropped. It also
shows the one thing steady state still gets wrong — offered traffic touches **101.8 %**
of serving capacity at the daily peak, and the queue absorbs it inside the 8 s budget.
Every other scenario is read against this one.

**`grid_outage_gen_ok` — does the backup chain actually work?** Both feeds drop at
10 min. The UPSes carry the site for **40 s** on battery, the genset cranks for 30 s,
each ATS transfers, 42 L of diesel burn, the batteries recharge when the utility
returns at 25 min. **No user request is dropped.** The point is the sequence and its
timing: this is what the 2N chain is *for*, and the run is the evidence that battery
autonomy covers the crank.

**`grid_outage_gen_fail` — and when it doesn't?** The same outage at 5 min with
`generator_start_success_p = 0`. Three start attempts fail, the failure latches, the
batteries drain for **9.6 minutes** and the site goes dark. **56.2 % of requests
dropped**, uptime 41.6 %. Paired with the run above, it prices the generator: the
difference between the two KPI sets *is* the value of one diesel that starts.

**`side_a_lost` — is 2N really 2N?** Transformer A trips at 15 min on a busy site.
UPS A has no input, so it quietly discharges its own battery to cutoff while side B
picks up everything in the same tick — there is no failover code, only capacity that
went to zero. B peaks at **92 % of nameplate**: survivable, with nothing to spare.
Users see nothing.

**`side_a_lost_at_peak` — is it 2N at *peak*?** The same trip with all four racks
running batch at peak and no user traffic to blur the electrical picture. UPS B crosses
**101.3 %**, its overload hold timer expires and it transfers to **bypass**: the load
survives, but it is now sitting on raw utility with no battery behind it. The
interesting failure is not a blackout — it is a protection that silently went away.

**`undersized_cords` — what if the 2N is only on paper?** The chain is intact, but the
cords on a side carry at most **474 kW**, 60 % of site peak. Transformer A trips at
15 min and the cords, not the UPS, become the binding limit: side B has the capacity,
the racks cannot be handed it, and IT is curtailed rather than failed over.
Uptime reads **100 %** while **20.9 % of requests are dropped** and SLO compliance falls
to 28 %. It is the scenario that argues KPIs measured in kW and in uptime can both look
fine while the users lose.

**`user_surge` — what binds first when traffic grows?** All four racks serve users and
traffic goes to 1.5× between 15 and 75 min. Power and cooling never flinch — each UPS
stays near 50 %, PUE holds at 1.28. The racks hit their **throughput** ceiling instead:
offered load reaches **145.7 %** of serving capacity, the queue fills to the 8 s budget
and **9.3 %** of requests are abandoned. The constraint is compute, not kW.

**`load_spike` — and when the users are taken out of the picture?** Four batch racks
pinned at peak for an hour, no traffic at all, so the only question left is electrical
and thermal. Power is comfortable (each UPS ~50 %); cooling is the tighter margin, with
roughly **44 kW of chiller headroom** left of 600. The pair `user_surge` / `load_spike`
is deliberate: the same "more load" question has a different answer depending on whether
the load is users or work.

**`cooling_failure` — how does a thermal failure become a user-visible one?** The
chiller trips at 15 min and is restored at 105, because nothing else in the model can
pull heat out of the loop. The loop climbs about 1.5 °C/min, rack ΔT collapses, the
racks throttle (**10 times**) and then shut down (**4 times**), and the chiller's own
~80 kW of draw disappears from the power stack at the same moment — a cooling failure
moves the electrical picture in both directions at once. Uptime 77.6 %, IT served
71.2 %, **25.9 % of requests dropped**. Watch the queue rather than the temperatures:
throttling costs serving capacity long before any rack goes dark.

### The clock

Two conflicting needs: a failure only teaches something if the site was **busy**, but
nobody scrubs through twelve idle hours to reach 14:00.

`SiteConfig.start_hour` is the wall-clock time that `t = 0` means. Incident scenarios
start at **13:00**, so a failure 15 minutes in lands at 13:15 — on the daily peak.
Runtimes drop from 16–20 hours to 35 minutes–3 hours.

The offset applies to traffic, rack profiles **and** the tariff together. Applying it
to only some would bill a 13:00 start at the night rate.

Side effect: because the whole run sits at representative load instead of averaging a
quiet night, drop percentages are higher than the longer runs reported.

---

## 8. Not modelled

**Electrical.** Breakers and protection coordination, earthing, surge protection,
arc flash, harmonics, power factor, N+1 inside the UPS, a second generator,
paralleling and load sharing, load banks.

**Mechanical.** N+1 on chillers, CDUs and CRAHs; pressure and flow hydraulics;
ambient-dependent COP; humidity; aisle airflow; water consumption; leak detection.

**IT.** Individual GPUs, PSUs and fans; the NVLink fabric; storage and networking as
loads; MTBF and repair time; firmware.

**Other.** Fire detection and suppression, physical security, staffing and MTTR,
commissioning, capex, carbon, grid demand-response, multi-site failover.

Each simplification sits behind a replaceable object, so any of them can be upgraded
without touching the tick.

---

## 9. Changed from the original plan

| Plan | Built | Why |
|---|---|---|
| Components interact "only through events" | Four ordered steps per tick; transitions derived from the frames | A pure event bus cannot express "capacity down, then demand up" without lag or iteration |
| N electrical chain | **2N**, two sides, dual-corded racks | Failover and side overload become real |
| Cooling as a separate subsystem | Cooling draws from the same PDUs | PUE computed, not assumed |
| Plotly + Dash, later a custom viz | One self-contained HTML file; Plotly kept for static analysis | No server, shares by email, works offline |
| Load as GPU profiles | **User traffic** → tokens → power → queue → dropped requests | A failure in kW says nothing about what anyone experienced |
| Fixed 1 s tick implied | Adaptive dt, exact landing on event times | A week at 1 s is 600k ticks; a week at 60 s cannot show a 30 s start |
| Idle / Inference / Training / Burst per rack | Kept, plus **interactive vs batch** | Lost capacity fails requests on one, defers work on the other |

The architectural principle from the plan still holds and is the reason for §7:
**a digital twin of the structure, not of the physical world.**

---

## 10. Code map

```
app/simulations/sim1/
  models.py      every shape passed around: contexts, results, config, specs
  facility.py    the four-step tick
  engine.py      SimPy clock, adaptive dt, frame assembly
  demand.py      arrivals, queue, SLO, scheduler
  workload.py    rack profiles, segments, correlated noise
  rack.py        the load: thermal state machine, power <-> tokens
  thermal.py     the one analytic lumped-mass integrator
  electrical/    grid, transformer, ats, ups, battery, generator, pdu, feed, overload
  cooling/       loop, cdu, chiller, crah, plant
  scenarios.py   the nine scenarios, build_facility(), design_margins()
  telemetry.py   column schema, series contract, event log
  kpis.py        run summary, all dt-weighted
  economics.py   tariff, fuel, unserved compute, downtime
  analysis.py    Plotly figures -> analysis.html
  report.py      artifact writing
  viz/           the single-file visualization
tests/simulations/sim1/
  test_facility.py        conservation gate
  test_worked_example.py  pins the numbers in §2.2
```

Related: [`base_conceptions_ru.md`](base_conceptions_ru.md),
[`sim_0_plan.md`](sim_0_plan.md), and the [README](../README.md).
