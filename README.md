# Energy Simulations

Simulations of AI data center energy systems (SimPy for the clock), with a CLI to run scenarios and
two viewers: a Dash/Plotly app for sim-0 and a single self-contained HTML page for sim-1.

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

uv run app sim0-run --scenario inference --output /tmp/inference.csv
uv run app sim0-run --scenario training --output /tmp/training.csv
uv run app sim0-run --scenario mixed --output /tmp/mixed.csv
uv run app sim0-run --scenario cooling_failure --output /tmp/cooling_failure.csv

uv run app sim0-dashboard --scenario inference
uv run app sim0-dashboard --scenario training
uv run app sim0-dashboard --scenario mixed
uv run app sim0-dashboard --scenario cooling_failure
```

Add `--duration <seconds>` / `--dt <seconds>` to override the default week / 1-minute tick.

The dashboard is a Sankey diagram tracing where every kW of draw actually goes — demand splits into
delivered vs curtailed, delivered draw splits into useful IT compute vs PSU/VRM loss, and all of it ends up
as heat split between liquid and air — plus a cooling-by-channel chart and a per-rack drill-down (demand vs
draw, temperature). A dropdown compares the whole run against just the cooling-incident window.

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

sim-1 shares no code with sim-0. `Rack` and the workload model are deliberately re-derived, not copied.

**Reference build** (`app sim1-list` prints this from the config, so it cannot drift):

| | |
|---|---|
| IT | 4 × GB300 NVL72 — 540 kW nominal, 620 kW peak |
| Cooling electrical | ~119 kW at nominal (chiller at COP 6, CRAH at COP 3, 20 kW of pumps) |
| Facility total | ~688 kW → **PUE 1.27** |
| Per side | 750 kW UPS, 60 kWh battery, 1000 kW transformer, 800 kW PDU |
| Generator | one 800 kW unit, 30 s start, 4000 L tank |
| One side at site nominal | **88 % of UPS nameplate** — survivable |
| One side at site peak | **100.5 %** — an overload |

That last pair is the point: the build sits right at the edge, so losing a side is survivable at
nominal and an overload at peak. Each side's battery holds about five minutes at full *site* load, so
healthy 2N gives ten minutes of autonomy and a lost side gives five.

Scenarios (`--scenario`):

| Scenario | What it shows | Default run |
|---|---|---|
| `normal` | steady state, 50/50 split, PUE ≈ 1.27, reserves untouched | 7 d @ 60 s |
| `grid_outage_gen_ok` | both feeds drop → UPSes ride it on battery → 30 s crank → ATS transfers → diesel burns → batteries recharge | 2 h @ 10 s |
| `grid_outage_gen_fail` | three failed starts → latched failure → batteries to cutoff → the site goes dark | 40 min @ 5 s |
| `load_spike` | every rack pinned at peak: cooling, not power, is the tighter margin (~42 kW of chiller headroom left) | 8 h @ 30 s |
| `cooling_failure` | chiller trips → loop climbs ~1.5 °C/min → rack ΔT collapses → throttle → shutdown → restored after 4 h | 8 h @ 30 s |
| `side_a_lost` | transformer A trips; UPS A quietly burns its battery down, then side B carries the site at 93 % | 4 h @ 10 s |
| `side_a_lost_at_peak` | same at peak: UPS B crosses 100 %, its hold timer expires, it transfers to **bypass** — load survives, battery protection is gone | 4 h @ 10 s |
| `undersized_cords` | 2N on paper only (cords rated for 60 % of peak): losing a side curtails IT to 76 % | 4 h @ 10 s |

```bash
uv run app sim1-list                                          # scenarios + design margins
uv run app sim1-run --scenario grid_outage_gen_ok --open       # KPIs + all artifacts
uv run app sim1-viz --scenario cooling_failure                 # just the HTML page, fast
uv run app sim1-compare                                        # every scenario, side by side
```

`sim1-run` writes to `out/sim1/<scenario>/` (gitignored): `index.html` (the visualization),
`analysis.html` (Plotly figures), `facility.csv`, `racks.csv`, `events.csv`, `kpis.json`.
Add `--duration` / `--dt` / `--dt-fine` / `--seed` to override the scenario's own defaults, and
`--no-viz` / `--no-analysis` / `--no-csv` to skip the slow outputs.

#### How one tick resolves

Four passes, each a single sweep — not one-tick lag everywhere (at dt=60 the lag *is* a minute, and a
30 s generator start could not resolve), and not fixed-point iteration (which adds convergence failure
as a failure mode indistinguishable from a modeled one):

1. **probe** — capacity flows *down* from the utility. Non-mutating.
2. **request** — demand flows *up* from the racks. Non-mutating.
3. **deliver** — power flows *down*; the only pass that integrates state.
4. **consume** — loads draw, physics integrates.

The probe pass is what makes 2N work: a side that died on this very tick reports zero capacity, so it
receives zero demand and fails over inside the same tick — with no failover branch anywhere in the code.
Load is split in proportion to probed capacity, which makes 50/50 sharing and 0/100 failover the same
formula evaluated at different points.

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

#### The visualization

`index.html` is one file — data embedded as JSON, hand-written vanilla JS and SVG, no CDN, no external
asset of any kind. Open it by double-click, with the network off if you like.

It shows an animated flow diagram of the whole chain (utility → transformer → ATS → UPS + battery →
PDU → busbar → racks, plus generator and fuel tank, plus rack heat → CDU → loop → chiller → rejected,
and the air branch through the room and CRAHs), with edge thickness and dash speed set by the actual
kW on that edge and dead edges greyed out. A scrubber and 1× / 60× / 600× / 3600× playback move
through the run; event markers jump to the moment they happened; state ribbons under the timeline make
a failure narrative readable at a glance; and ten stacked charts share a cursor.

A corner readout prints `in − out` every tick. It stays at zero because the model conserves energy —
`max_balance_residual_kw` in the KPIs is the same check as a number, and `tests/simulations/sim1/test_facility.py`
asserts it per-row, including the heat stored in the loop, the room and the racks themselves.

A one-week run is ~10,000 ticks and lands in about 700 KB: column-oriented arrays, ~35 primitive series
with the rest derived in the browser, per-series integer quantization, and downsampling that keeps event
windows at fine resolution and ships companion max/min series wherever the peak is the point. Without
that last rule a 60-second 105 % UPS overload would vanish into a five-minute bucket — which is exactly
the finding `side_a_lost_at_peak` exists to show.

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

## Development

There is a [justfile](justfile) wrapping the common commands — `just` on its own lists them:

```bash
just install                       # uv sync + pre-commit install
just scenarios                     # sim1 scenarios and design margins
just sim1 cooling_failure          # run one scenario, write artifacts
just viz side_a_lost_at_peak       # run one and open the visualization
just sim1-all                      # every scenario, all artifacts
just compare                       # every scenario, headline KPIs side by side
just check                         # what CI runs: lint + tests with coverage
just clean                         # delete out/
```

Every recipe forwards extra flags, so `just sim1 normal --duration 86400 --no-analysis` works.

Or install deps and pre-commit hooks directly:

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
