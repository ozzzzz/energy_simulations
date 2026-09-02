# Energy Simulations

Simulations of AI data center energy systems (SimPy for the clock), with a CLI to run scenarios and
two viewers: a static Plotly report for sim-0 and a single self-contained HTML page for sim-1, which
`app sim1-video` can also record as an MP4.

## Simulations

### Sim-0 — rack-centric ([docs/sim_0_plan.md](docs/sim_0_plan.md))

4 GPU racks as the modeled core; power/cooling/workload are input signals. Cooling is split into
liquid (direct-to-chip, handles most of the load) and air (residual). Default run length is one
week at a 1-minute tick, long enough to show the daily peak (users active) / trough (asleep) cycle.

Scenarios (`--scenario`):

| Scenario | Profile |
|---|---|
| `inference` | user-driven traffic: short request spikes, follows the day/night cycle |
| `training` | scheduled batch jobs: sustained near-peak draw with rare dips (checkpoint/sync), runs flat around the clock |
| `mixed` | rack 1 trains, racks 2-4 serve inference, all at once |
| `cooling_failure` | training load + a scripted CDU/chiller incident on day 3 (cooling cut to 15% for 1.5 days) — exercises throttle/shutdown/recovery |

```bash
uv sync

# KPIs, a CSV of the time series, and a static HTML report with the graphs
uv run app sim0-run --scenario inference --output out/sim0/inference.csv --html out/sim0/inference.html
```

`just sim0-all` does all four scenarios into `out/sim0/`. Add `--duration <seconds>` / `--dt <seconds>`
to override the default week / 1-minute tick.

The report shows four figures. The centrepiece is a Sankey tracing where every kW of draw
actually goes — demand splits into delivered vs curtailed, delivered draw splits into useful IT compute
vs PSU/VRM loss, and all of it ends up as heat split between liquid and air — plus a cooling-by-channel
chart and a per-rack drill-down (demand vs draw, temperature). Where the scenario has a cooling
incident, the report compares the whole run against the incident window and against the run outside
it, one section each.

The figures live in [`figures.py`](app/simulations/sim0/figures.py), separate from the report that
writes them, so a figure can be built and tested without going through file output.

Note: "power available" (electricity the rack can draw — an energy *input*) and "cooling available" (heat
the site can remove — a capacity for an *output*) are different physical quantities, not two flavors of the
same thing, even though both happen to be measured in kW.

### Sim-1 — the whole power and cooling chain ([docs/sim_1_plan.md](docs/sim_1_plan.md))

Where sim-0 collapses everything upstream of the racks into two constant input signals, sim-1 models
the chain itself as independent objects, in **2N**: two utility feeds, each with its own transformer,
ATS, UPS, battery string and PDU, plus one shared diesel generator with a fuel tank. Racks are
dual-corded and draw from both sides.

Cooling is a **closed loop**: the chiller, CDU pumps and CRAHs draw electricity from the same PDUs, so
facility load is IT + cooling + conversion losses and **PUE is computed rather than assumed**. A
chiller trip therefore moves the electrical picture in both directions at once — its own ~80 kW of
draw disappears while its heat removal collapses.

And the load is not a curve someone drew — it comes from **users**:

```
users arrive → requests become tokens of work → the scheduler asks the racks for the power
needed to serve them → the racks draw it (or cannot) → served throughput follows from actual
draw → whatever was not served queues → past the latency budget, requests are dropped
```

So a failure is legible in the terms that matter. A chiller trip is not just "the loop reached
87 °C": it throttles the racks, which cuts serving capacity, which grows the queue, which drops
**23.7 % of user requests**. A clean grid outage with a working genset drops none. Racks are either
**interactive** (power follows arriving traffic; lost capacity costs requests) or **batch** (training
work; lost capacity is deferred, not failed) — the default build runs two of each.

sim-1 shares no code with sim-0. `Rack` and the workload model are deliberately re-derived, not copied.

**Reference build** (`design_margins()` computes this from the config, so it cannot drift):

| | |
|---|---|
| IT | 4 × GB300 NVL72 — 540 kW nominal, 620 kW peak |
| Cooling electrical | ~119 kW at nominal (chiller at COP 6, CRAH at COP 3, 20 kW of pumps) |
| Facility total | ~688 kW → **PUE 1.27** |
| Per side | 750 kW UPS, 60 kWh battery, 1000 kW transformer, 800 kW PDU |
| Generator | one 800 kW unit, 30 s start, 4000 L tank |
| One side at site nominal | **88 % of UPS nameplate** — survivable |
| One side at site peak | **100.5 %** — an overload |
| User traffic | 76 req/s per interactive rack at the daily peak, 420 tokens each, 8 s latency budget |
| Rack throughput | 40 000 tokens/s at peak power, so nominal power ≈ **85 % of the throughput ceiling** |

That last pair is the point: the build sits right at the edge, so losing a side is survivable at
nominal and an overload at peak. Each side's battery holds about five minutes at full *site* load, so
healthy 2N gives ten minutes of autonomy and a lost side gives five.

The same holds on the compute side, and for the same reason: peak power and peak throughput are the
same point, so a rack at its nominal draw is already at ~85 % of what it can serve. Sizing traffic to
land near nominal at the daily peak leaves roughly 20 % of throughput in reserve — which the
minute-to-minute burstiness eats into before any surge arrives.

Scenarios (`--scenario`):

| Scenario | What it shows | Users hit | Incident at | Run |
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

**Every incident happens 5–15 minutes into the run, and no run is longer than three hours.** The trick
is that a scenario's clock need not start at midnight: `start_hour` sets the wall-clock time `t=0`
corresponds to, and the incident scenarios start at **13:00** — so a failure fifteen minutes in lands
at 13:15, right on the daily traffic peak. A transformer trip at 03:00 tells you nothing about whether
one side can carry the site; waiting thirteen hours to reach 14:00 tells you nothing either. The offset
applies to traffic, rack profiles and the electricity tariff together, so they cannot disagree about
what time it is.

Because the whole run now sits at representative load instead of averaging a quiet night into every
KPI, the drop percentages below are higher than the longer runs used to report.

```bash
uv run app sim1-run --scenario cooling_failure --open          # KPIs + all artifacts
uv run app sim1-run --scenario normal --no-analysis --no-csv   # just the HTML page, fast
uv run app sim1-video --scenario cooling_failure               # the same page, recorded as MP4
```

`sim1-run` writes to `out/sim1/<scenario>/` (gitignored): `index.html` (the visualization),
`analysis.html` (Plotly figures), `facility.csv`, `racks.csv`, `events.csv`, `kpis.json`.
Add `--duration` / `--dt` / `--dt-fine` / `--seed` to override the scenario's own defaults, and
`--no-viz` / `--no-analysis` / `--no-csv` to skip the slow outputs.

#### Recording a run as video

`sim1-video` plays a run back into an MP4 (`out/sim1/<scenario>/<scenario>.mp4`, 90 s of 1080p by
default). It drives the same `index.html` in a headless Chromium and pipes each frame straight into
ffmpeg, so the clip cannot show anything the interactive page would not — there is no second
renderer to keep in sync. Playback steps through the payload by *index*, not by wall-clock time,
which is what gives an incident lasting seconds of a three-hour run its own seconds of video.

The frame is the KPI header plus the flow diagram; `--timeline` adds the ribbon strip back at the
cost of a third of the diagram's height. A copyright line runs diagonally across the lower right of
every frame, overlapping the flows there so it cannot be cropped or painted out without taking the
diagram with it, and the same string goes into the file's metadata. It defaults to
`© Bogdan Neterebskii`; `--watermark` / `--watermark-opacity` set the text and how loud it is.

```bash
uv run app sim1-video --scenario cooling_failure --seconds 120 --open   # slower still
uv run app sim1-video --scenario grid_outage_gen_fail --no-build      # reuse an existing index.html
just sim1-videos                                                      # every scenario
```

Frames are captured at twice the output resolution and downscaled, which costs about ten minutes
for a 90 s clip; `--scale 1` records roughly three times faster and slightly softer. `--seconds`
is the playback speed knob: the same run spread over more seconds holds each point longer.

Needs ffmpeg on `PATH` and Chromium for Playwright (`uv run playwright install chromium`, once).

#### How one tick resolves

Two kinds of quantity, behaving differently. **State** — battery charge, fuel,
temperatures, timers, the request queue — is simulated the way you would expect: each
component owns its own and advances it every tick, from its own inputs, without
touching anyone else's. That is most of the model.

**Flows** — kW right now — have no memory, so they are not integrated forward but
*solved* within the tick. Electricity has no travel time: when the racks draw 500 kW
the transformer carries 500 kW in the same instant. Passing flows along one component
per tick would model a delay that does not exist, and would leave sources and sinks
disagreeing by up to 600 kW at the moment a failure lands — the exact moment you are
watching. Solving them keeps the books exact (residual 2.3e-13 kW), which is the
project's main correctness check.

Solving them takes one question — **how many kW does each rack actually get?** — whose
answer depends on two numbers neither known when the tick starts: what the equipment
can supply *right now* (a transformer may have just tripped) and what the load wants
*right now* (a rack may be throttling). They also depend on each other.

So the tick works through them in a fixed order, four single sweeps, no iteration and
no going back:

1. **Ask the equipment what it could supply.** The question travels down each side —
   utility, transformer, ATS, UPS, PDU — each applying its own limit. The tightest
   link is the answer. Nothing is committed.
2. **Work out what the load wants.** User traffic becomes tokens of work becomes a
   power ask per rack; cooling asks too, since its pumps and compressors are
   electrical load. Still nothing committed.
3. **Hand out the power.** The only step that changes state — batteries, fuel,
   timers, winding temperatures.
4. **Consume it.** Racks draw, heat is integrated, throughput is read back off the
   power actually drawn, the request queue ages.

Asking the equipment *before* demand exists is what makes 2N work. A side that failed
a millisecond ago answers "zero", so it is handed zero work, so the other side picks
up everything — in the same tick, with no code anywhere that detects a failure and
switches over. Load is divided in proportion to what each side can carry, and that one
line covers both cases: two healthy sides split evenly because their capacities match,
a dead side takes nothing because its capacity is zero.

Heat is different and *is* lagged, because heat genuinely propagates slowly — water
has to move, metal has to warm up. Every thermal coupling is integrated forward one
tick at a time by whichever component owns it.

[docs/sim_1_plan.md §2.1–2.3](docs/sim_1_plan.md) covers what is simulated versus what
is solved, walks a real tick end to end with actual numbers, and gives the measured
cost of the lag-everything alternative.

Only one quantity is lagged: the **coolant loop's stored heat**. The chiller sizes its demand from the
loop temperature as of the previous tick, which is both physically true (real capacity control has tens
of seconds of dead time) and what makes the cooling chain degrade smoothly instead of clipping —
capacity exceeded raises the loop temperature, which shrinks each rack's ΔT, which shrinks the heat it
can shed, which raises rack temperature, which throttles. There is no `min()` in that chain.

Two consequences worth knowing before reading a run:

- **dt is not uniform.** The engine refines the tick inside windows around scheduled events and lands
  ticks exactly on event times, so a 30 s crank is visible in a run that is otherwise coarse. Every
  aggregate is therefore `Σ p·dt`; anything that counts rows is wrong.
- **Cooling outranks IT.** In a deep brownout the pumps keep turning while IT falls to zero. Losing the
  chillers cooks the hall; losing GPU-seconds does not.
- **A saturated queue reads as empty.** Once requests are past the latency budget they are abandoned,
  not held, so the queue depth drops to zero at exactly the moment the drop rate is at its worst.
  Latency pins at the budget in the same situation — the drop rate is the number to read there.

#### The visualization

`index.html` is one file — data embedded as JSON, hand-written vanilla JS and SVG, no CDN, no external
asset of any kind. Open it by double-click, with the network off if you like.

It shows an animated flow diagram of the whole chain — **users → queue → racks** on the right, then
utility → transformer → ATS → UPS + battery → PDU → busbar → racks on the left, plus generator and
fuel tank, plus rack heat → CDU → loop → chiller → rejected, and the air branch through the room and
CRAHs. Edge thickness and dash speed are set by the actual flow on that edge (kW on the power side,
requests/s on the user side) and dead edges are greyed out. Each rack is labelled with which side of
the workload it serves, and the USERS / QUEUE / DROPPED boxes carry offered rate, serving capacity,
queue depth, wait against the budget, and the share being abandoned. A scrubber and 1× / 60× / 600× / 3600× playback move
through the run; state ribbons under the timeline make a failure narrative readable at a glance; and
fifteen stacked charts share a cursor.

Time reads as **wall clock plus elapsed** (`13:15:00 · +15:00`), and a readout beside it counts down to
the next incident and up from the last one — `T−15:00 → Chiller tripped at 13:15:00`, then
`T+06:00 since Chiller tripped at 13:15:00`. Every scheduled incident is a dashed line on every chart
and a clickable timeline marker labelled with its wall-clock time; the one the countdown is tracking is
highlighted.

A corner readout prints `in − out` every tick. It stays at zero because the model conserves energy —
`max_balance_residual_kw` in the KPIs is the same check as a number, and `tests/simulations/sim1/test_facility.py`
asserts it per-row, including the heat stored in the loop, the room and the racks themselves.

A one-week run is ~10,000 ticks and lands in about 700 KB: column-oriented arrays, ~35 primitive series
with the rest derived in the browser, per-series integer quantization, and downsampling that keeps event
windows at fine resolution and ships companion max/min series wherever the peak is the point. Without
that last rule a 60-second 105 % UPS overload would vanish into a five-minute bucket — which is exactly
the finding `side_a_lost_at_peak` exists to show.

The first four of the fourteen charts are the demand side: offered vs served vs dropped requests
against serving capacity, compute utilisation against 100 %, queue depth, and queue latency against
the budget. `analysis.html` leads with the same two rows.

### Terminology

Field/unit names follow standard data center and GPU industry usage, not invented shorthand:

| Term | Meaning |
|---|---|
| kW / kWh | Real power / energy — SI units used throughout (no kVA/power-factor modeling) |
| °C | Temperature — Celsius, standard in DC thermal specs (ASHRAE) |
| `nominal_kw` | TDP (Thermal Design Power) — sustained rated draw |
| `peak_kw` | EDPp (Electrical Design Power, peak) — burst draw a circuit must be provisioned for |
| `throttle_temp_c` | GPU junction throttle threshold (Tj) — vendor-published thermal limit before throttling |
| `liquid_capture_rate` | Liquid capture rate — fraction of rack heat removed by direct liquid cooling (DLC) vs air, a standard DLC data center metric |
| `consumed_kw` | The rack's total electrical draw from the PDU (what `peak_kw`/EDPp actually limits) — fully becomes heat regardless of conversion loss |
| `psu_efficiency` / `it_kw` / `loss_kw` | PSU/VRM efficiency splits that same draw into useful IT load (`it_kw`) vs conversion loss (`loss_kw`), for reporting only — both still count as heat |
| `delivery_efficiency_pct` | `it_kw ÷ consumed_kw` — a partial, rack-level PUE-style figure (PSU/VRM loss only, not full facility PUE) |
| `uptime_pct` | Availability — DC industry usually expresses this in "nines" (99.9%, 99.99%) or Uptime Institute Tier ratings; we report a raw percentage |
| PUE | Power Usage Effectiveness (Total Facility Power ÷ IT Power) — the standard DC efficiency KPI. In sim-0 only `delivery_efficiency_pct` is available, covering the rack's own PSU/VRM loss but not cooling's electrical draw or upstream UPS/transformer loss. **sim-1 computes real PUE**, because cooling draws from the same PDUs |

sim-1 adds:

| Term | Meaning |
|---|---|
| 2N | Full redundancy — two independent supply paths, each able to carry the whole load alone (vs N+1, one spare unit) |
| ATS | Automatic Transfer Switch — selects utility or generator; its transfer time is *why* the UPS exists |
| UPS bypass | Static bypass — mains passed straight through when the UPS cannot support the load. Keeps the load up, drops battery protection |
| SoC | State of Charge — battery energy remaining as a fraction of capacity |
| COP | Coefficient of Performance — heat removed per unit of electricity a chiller or CRAH consumes |
| CDU | Coolant Distribution Unit — the pumps and heat exchanger between the racks' cold plates and the facility loop |
| CRAH | Computer Room Air Handler — the air-side channel, handling the ~10 % of rack heat the cold plates do not take |
| `ua_kw_per_c` | Thermal conductance (UA) between a body and its cooling sink, in kW per °C of ΔT |
| `served_pct` | IT energy delivered ÷ IT energy the workload asked for — catches graceful degradation that binary uptime misses |
| `autonomy_s` | Seconds a battery string could carry its current load, reported every tick whether discharging or not |
| rps | Requests per second offered by users — the exogenous driver of everything else |
| tokens/s | Unit of served work. A request is `tokens_per_request` of it; a rack's throughput is linear in draw above its idle floor |
| SLO / latency budget | `slo_latency_s` — how long a user waits before abandoning. Requests still queued past it are dropped, which is what turns a capacity shortfall into a number rather than ever-growing latency |
| `served_pct` vs `request_drop_pct` | Energy served vs requests served. They differ: curtailment can cost kWh while the queue absorbs it, dropping nothing |
| Interactive / batch | Whether a rack's demand comes from user traffic or from its own schedule. Losing capacity fails requests on the first and defers work on the second |

## Development

There is a [justfile](justfile) with the handful of commands worth shortcutting — `just` on its own
lists them:

```bash
just install        # uv sync + pre-commit install
just test           # pytest; extra arguments are forwarded
just sim1-all       # every sim-1 scenario at its own timescale, all artifacts
just sim0-all       # every sim-0 scenario, graphs and CSVs to out/sim0/
```

Anything more specific goes through the CLI directly — see the per-simulation sections above, or
`uv run app --help`.

Or install deps and pre-commit hooks by hand:

```bash
uv sync
uv run pre-commit install
```

Run tests:

```bash
uv run pytest
uv run pytest --cov=app --cov-report=term-missing
```

Lint and format:

```bash
uv run ruff check .
uv run ruff format .
```

Type check:

```bash
uv run pyright
```
