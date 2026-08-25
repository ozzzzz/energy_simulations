# Sim-1 — architecture and realisation

> **Что здесь.** Документ описывает то, что реально построено (на английском, как и код).
> Исходный план на русском сохранён без изменений в [приложении](#appendix-original-plan-исходный-план).

Code: [`app/simulations/sim1/`](../app/simulations/sim1/).
Run it: `uv run app sim1-list`, then `uv run app sim1-run --scenario cooling_failure --open`.

---

## 1. What this simulates, in one page

A small AI data centre: **4 GB300 NVL72-class racks, ~540 kW of IT at nominal, ~690 kW at the
meter**, behind a fully redundant (2N) electrical chain and a liquid cooling plant.

Three things make it different from a spreadsheet:

**1. The load comes from users, not from a curve.** Requests arrive on a daily cycle, become tokens
of work, and the scheduler asks each rack for the power needed to serve them. Throughput is read back
off the power the racks actually drew. Whatever was not served queues, and past a latency budget it is
**dropped**. So every failure has an answer in the only units that matter to a business: a chiller trip
costs *23.7 % of user requests*, a clean grid outage with a working generator costs *none*.

**2. Cooling is inside the electrical model.** Chillers, pumps and air handlers draw from the same
PDUs as the racks. Facility load is IT + cooling + conversion loss, so **PUE is computed, not
assumed** (1.27 at nominal). A chiller trip moves the electrical picture in *both* directions at once:
its own ~80 kW of draw disappears while its heat removal collapses.

**3. Every component is a separate object with its own state.** Grid, transformer, ATS, UPS, battery,
generator, PDU, rack, CDU, loop, chiller, CRAH. Each one can fail on its own terms — overload,
overheat, failed start, empty tank, tripped breaker — and each publishes telemetry. Nothing is a
constant.

The site is deliberately sized **right at the edge**, because a build with 40 % headroom everywhere
teaches nothing:

| Question | Answer |
|---|---|
| Can one side carry the whole site at nominal? | Yes — **88 %** of UPS nameplate |
| Can one side carry it at IT peak? | No — **100.5 %**, an overload that ends in bypass |
| Is there enough cooling at IT peak? | Just — **558 kW** of liquid heat against 600 kW of chiller |
| Is there enough compute at the daily traffic peak? | **80 %** utilised by design — but burstiness alone touches 100 % over a week |
| How long do the batteries last? | ~10 min in healthy 2N, ~5 min on one side |
| Does the generator cover the site? | Yes — 800 kW against 688 kW, 112 kW spare |

---

## 2. How the simulation actually runs

### 2.1 One tick, with real numbers

The simulation advances in ticks. Each tick has to answer one question:

> **How many kW does each rack actually get?**

That is harder than it looks, because it depends on two numbers, and neither is
known when the tick starts:

- **What the equipment can supply right now.** Not the nameplate — the *current*
  answer, which changes when a transformer trips, a battery empties or a UPS goes
  to bypass.
- **What the load wants right now.** Also not fixed: it depends on how many users
  are online, and on whether the racks are hot enough to be throttling.

And they interact. You cannot split the load across two sides until you know what
each side can carry; you cannot know what the racks want until you know whether
they are being throttled. So the tick works through them in a fixed order, four
steps, each one a single sweep with no going back. Below is a real tick from
`cooling_failure` at t = 600 s (dt = 30 s), a few minutes before the chiller trips.
Every number is from the run.

#### Step 1 — ask the equipment what it could supply

Before any demand is calculated, each side is asked: *given the state you are in at
this instant, what is the most you could pass through?* The question travels **down**
the chain — utility, transformer, ATS, UPS, PDU — and each component takes the
number handed to it from above and applies its own limit. The last number out is
the answer, so the chain's capacity is set by its **tightest link**:

```
utility A    1500.0 kW   the feed could supply this much
transformer   990.5 kW   its own cap is 1000, less its full-load loss
ATS           990.5 kW   a switch, so it just passes the number through
UPS A         937.5 kW   750 nameplate x 1.25 short-term overload rating
PDU A         796.0 kW   capped at 800, less 0.5 % distribution loss
                         ^ tightest link, so this is side A's answer

side A can deliver 796 kW        side B can deliver 796 kW
```

Nothing is committed here and nothing changes state — it is a question, not an
action. **Asking before demand exists is the whole trick.** A side that failed one
millisecond ago answers "zero", so it is handed zero work, so the other side picks
up everything — in this same tick, with no code anywhere that detects a failure and
switches over. Ask demand first and you would have to go back and redo the split.

#### Step 2 — work out what the load wants

Now demand is assembled from the bottom up, starting from actual user traffic:

```
142.4 requests/s arriving           13:10 on the daily curve, plus jitter
  x 420 tokens per request     =    59,812 tokens/s of work to do
  / 2 racks serving users      =    29,906 tokens/s each
  -> convert work to power     =    121.0 kW asked of rack-2 and rack-3

rack-1 and rack-4 run training, not users. They follow their own
schedule and ask for 125.6 and 133.5 kW.
                                    IT total  =  501.1 kW

Cooling is asked too, because its pumps and compressors are electrical
load like anything else. It sizes itself from the coolant loop's
temperature at the end of the PREVIOUS tick (the one lagged number in
the whole model, see 2.3):
  chiller 89.7 + pumps 20.0 + air handlers 18.0  =  127.7 kW

                                    total wanted  =  628.8 kW

Split across the two sides in proportion to what each can carry
(796 and 796, so evenly):           314.4 kW asked of each side
```

Still nothing committed. If a rack were throttling, its ask would already be capped
here, which is how a thermal problem becomes a power question.

#### Step 3 — hand out the power

628.8 kW wanted against 1592 kW available, so everyone gets what they asked for.
This is the only step that changes anything: batteries charge or discharge, fuel
burns, timers advance, transformer windings heat up.

```
side A:  grid 329.7 kW in
         -2.4 kW  transformer loss
        -11.4 kW  UPS conversion loss
         -1.6 kW  PDU loss
        = 314.4 kW delivered      battery untouched, still 100 %

side B:  identical.        30.6 kW total lost as heat in the chain
```

#### Step 4 — the load consumes it, and physics runs

```
Cooling is paid first, before IT (a deliberate policy — see 2.5):   127.7 kW
IT gets the remainder:                                              501.1 kW
                                                        nothing curtailed

Every watt a rack draws becomes heat. Its temperature is then solved
for the tick:
  rack-4 draws 133.5 kW, sits at 44.7 C, sheds 128.0 kW into its coolant
         the missing 5.5 kW went into warming the rack's own metal
  rack-2 is cooling down after a load dip, so it sheds 2.7 kW MORE than
         it drew - that heat is coming back out of its metal

Heat reaching the cooling system:  451.0 kW liquid (90 %) + 50.1 kW air (10 %)

Work actually done: 29,906 tokens/s x 2 racks = 142.4 requests/s served,
which is everything that arrived, so the queue stays empty.

The loop takes the liquid heat; the chiller rejects 538.2 kW of it
(89.7 kW of electricity x COP 6). More out than in, so the loop is still
cooling towards its 18 C setpoint - it ends the tick at 19.24 C, which is
the number step 2 will use next tick.
```

#### The tick's books

```
in:   659.4 kW from the grid
out:  501.1 IT  +  127.7 cooling  +  30.6 losses  =  659.4 kW
                                       difference =  0.000 kW
PUE this tick = 659.4 / 501.1 = 1.316
```

That is the entire machine. Everything below explains why it is arranged this way.

In the code these four steps are `probe`, `request`, `deliver` and the consume
block, all in [`facility.py`](../app/simulations/sim1/facility.py); every component
implements the first three. The numbers above are pinned by
`tests/simulations/sim1/test_worked_example.py`, so if a change to the model moves
them, that test fails and this section gets updated.

### 2.2 Why four steps and not something simpler

The awkward property of a power chain is that **capacity flows down it and demand
flows up it**, and each depends on the other. Two simpler designs were tried first:

**Let everything lag by one tick.** Compute this tick from last tick's answers and
move on. This is what sim-0 does, and it works there because sim-0 has one lagged
number and nothing that happens fast. Here it breaks: at a 60-second tick the lag
*is* a minute, so a grid failure would be invisible to the UPS for a full minute,
and a 30-second generator start could not be represented at all.

**Iterate until the numbers stop moving.** Guess, recompute, repeat. This buys
precision a teaching model does not need, and it adds "failed to converge" as a new
kind of failure — one you cannot tell apart from a failure the model was supposed to
show you.

Splitting the tick into *ask*, *ask back*, *commit* gets the ordering right in a
single sweep, with no iteration and no lag. The concrete payoff is the one noted in
step 1: failover needs no failover code. Load is divided between the two sides in
proportion to what each can carry, and that one line covers both the healthy case
and the failed one:

```python
def split_2n(cap_a, cap_b, demand_kw, cord_limit_kw):
    total = cap_a + cap_b
    a = demand_kw * cap_a / total      # 796 and 796 -> half each
    b = demand_kw - a                  # 0 and 796   -> all of it to B
```

Two healthy sides split evenly because their capacities are equal. A dead side
takes nothing because its capacity is zero. Same formula, no branch, no special
case — and because step 1 ran before the split, it happens in the tick the failure
occurred.

### 2.3 The one thing that lags

Cooling load depends on IT load depends on available power depends on cooling
load. That circle has to be cut somewhere. It is cut in exactly **one** place: the
chiller sizes its demand from the coolant loop's temperature as of the *end of the
previous tick* (19.24 °C in the trace above).

Why there and nowhere else: a real chiller's capacity control genuinely has tens of
seconds of dead time, so a 30–60 s lag is *more* accurate than instant response;
and the lagged quantity is a slow state variable (5000 L of water ≈ 20,900 kW·s/°C),
not a fast flow.

It also buys the most important behaviour in the whole model. Because rack heat
leaves through a *conductance to a temperature* rather than through a capacity
limit, exceeding cooling capacity does not clip anything — it starts a chain:

> loop temperature rises → each rack's ΔT to its coolant shrinks → the heat a rack
> can shed falls → rack temperature rises → throttle → serving capacity falls →
> requests queue → requests dropped.

No `min()` anywhere in that sequence. That is what makes `cooling_failure` a story
with a timeline instead of a step change.

### 2.4 The tick length changes during a run

At dt = 60 s the entire five-minute battery story is five samples wide, and a 30 s
generator start cannot be represented at all. Running a whole week at 1 s instead
would be 600,000 ticks.

So the engine refines dt inside windows around scheduled events and lands ticks
*exactly* on event times. A week is ~10,000 ticks; the ten interesting minutes get
1-second resolution.

The consequence propagates everywhere: **every aggregate is `Σ p·dt`**, never
`mean × count`. Anything that counts rows over these frames is wrong — including
percentiles, which is why `p95_queue_latency_s` is weighted by time.

### 2.5 Two policies to know before reading a run

- **Cooling outranks IT.** When power is short the pumps keep turning and IT falls
  to zero, not the other way round. Losing the chillers cooks the hall; losing
  GPU-seconds does not. Battery recharge is the lowest priority of all.
- **A saturated queue reads as empty.** Once requests pass the latency budget they
  are *abandoned*, not held — so queue depth hits zero at the exact moment the drop
  rate is worst, and latency pins at the budget. Read the drop rate there.

### 2.6 Conservation

Every tick, for every row:

```
grid_a + grid_b + generator + battery_discharge
    == IT + cooling + conversion losses + battery_charge
```

Worst residual across all nine scenarios: **2.3e-13 kW**. The visualization prints
`in − out` in the corner rather than hiding it, and `test_facility.py` asserts it
per row — including heat stored in the loop, the room air and the racks themselves,
which is the term that would otherwise let the lagged edge hide a real error.

---

## 3. Where the load comes from

```
                              ┌──────────────────────────────────────┐
   daily cycle + burstiness → │ arrivals   152 req/s at 14:00 peak   │
   + scripted surges          │            35 % of that at 02:00     │
                              └──────────────┬───────────────────────┘
                                             │ × 420 tokens/request
                              ┌──────────────▼───────────────────────┐
                              │ scheduler: kw_for(tokens) per rack   │
                              └──────────────┬───────────────────────┘
        rack state caps the ask ─────────────┤ (throttled → 60 %, down → 0)
                              ┌──────────────▼───────────────────────┐
                              │ racks draw, or are curtailed         │
                              └──────────────┬───────────────────────┘
                                             │ tokens_per_s_for(drawn_kw)
                              ┌──────────────▼───────────────────────┐
                              │ served │ queued │ dropped past 8 s   │
                              └──────────────────────────────────────┘
```

**Power ↔ throughput** is linear above an idle floor and invertible, which is what lets the scheduler
ask in units of work: a rack draws `idle_kw` at zero load and `peak_kw` at `peak_tokens_per_s`. A
consequence worth internalising: **peak power and peak throughput are the same point**, so a rack at
its *nominal* draw is already at ~85 % of what it can serve.

**A margin worth knowing about.** The build is sized so the daily traffic peak sits at ~80 % of
serving capacity. Over a full week, the ±8 % correlated burstiness alone pushes the realised peak to
**~102 %** on the busiest days — absorbed by the racks drawing harder, with nothing dropped, but it
means the nominal 20 % of headroom is already spoken for before any surge arrives. `normal` reports
`peak_utilisation_pct` for exactly this reason.

**Two rack segments.** *Interactive* racks are driven by traffic; losing their capacity costs requests.
*Batch* racks (training) follow their own profile; losing their capacity **defers** work — it
accumulates as a kWh backlog they work off later, at up to peak power, over an hour. The default build
runs two of each.

**Queue model.** Backlog in tokens; latency by Little's law (`backlog / served_rate`); abandonment
when the wait exceeds `slo_latency_s`. When served rate is zero and something is waiting, latency is
*undefined*, not infinite — it is emitted as `null`, because `json.dumps` writes bare `Infinity` and
`JSON.parse` rejects it, which would load the visualization blank.

---

## 4. Component reference

### How to read the chain

**The power path exists to do two things**: change the form of the energy so the
next stage can use it, and keep it flowing when the stage above fails.

```
utility 20 kV ─ transformer ─ ATS ─ UPS(+battery) ─ PDU ─ rack PSU ─ GPU
     source      change form   choose   bridge &     split    to DC     work
                               source   condition
                                  └── generator (long-outage source)
```

The grid delivers at 20 kV because high voltage means low current means thin
cables. IT equipment needs 400 V, so a transformer converts. The ATS picks between
utility and generator. The UPS carries the load from battery across the seconds
where neither source is available, and cleans up whatever the utility does to its
waveform. The PDU splits one big feed into many protected rack circuits. The rack's
own supplies turn that into the DC the chips want.

**The heat path exists to get the energy back out of the building.** Nothing in it
destroys energy — every watt that goes into a rack comes out as heat, which is why
cooling capacity, not floor space, is what limits a modern hall.

```
GPU ─ cold plate ─ CDU ─ facility loop ─ chiller/dry cooler ─ outside air
                    │
      the ~10 % not on a cold plate ─ room air ─ CRAH ─ outside air
```

Water carries roughly 3,500 times more heat per unit volume than air, which is the
entire reason liquid cooling exists at these densities.

Each block below: what the component is **for**, its numbers, and **what you would
actually buy**. Vendor names are product *families*, not quotes — ratings and SKUs
move, so check current catalogues. Everything here is standard commercially
available equipment; nothing is bespoke. What the model leaves out is collected in
[§7](#7-what-the-model-deliberately-leaves-out) rather than repeated per component.

---

### 4.1 Utility feed — `electrical/grid.py`

**What it does.** Brings power in from the public grid at medium voltage. It is the
primary energy source; every other electrical component exists either to condition
what arrives here or to substitute for it when it stops. There are **two** feeds
because one is a single point of failure, and they should come from *different
substations* — two feeders off the same substation is a single point of failure
wearing a 2N costume.

**Numbers.** 1500 kW per feed · 20 kV nominal · a brownout state at 65 % of
capacity and 85 % of voltage.

**What you buy.** A medium-voltage service from the utility, plus MV switchgear:
ring main units to isolate sections, protection relays to trip on faults, and
revenue metering. This is usually the longest-lead-time item on the whole project.
> ABB UniGear · Schneider Electric PIX / SM6 / RM6 · Siemens 8DJH / NXPLUS C ·
> Eaton Xiria · Ormazabal CGM. Protection relays: SEL, Schneider Easergy,
> ABB Relion, Siemens SIPROTEC.

**What you'll see it do.** Go `offline` or `brownout` when a scenario says so, and
its `drawn_kw` is the number the tariff bills. When both feeds are down, that is
the signal that starts the generator.

---

### 4.2 Transformer — `electrical/transformer.py`

**What it does.** Steps 20 kV down to the 400 V the rest of the building uses.
Without it you cannot connect racks to the grid at all. It is a passive lump of
iron and copper — very reliable, nothing to go wrong mechanically — but it is also
the **first place a meaningful slice of your energy becomes heat**, about 1 %, and
it can be overloaded. Overloading it cooks the winding insulation: a slow,
cumulative, unrecoverable failure rather than a clean trip.

**Numbers.** 1000 kW · no-load loss 1.5 kW · full-load copper loss 8 kW (≈99.2 %
efficient at nominal) · winding overheats at 140 °C · instantaneous trip at 150 % ·
tolerates moderate overload for 30 minutes.

**What you buy.** A **cast-resin dry-type** distribution transformer, 1000–1250 kVA,
20 kV → 400 V, indoors next to the LV switchboard. Dry-type is the normal choice
inside a DC building: no oil to contain, better fire behaviour. Oil-filled is
cheaper and marginally more efficient if it can sit outdoors.
> Siemens **GEAFOL** · Schneider Electric **Trihal** · ABB **RESIBLOC** ·
> Hitachi Energy · Eaton · Legrand/Zucchini · TMC Transformers · Trafo Elettro.

**What you'll see it do.** Its loss grows with the *square* of load, so the winding
temperature climbs visibly when one side carries the whole site. Trip it in a
scenario and that side's capacity goes to zero, which is how `side_a_lost` starts.

---

### 4.3 Automatic transfer switch — `electrical/ats.py`

**What it does.** Chooses which source feeds this side: utility or generator, and
automates the changeover so nobody has to run to the switchroom at 03:00. It is
**break-before-make** — it disconnects one source before connecting the other —
because back-feeding the utility from your generator would electrocute a lineman
and is illegal. The unavoidable consequence is a dead interval during the switch,
**and that gap is the entire reason the UPS exists.**

**Numbers.** transfer 1.0 s · returns to utility only after the mains have been
stable for 60 s.

**What you buy.** An open-transition ATS sized for the full side load, or a pair of
mechanically interlocked breakers with a controller. Closed-transition switches
exist if you want no break at all, at more money and more protection complexity.
> ASCO (Schneider) **7000 series** · Socomec **ATyS** · Eaton ATC / **Power
> Xpert** · Cummins **OTPC / PowerCommand** · Generac **RTS** · Kohler.
> Breaker-pair route: ABB Emax 2, Schneider Masterpact MTZ, Siemens 3WA, with a
> Deep Sea or ComAp controller.

**What you'll see it do.** Sit on `on_primary`, then `transferring` (output zero),
then `on_generator`. The 60 s anti-flap delay is skipped if the generator has quit
— waiting on a dead source is the wrong trade. In `grid_outage_gen_ok` both sides
transfer out and back, four transfers in total.

---

### 4.4 UPS — `electrical/ups.py`

**What it does.** Two jobs that get conflated. **(1) Bridge**: carry the load from
its battery across the seconds between losing the utility and the generator taking
over. **(2) Conditioner**: in double-conversion mode it rectifies the incoming AC
to DC and rebuilds a clean AC waveform from scratch, so sags, spikes, frequency
wander and harmonics on the utility never reach the IT equipment.

You pay for that insurance continuously: about **3.5 % of every kWh** the site
consumes, forever, as conversion loss. That is the single largest avoidable line in
your PUE, and it is why eco-mode exists.

**Numbers.** 750 kW · rectifier 98.5 % × inverter 98 % = **96.5 %** · bypass 99.9 %
· delivers up to **125 %** of nameplate short-term · instantaneous trip 150 % ·
hold timer 60 s at overload.

**What you buy.** A modular three-phase VFI (double-conversion) UPS, 750–800 kW,
ideally in `n+1` power modules inside the frame so one can be swapped while live.
96–97 % is current state of the art for double conversion.
> Schneider Electric **Galaxy VX / VL** · Vertiv **Liebert Trinergy Cube /
> EXL S1** · Eaton **93PM / Power Xpert 9395** · ABB **DPA 250 S4 / MegaFlex** ·
> Huawei **UPS5000** · Delta **DPH / Ultron** · Mitsubishi Electric **9900** ·
> Riello Multi Power · Legrand Trimod.

**What you'll see it do.** The most instructive behaviour in the electrical model:
under sustained overload it does **not** drop the load — it transfers to static
**bypass**, passing the utility straight through. The site survives and silently
loses battery protection, so the *next* grid event is the one that kills it. That
is exactly what `side_a_lost_at_peak` demonstrates.

---

### 4.5 Battery string — `electrical/battery.py`

**What it does.** The UPS's energy store, and its only job is to **buy time** —
enough seconds to start a generator and transfer to it. It is *not* there to run
the site. Sizing it is a bet on how fast your generator starts, which is why five
minutes is the modern norm (gensets start in 10–30 s and you want margin) rather
than the 10–15 minutes older sites specified. Beyond that, a bigger battery is
mostly wasted capital unless you have no generator at all.

**Numbers.** 60 kWh per side · usable down to 5 % SoC · discharge ≤ 800 kW, charge
≤ 100 kW · 96 % each way.

**The sizing story in one number.** Each side holds ~5 minutes at the full *site*
load. In healthy 2N each side carries half, so the site gets **~10 minutes**; lose
a side and the survivor is back to 5.

**What you buy.** **Lithium-ion** cabinets are the default for new builds: a third
of the footprint of VRLA, 10–15 year life, thousands of cycles, integrated BMS. LFP
chemistry if you weight safety and cycle life; NMC if you weight volume.
> Matched to the UPS: Vertiv **HPL** · Schneider **Galaxy Li-ion** · Eaton
> **xStorage / Li-ion UPM** · Huawei **SmartLi** · Delta. Cells and modules:
> Samsung SDI, LG Energy Solution, CATL, EVE, Narada, Soundon. VRLA if you must:
> EnerSys **PowerSafe / DataSafe**, C&D, Yuasa, Leoch.

**What you'll see it do.** Report **remaining autonomy every tick**, discharging or
not — "if the grid dropped right now, you have 9.4 minutes" is far more useful than
a number that only appears during an outage. Watch it drain to the 5 % cutoff in
`grid_outage_gen_fail`, and watch it quietly drain in `side_a_lost` where UPS A has
no input at all.

---

### 4.6 Diesel generator and fuel — `electrical/generator.py`

**What it does.** The actual answer to a long outage. The battery buys minutes; the
generator provides hours or days, limited only by what is in the tank. It converts
stored chemical energy into electricity on demand.

The property that matters is not its rating but **whether it starts**. A genset
that fails to start is worth exactly nothing, which is why real sites test them
under load monthly and why serious sites buy two. There is deliberately only
**one** here — it is the single point of failure in an otherwise 2N site, and
`grid_outage_gen_fail` exists to make that concrete.

**Numbers.** 800 kW · start 30 s · 98 % start success, up to 3 attempts 10 s apart
· output ramps over 10 s · 5-minute cooldown after the mains return · fuel
`8.0 + 0.24 × kW` L/h — about 200 L/h at full load, **0.25 L/kWh** · 4000 L tank,
roughly 20 hours.

**What you buy.** A standby-rated diesel genset, 800–1000 kVA, with radiator,
exhaust and silencer package, a day tank plus bulk storage, and a controller that
does mains-failure detection itself. NFPA 110 Level 1 wants rated voltage and
frequency within **10 s**; the 30 s here is conservative.
> Caterpillar **C32 / 3512** · Cummins **QSK / C-series** · MTU Onsite Energy
> (Rolls-Royce) **Series 4000** · Kohler-SDMO · FG Wilson (Perkins) · Generac
> Industrial · Himoinsa · Atlas Copco. Controllers: Deep Sea Electronics,
> ComAp InteliGen, Woodward easYgen.

**What you'll see it do.** Crank for 30 s, then either come up and ramp, or fail
and retry twice before latching `failed` for good. Fuel burn is **affine**, not
proportional — a lightly loaded genset is inefficient, which is why sites avoid
running them near idle. Its output is shared between both sides in proportion to
what each asks for.

---

### 4.7 PDU / busway — `electrical/pdu.py`

**What it does.** Takes one large conditioned feed and splits it into many
rack-level circuits, each with its own breaker, so a fault in one rack does not
take out the hall. Busway specifically lets you add, move or change rack power
without re-pulling cable — which matters because rack density changes far faster
than buildings do.

**Numbers.** 800 kW · 99.5 % efficient · trip at 160 % · hold 120 s. A separate
facility-level `cord_limit_kw` caps what one side may deliver to the load bus.

**What you buy.** For NVL72-class racks, **overhead busway** with tap-off boxes is
the normal answer, fed from a switchboard or remote power panel. Inside the rack it
is a busbar with PSU shelves rather than a traditional rack PDU. The real thing is
**dual-corded end to end** — A and B feeds into separate shelves.
> Busway: **Starline** (Legrand) · Anord Mardix (Flex) · Eaton Pow-R-Way ·
> Schneider Canalis · Siemens. Floor PDUs / RPPs: Vertiv **Liebert PPC/PPA** ·
> Schneider **EcoStruxure Row PDU** · Eaton. Rack metering: Raritan, ServerTech,
> APC AP89xx.

**What you'll see it do.** Mostly nothing — which is the point of a well-sized
distribution layer. The exception is `undersized_cords`, where the cord limit binds
and IT is curtailed even though the surviving UPS has headroom to spare: 2N on
paper, not in the cable.

---

### 4.8 Rack — `rack.py`

**What it does.** The thing you built the data centre for; everything else is
overhead. In a GB300 NVL72 the **rack is the unit of compute** — 72 GPUs on one
NVLink domain behave as a single large accelerator, which is why you buy a rack
rather than servers. It converts electricity into computation and, at effectively
100 %, into heat.

It also protects itself. When it gets too hot it slows down (throttle), and if that
is not enough it stops (emergency shutdown), rather than damaging silicon. Those
two thresholds are where a cooling problem becomes a compute problem.

**Numbers.** 135 kW nominal / 155 kW peak · idle draw 15 % of nominal (20.25 kW) ·
40,000 tokens/s at peak power · throttle at 85 °C to 60 % draw, shut down at 95 °C,
recover at 70 °C with 5 °C of hysteresis · sits ~30 °C above its coolant at peak,
time constant ~174 s · 90 % of heat to liquid, 10 % to air.

**What you buy.** **NVIDIA GB300 NVL72** — 72 Blackwell Ultra GPUs and 36 Grace
CPUs in one liquid-cooled rack, sold as an integrated rack by OEMs rather than as
loose servers. Cold plates on GPUs, CPUs and NVSwitch; the residual (optics,
storage, DPUs, PSU losses) goes to air. Published power is in the 120–140 kW range
per rack depending on configuration.
> OEM integrators: **Dell** (PowerEdge XE / IR7000) · **HPE** · **Supermicro** ·
> **Lenovo** · Foxconn/Ingrasys · Wiwynn · Quanta/QCT · Gigabyte · ASUS ·
> ZT Systems (AMD). Comparable density elsewhere: AMD Instinct MI355X platforms,
> Google TPU (cloud only), Cerebras, Groq.

**What you'll see it do.** Draw exactly what it is granted, and convert power to
throughput linearly above its idle floor. A consequence worth internalising: peak
power and peak throughput are the same point, so **a rack at its nominal draw is
already at ~85 % of what it can serve**. Watch it throttle then shut down in
`cooling_failure`, taking serving capacity with it.

---

### 4.9 Workload and users — `workload.py`, `demand.py`

**What it does.** Supplies the demand, which is the only thing that makes any of
the sizing above meaningful. Requests arrive on a daily cycle with correlated
jitter; each carries a number of tokens of work; a scheduler turns that into a
power ask per rack. Racks are either **interactive** (driven by traffic — losing
capacity fails requests) or **batch** (training — losing capacity *defers* work,
which accumulates as a kWh backlog they work off later).

White noise would be wrong for either: real load wanders over minutes, and
uncorrelated noise averages away exactly the excursions that stress a power chain
or overflow a queue.

**Numbers.** 76 req/s per interactive rack at the 14:00 peak (152 for the default
two) · 35 % of that at 02:00 · ±8 % jitter with a 300 s time constant · 420 tokens
per request · 8 s latency budget before a request is abandoned · batch catch-up
spread over an hour.

**What you buy.** This is software, and it is where a real deployment's numbers
come from — **measure tokens/s per rack and tokens/request on your own model and
traffic** rather than taking these.
> Serving stacks: **vLLM** · **NVIDIA TensorRT-LLM** + **Dynamo** / Triton ·
> **SGLang** · Hugging Face TGI · Ray Serve · KServe. Gateways and queueing:
> Envoy / Istio, LiteLLM, Kong. Measurement: NVIDIA **GenAI-Perf**, vLLM's own
> benchmark harness, k6, Locust. Batch schedulers: Slurm, Kubernetes + Kueue,
> Run:ai, Volcano.

**What you'll see it do.** Traffic rises and falls across the day; the queue fills
when offered work exceeds what the racks can serve; and past the 8 s budget
requests are dropped rather than held. That drop percentage is the number that
turns "the loop reached 87 °C" into something a business can read.

---

### 4.10 CDU and pumps — `cooling/cdu.py`

**What it does.** Sits on the boundary between two water loops: the clean,
tightly-controlled *technology* loop that goes into the racks' cold plates, and the
*facility* loop that goes outdoors. It exists for **isolation** — a leak or
contamination in the facility loop must never reach three million dollars of GPUs —
plus pressure and temperature control at the rack. It also contains the pumps that
actually move the heat.

Small in kW, decisive in effect: heat transfer from cold plate to coolant scales
with flow, so losing pump power does not degrade cooling proportionally to the
power lost — it stops cooling almost entirely while the chiller carries on
compressing nothing.

**Numbers.** 20 kW of pump power at design flow.

**What you buy.** A **liquid-to-liquid CDU** with redundant pumps, filtration and a
plate heat exchanger. NVL72 reference designs use CDUs in the ~1.3 MW class; four
racks at ~550 kW is one mid-size unit, or a redundant pair if you want N+1.
> **CoolIT Systems** CHx / AHx · **Vertiv** XDU (e.g. XDU1350) · **Motivair**
> MCDU · **Boyd** · **nVent** (in-row / in-rack) · Stulz CyberCool CMU ·
> LiquidStack · Delta · Envicool · Chilldyne (negative-pressure, leak-safe).
> Manifolds and quick-disconnects: CPC, Staubli, Parker.

**What you'll see it do.** Get served **before** the compressors when power is
short, because a chiller with no flow past it is useless. Its flow fraction scales
the rack-to-coolant conductance, so partial pump power shows up as a worse ΔT
rather than a capped kW.

---

### 4.11 Coolant loop — `cooling/loop.py`

**What it does.** The transport mechanism. Water carries roughly 3,500 times more
heat per unit volume than air, which is the whole reason liquid cooling exists at
these densities. Its job is to move heat from the cold plate to somewhere it can be
rejected.

Its **volume is also a thermal flywheel**, and that is worth designing
deliberately: the stored water is what buys you minutes when cooling fails. At
540 kW in and nothing out this loop climbs **1.55 °C/min**, so a chiller trip takes
about twenty minutes to collapse the rack ΔT. Halving the volume halves that and
turns a story into a cliff.

**Numbers.** 5000 L (≈20,900 kW·s/°C) · supply 20 °C with a 12 °C floor · loop heat
capacity rate 60 kW/°C.

**What you buy.** Pipework (stainless or HDPE), a buffer tank sized for exactly the
ride-through above, an expansion vessel, side-stream filtration, a chemical dosing
pot, and either treated water or PG25 (25 % propylene glycol) if there is any
freeze risk. Leak detection under the floor and at every manifold.
> Heat exchangers: **Alfa Laval** · **Kelvion** · SWEP · API. Pumps:
> **Grundfos** · KSB · Wilo · Armstrong · Xylem. Valves and controls: Belimo,
> Siemens, Danfoss. Water treatment: Nalco (Ecolab), Kurita. Leak detection: TTK,
> RLE, nVent Raychem.

**What you'll see it do.** Drift a degree or two under normal load, and climb
steadily when removal falls short — the gap between "heat generated" and "heat
rejected" on the cooling chart *is* the loop warming up.

---

### 4.12 Chiller / heat rejection — `cooling/chiller.py`

**What it does.** **The exit door for energy.** Everything else in the cooling chain
moves heat around; this is the only component that gets it out of the building, by
rejecting it to outside air or water.

It is also where cooling costs you real electricity — most of the gap between
PUE 1.0 and PUE 1.27 is here. And because its draw comes off the *same PDUs* as the
racks, it is the component that couples cooling back into the power model: a
chiller trip moves the electrical picture in both directions at once, freeing ~90 kW
of draw while its heat removal collapses.

**Numbers.** 600 kW of removal · COP 6.0 (six units of heat moved per unit of
electricity) · 18 °C setpoint · proportional control with a 120 s lag.

**What you buy.** An 18 °C supply is **warm-water** territory, so in practice this
position is *dry coolers with adiabatic assist* for most of the year plus a **trim
chiller** for the hot hours — which is exactly where a COP of 6+ comes from. A
conventional 7 °C chilled-water plant sits nearer COP 3–5 and would not reach these
numbers. 600 kW ≈ 170 tons refrigeration.
> Free cooling / dry coolers: **Vertiv Liebert AFC** · **Güntner** · **Kelvion** ·
> Thermokey · Alfa Laval. Chillers: **Carrier AquaForce** · **Trane Series R /
> Sintesis** · **York (JCI) YVAA / YZ** · **Daikin Applied** · Swegon ·
> Climaveneta (Mitsubishi) · LG · Midea. Cooling towers if you accept water use:
> Baltimore Aircoil, SPX Marley, Evapco.

**What you'll see it do.** Modulate smoothly with load, and drop straight to zero
draw when tripped. The 120 s control lag is not cosmetic — without it the
cooling↔power feedback produces a two-tick oscillation whenever the site sits
exactly at capacity.

---

### 4.13 Air side — `cooling/crah.py`

**What it does.** Handles what the cold plates do not. Not everything in a rack can
have a plate bolted to it — optics, DPUs, storage, PSU losses — and that residual,
about 10 % of rack power, heats room air. The CRAHs keep that air within spec for
those parts.

Room air has very little thermal mass compared to the water loop, so when the air
handlers fall behind the temperature moves within *minutes* and drags the racks'
blended sink temperature up with it.

**Numbers.** 100 kW · COP 3.0 — worse than the chiller's, which is precisely why
liquid cooling is worth the plumbing · 24 °C setpoint · 1000 m³ room · a small
leakage term to outdoors.

**What you buy.** Chilled-water CRAHs or in-row units sized for the residual air
load only. In a direct-liquid-cooled hall this is a fraction of what a legacy
air-cooled hall needs, which is the entire economic argument for DLC.
> **Vertiv Liebert CRV / PDX / DSE** · **Stulz CyberAir** · **Schneider Uniflair** ·
> Rittal LCP · Munters · Airedale (Modine) · Systemair.

**What you'll see it do.** Track the 10 % air share quietly. Its contribution to
the racks' sink temperature is blended with the liquid loop's, weighted by the
capture rate — so a hot room raises rack temperatures even when the water is fine.

---

### 4.14 Monitoring and economics — `telemetry.py`, `kpis.py`, `economics.py`

**What it does.** You cannot operate what you cannot see, and you cannot justify
what you cannot cost. Every component publishes a flat telemetry dict; the facility
row is assembled by prefixing them, so adding a component means adding one dict
rather than editing three lists of column names. Discrete transitions are *derived*
from the resulting frames rather than emitted by the components, so the event log
cannot disagree with the time series it was built from.

Costs are four separate lines because they answer four different questions: grid
energy at a time-of-use tariff, diesel, curtailed compute as an opportunity cost,
and downtime as an SLA step function.

**Numbers.** $0.09/kWh off-peak, $0.19/kWh 08:00–20:00 · $1.10/L diesel ·
$3.00/kWh of unserved compute · $500/min while the hall draws nothing.
> The widely quoted $9,000/min downtime figure is a hyperscale number and would be
> absurd for four racks.

**What you buy.** DCIM for assets and capacity, a BMS for the mechanical plant, and
power metering wherever you want a number you can defend.
> DCIM: **Vertiv Trellis** · **Schneider EcoStruxure IT** · Nlyte · Sunbird ·
> Hyperview · NetBox (open source, inventory only). BMS/SCADA: Siemens Desigo,
> Schneider EcoStruxure Building, Johnson Controls Metasys, Ignition. Metering:
> **Schneider PowerLogic** · Socomec **Diris** · Janitza · Accuenergy.
> Rack and environmental: Vutlan, AKCP, Raritan.

**What you'll see it do.** Produce the three frames every chart and KPI is built
from, and the event log the visualization's timeline reads.

---

## 5. Reference build, in numbers

| | Value | Where it comes from |
|---|---|---|
| Racks | 4 × 135 kW nominal / 155 kW peak | `SiteConfig.rack_*` |
| IT load | **540 kW** nominal, **620 kW** peak | 4 × the above |
| Liquid heat at nominal | 486 kW → 81 kW electrical | 90 % capture, COP 6 |
| Air heat at nominal | 54 kW → 18 kW electrical | 10 % residual, COP 3 |
| Pumps | 20 kW | fixed |
| **Cooling electrical** | **119 kW** at nominal, 134 kW at peak | computed |
| UPS output | 659 kW at nominal, 754 kW at peak | IT + cooling |
| UPS loss | ~23 kW | 96.5 % double conversion |
| Transformer loss | ~5 kW | quadratic copper term |
| **Facility total** | **688 kW** → **PUE 1.27** | computed, not assumed |
| Per side | 1000 kW transformer · 750 kW UPS · 60 kWh battery · 800 kW PDU | |
| Generator | 1 × 800 kW, 30 s start, 4000 L | 112 kW spare over facility nominal |
| Cooling plant | 600 kW liquid + 100 kW air, 5000 L loop | |
| Traffic | 152 req/s at the daily peak, 420 tokens each | 80 % of capacity by design, ~102 % realised peak over a week |
| Serving capacity | 190 req/s across two interactive racks | 40 000 tokens/s per rack |

`uv run app sim1-list` prints this table from the config — it is computed, so it cannot drift from
what the code actually does.

---

## 6. Scenarios

| Scenario | What it demonstrates | Users hit | Incident at | Run |
|---|---|---|---|---|
| `normal` | steady state: traffic follows the day, 50/50 split, PUE ≈ 1.27, reserves untouched | none | — | 7 d @ 60 s |
| `grid_outage_gen_ok` | both feeds drop → UPSes ride it on battery → 30 s crank → ATS transfers → diesel burns → batteries recharge | **none** | 10 min | 1 h @ 10 s |
| `grid_outage_gen_fail` | three failed starts → latched failure → batteries to cutoff → the site goes dark | 56.2 % dropped | 5 min | 35 min @ 5 s |
| `user_surge` | all four racks serve users, traffic to 1.5×. Power and cooling hold; the racks hit their **throughput** ceiling, the queue fills to the 8 s budget and requests are abandoned | 9.3 % dropped | 15 min | 2 h @ 15 s |
| `load_spike` | the power-and-cooling question with users out of the way: four batch racks pinned at peak. Power fine, cooling is the tighter margin (~43 kW of chiller headroom) | n/a | 15 min | 2 h @ 15 s |
| `cooling_failure` | chiller trips → loop climbs ~1.5 °C/min → rack ΔT collapses → throttle → shutdown → restored 90 min later | 25.9 % dropped | 15 min | 3 h @ 30 s |
| `side_a_lost` | transformer A trips; UPS A quietly burns its battery down, then side B carries the site at 92 % | none | 15 min | 90 min @ 15 s |
| `side_a_lost_at_peak` | a purely electrical test — all batch at peak. UPS B crosses 100 %, its hold timer expires, it transfers to **bypass**: load survives, battery protection is gone | n/a | 10 min | 1 h @ 10 s |
| `undersized_cords` | 2N on paper only (cords rated for 60 % of peak): losing a side curtails IT, and the curtailment lands on requests | 20.9 % dropped | 15 min | 90 min @ 15 s |

### The clock, and why incidents happen at minute 15

Two constraints pull against each other. A failure only teaches something if the site was **busy** when
it hit — a transformer trip at 03:00 says nothing about whether one side can carry the load. But nobody
scrubs through twelve idle hours to reach 14:00.

`SiteConfig.start_hour` resolves it: the wall-clock time that simulated `t = 0` corresponds to. The
incident scenarios start at **13:00**, so a failure fifteen minutes into the run lands at 13:15, right
on the peak. Runtimes drop from 16–20 hours to 35 minutes–3 hours, and the whole run sits at
representative load instead of averaging a quiet night into every KPI — which is why the drop
percentages above are higher than the longer runs reported.

The offset applies to the traffic curve, the rack workload profiles **and** the electricity tariff
together. Applying it to only some would leave the model disagreeing with itself about what time it is
— billing a 13:00 start at the night rate, for instance.

---

## 7. What the model deliberately leaves out

Worth knowing before quoting a number from it.

**Electrical.** Breakers and protection coordination, earthing, surge protection, arc-flash, harmonics,
power factor, N+1 within the UPS, a second generator, paralleling and load sharing, load banks.

**Mechanical.** N+1 on chillers, CDUs and CRAHs; pressure and flow hydraulics; ambient-dependent COP;
humidity; hot/cold aisle airflow; water consumption; leak detection.

**IT.** Individual GPUs, PSUs and fans; the NVLink fabric; storage and networking as loads; MTBF and
repair time; firmware and driver behaviour.

**Everything else.** Fire detection and suppression, physical security, staffing and MTTR, commissioning,
capex, carbon, grid demand-response, multi-site failover.

The architectural principle from the original plan still holds and is the reason for most of the above:
**this is a digital twin of the structure, not of the physical world.** Each simplification sits behind
a replaceable object, so any of them can be upgraded without touching the tick.

---

## 8. Where the original plan changed, and why

| Plan | Realisation | Why |
|---|---|---|
| Components interact "only through events" | Four ordered phases per tick; discrete transitions derived from the frames | A pure event bus cannot express "capacity down, then demand up" without either lag or iteration. Failover works *inside* one tick because of it. |
| N electrical chain (one of each) | **2N** — two sides, dual-corded racks | Failover, side overload and single-point-of-failure become real rather than described. |
| Cooling as an independent subsystem | Cooling draws from the same PDUs | PUE becomes computed rather than assumed, and a chiller trip perturbs the electrical picture. |
| Plotly + Dash, "later a custom web viz" | One self-contained HTML file with an animated flow diagram, scrubber and playback; Plotly kept for static analysis | The single file needed no server, shares by email, and works offline. Dash for sim-1 would have been a second untested surface. |
| Load as GPU profiles | **User traffic** → tokens → power, and capacity loss → queue → dropped requests | A failure measured only in kW says nothing about what anyone experienced. |
| Fixed 1-second tick implied | Adaptive dt, exact landing on event times | A week at 1 s is 600 k ticks; a week at 60 s cannot represent a 30 s generator start. |
| "Idle / Inference / Training / Burst" per rack | Kept, plus an **interactive vs batch** split | Losing capacity fails requests on one and defers work on the other — different consequences, different KPIs. |

---

## 9. Where things live

```
app/simulations/sim1/
  protocols.py     the probe/request/deliver contract every component obeys
  facility.py      the four-phase tick
  engine.py        SimPy clock, adaptive dt, frame assembly
  demand.py        arrivals, queue, SLO, the scheduler
  workload.py      rack profiles, segments, correlated noise
  rack.py          the load: thermal state machine, power <-> tokens
  thermal.py       the one analytic lumped-mass integrator
  electrical/      grid, transformer, ats, ups, battery, generator, pdu, feed, overload
  cooling/         loop, cdu, chiller, crah, plant
  scenarios.py     SiteConfig, the nine scenarios, design_margins()
  telemetry.py     column schema, series contract, event log
  kpis.py          run summary, all dt-weighted
  economics.py     tariff, fuel, unserved compute, downtime
  analysis.py      Plotly figures -> analysis.html
  report.py        artifact writing, scenario comparison
  viz/             the single-file visualization (template + css + 4 js modules)
tests/simulations/sim1/   171 tests; test_facility.py is the conservation gate
```

Related docs: [`base_conceptions_ru.md`](base_conceptions_ru.md) (the concepts this rests on),
[`sim_0_plan.md`](sim_0_plan.md) (the rack-centric predecessor), and the
[README](../README.md#sim-1--the-whole-power-and-cooling-chain-docssim_1_planmd) for how to run it.

---
---

## Appendix: original plan (исходный план)

*Preserved verbatim. Section 8 above lists where the realisation departs from it.*

Архитектура первой симуляции дата-центра

Цель

Построить не максимально реалистичную, а максимально обучающую симуляцию небольшого AI-дата-центра.

Симуляция должна позволять постепенно увеличивать сложность модели без переписывания архитектуры.

Основная задача первой версии — понять, как энергия проходит через всю систему, где появляются ограничения и что происходит при отказах.

⸻

Принципы

1. Все компоненты моделируются как независимые объекты.
2. Каждый компонент имеет собственное состояние.
3. Компоненты взаимодействуют только через события.
4. Любое изменение состояния должно быть наблюдаемым.
5. Любой компонент можно заменить реальным аналогом.

⸻

Предлагаемый стек

Backend

Python

Основная библиотека:

* SimPy

Почему именно SimPy:

* дискретно-событийное моделирование;
* естественное моделирование времени;
* процессы;
* события;
* ожидания;
* отказоустойчивость.

⸻

Визуализация

Первая версия

* Plotly
* Dash

Позже

* React Flow
* Cytoscape
* собственная web-визуализация

⸻

Архитектура модели

Power Grid
      │
      ▼
Transformer
      │
      ▼
UPS
      │
      ▼
Battery
      │
      ▼
Generator
      │
      ▼
Power Distribution Unit
      │
      ▼
Server Racks
      │
      ▼
GPU

Параллельно существует независимая подсистема охлаждения.

Cooling Plant
      │
      ▼
Cooling Loop
      │
      ▼
Racks

⸻

Компоненты первой версии

1. Внешняя сеть

Состояния

* Online
* Offline

Параметры

* напряжение
* максимальная мощность
* вероятность отказа

События

* отключение
* восстановление

⸻

2. Трансформатор

Параметры

* максимальная мощность
* КПД
* температура

Отказы

* перегрев
* перегрузка

⸻

3. UPS

Параметры

* мощность
* КПД
* режим работы

Состояния

* Bypass
* Online
* Battery

⸻

4. Батареи

Параметры

* емкость
* уровень заряда
* скорость заряда
* скорость разряда

Главная задача

Пережить запуск генератора.

⸻

5. Генератор

Параметры

* номинальная мощность
* расход топлива
* время запуска
* вероятность успешного запуска

Состояния

* Stop
* Starting
* Running
* Failed

⸻

6. PDU

Распределяет питание по стойкам.

Пока без сложной логики.

⸻

7. Стойки

Первая версия

4 стойки NVIDIA GB300 NVL72

Параметры

* номинальная мощность
* пиковая мощность
* температура
* состояние

⸻

8. GPU-нагрузка

Профили

Idle

Inference

Training

Burst

Каждый профиль имеет

* среднюю мощность
* пики
* длительность

⸻

9. Охлаждение

Параметры

* максимальная мощность отвода тепла
* КПД
* температура теплоносителя

Если охлаждение не справляется

↓

растет температура стоек

↓

троттлинг

↓

аварийное выключение

⸻

Что должно происходить во времени

Каждая секунда симуляции может содержать

* изменение нагрузки
* изменение температуры
* заряд батареи
* расход топлива
* переключение UPS
* запуск генератора
* аварии
* восстановление

⸻

Референсная конфигурация

IT

4 × NVIDIA GB300 NVL72

Нагрузка

примерно

540 кВт номинально

до 620 кВт пиковой

UPS

750 кВт

Батареи

около 5 минут при полной нагрузке

Генератор

800 кВт

время запуска

30 секунд

Охлаждение

600 кВт жидкостного охлаждения

100 кВт воздушного

⸻

Сценарии первой версии

Сценарий 1

Нормальная работа.

⸻

Сценарий 2

Отключение внешней сети.

UPS удерживает нагрузку.

Генератор успешно запускается.

⸻

Сценарий 3

Отключение сети.

Генератор не запускается.

Батареи постепенно разряжаются.

Дата-центр отключается.

⸻

Сценарий 4

Резкий рост AI-нагрузки.

Проверка:

* хватает ли мощности
* хватает ли охлаждения

⸻

Сценарий 5

Отказ охлаждения.

Температура растет.

GPU начинают троттлить.

При достижении критической температуры происходит аварийное отключение.

⸻

Что необходимо отображать

Электрика

* потребляемая мощность
* доступная мощность
* состояние сети
* состояние UPS
* заряд батарей
* работа генератора

⸻

Серверы

* загрузка
* температура
* энергопотребление

⸻

Охлаждение

* текущая температура
* запас мощности охлаждения

⸻

Итоговые показатели

* uptime
* потраченная энергия
* расход топлива
* время работы на батареях
* число отказов
* стоимость эксплуатации

⸻

Архитектурный принцип

Важно не пытаться сразу сделать «правильную физику».

Нужно построить цифровой двойник структуры, а не цифровой двойник реального мира.

Если архитектура компонентов будет удачной, позже можно постепенно заменять упрощённые модели на реальные физические расчёты, добавлять настоящие характеристики оборудования, учитывать экономику, стоимость электроэнергии, деградацию батарей, надежность компонентов и даже моделировать взаимодействие дата-центра с внешней энергосистемой.

Именно такая эволюционная архитектура позволит превратить учебную симуляцию в полноценную исследовательскую платформу.
