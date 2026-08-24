"""The whole site, and the four-phase tick that resolves it.

Two obvious tick designs were rejected before this one.

*One-tick lag everywhere*, as sim0 does: at dt=60 the lag **is** 60 seconds, so a
grid loss would be invisible to the UPS for a full minute and a 30 second
generator start could not resolve at all. sim0 gets away with it because it has
exactly one lagged edge and no fast dynamics.

*Fixed-point iteration inside the tick*: buys accuracy a teaching model does not
need, and introduces convergence failure as a new failure mode indistinguishable
from a modelled one.

What is here instead is four named phases, each a single pass, with capacity
travelling down before demand travels up. The non-mutating ``probe`` phase is
the piece sim0 lacks and the piece that makes 2N work.
"""

from dataclasses import dataclass, field

from app.simulations.sim1.cooling.plant import CoolingPlant, CoolingSupply
from app.simulations.sim1.economics import Economics
from app.simulations.sim1.electrical.feed import Feed, FeedDelivery, split_2n
from app.simulations.sim1.electrical.generator import DieselGenerator, GenBus
from app.simulations.sim1.electrical.grid import GridState
from app.simulations.sim1.events import EventSchedule, ScheduledEvent
from app.simulations.sim1.protocols import TickContext
from app.simulations.sim1.rack import Rack, RackState
from app.simulations.sim1.units import finite_or_none, kwh, safe_ratio


@dataclass
class Facility:
    side_a: Feed
    side_b: Feed
    genset: DieselGenerator
    cooling: CoolingPlant
    racks: list[Rack]
    economics: Economics
    schedule: EventSchedule = field(default_factory=EventSchedule)

    cord_limit_kw: float = 900.0
    """Ceiling on what one side may deliver to the load bus. In a real 2N build
    each cord carries the whole site, so this only binds when someone has cheaped
    out — which is exactly what the ``undersized_cords`` scenario models."""

    fired_events: list[ScheduledEvent] = field(default_factory=list, init=False)

    @property
    def sides(self) -> tuple[Feed, Feed]:
        return self.side_a, self.side_b

    def tick(self, ctx: TickContext) -> dict[str, float | str | None]:
        # ---- phase 0: exogenous events ---------------------------------
        for event in self.schedule.due(ctx.t):
            self._apply_event(event)
            self.fired_events.append(event)

        # ---- phase 1: capacity DOWN, non-mutating ----------------------
        gen_capacity_kw = self.genset.probe(ctx)
        cap_a = self.side_a.probe(ctx, generator_kw=gen_capacity_kw)
        cap_b = self.side_b.probe(ctx, generator_kw=gen_capacity_kw)

        # ---- phase 1.5: commands ---------------------------------------
        # The genset has to be told to start before it is asked to produce, and
        # the only honest moment to notice "both mains are gone" is after the
        # capacity probe.
        self.genset.command(start=self.side_a.mains_lost and self.side_b.mains_lost)

        # ---- phase 2: demand UP, non-mutating --------------------------
        it_demand_kw = [rack.request(ctx) for rack in self.racks]
        want_it_kw = sum(it_demand_kw)
        want_mech_kw = self.cooling.request(ctx)
        demand_kw = want_it_kw + want_mech_kw

        want_a, want_b = split_2n(cap_a, cap_b, demand_kw, self.cord_limit_kw)
        self.genset.reset_asks()
        ask_a = self.side_a.request(ctx, want_a)
        ask_b = self.side_b.request(ctx, want_b)
        for side, ask in ((self.side_a, ask_a), (self.side_b, ask_b)):
            if side.on_generator:
                self.genset.register_ask(ask.total_kw)

        # ---- phase 3: power DOWN, mutating ----------------------------
        gen_bus = self.genset.deliver(ctx)
        del_a = self.side_a.deliver(ctx, gen_bus)
        del_b = self.side_b.deliver(ctx, gen_bus)
        granted_kw = del_a.delivered_kw + del_b.delivered_kw

        # Mechanical load outranks IT. Losing the chillers cooks the site;
        # losing GPU-seconds does not. This is a POLICY choice, and it is why a
        # deep brownout can show IT at zero while the pumps still turn.
        granted_mech_kw = min(want_mech_kw, granted_kw)
        granted_it_kw = granted_kw - granted_mech_kw

        # ---- phase 4: consume, integrate, close the lag ---------------
        supply = self.cooling.deliver(ctx, electrical_kw=granted_mech_kw)
        scale = 1.0 if want_it_kw <= 0.0 else granted_it_kw / want_it_kw

        it_drawn_kw = 0.0
        liquid_heat_kw = 0.0
        air_heat_kw = 0.0
        for rack, want in zip(self.racks, it_demand_kw, strict=True):
            rack.apply(
                ctx,
                granted_kw=want * scale,
                sink_c=supply.sink_c(rack.liquid_capture_rate),
                ua_scale=supply.ua_scale,
            )
            it_drawn_kw += rack.drawn_kw
            liquid_heat_kw += rack.liquid_kw
            air_heat_kw += rack.air_kw

        self.cooling.observe(ctx, liquid_heat_kw=liquid_heat_kw, air_heat_kw=air_heat_kw)

        # The uncapped workload ask, which is the only honest denominator for
        # "what did we fail to serve": `it_demand_kw` above is already clipped by
        # throttling and shutdown, so measuring against it would score a
        # thermally dead hall as fully served.
        it_want_kw = sum(rack.demand_kw for rack in self.racks)

        return self._row(ctx, del_a, del_b, gen_bus, supply, it_want_kw, want_it_kw, want_mech_kw, it_drawn_kw)

    # ------------------------------------------------------------------
    def _apply_event(self, event: ScheduledEvent) -> None:
        if event.target in ("grid_a", "grid_b"):
            grid = (self.side_a if event.target == "grid_a" else self.side_b).grid
            grid.state = {
                "offline": GridState.OFFLINE,
                "brownout": GridState.BROWNOUT,
                "online": GridState.ONLINE,
                "restore": GridState.ONLINE,
            }[event.action]
        elif event.target in ("tx_a", "tx_b"):
            (self.side_a if event.target == "tx_a" else self.side_b).transformer.trip()
        elif event.target == "chiller":
            if event.action == "restore":
                self.cooling.chiller.restore()
            else:
                self.cooling.chiller.fault()
        elif event.target == "crah":
            self.cooling.crah.fault()
        elif event.target == "cdu":
            self.cooling.cdu.fault()
        elif event.target == "marker":
            # A labelled point in time with no side effect. Gives the engine a
            # dt-refinement anchor and the visualization a timeline annotation
            # for changes that live in the workload rather than in a component.
            pass
        else:  # pragma: no cover - guarded by scenario definitions
            raise ValueError(f"unknown event target: {event.target}")

    def _row(
        self,
        ctx: TickContext,
        del_a: FeedDelivery,
        del_b: FeedDelivery,
        gen_bus: GenBus,
        supply: CoolingSupply,
        it_want_kw: float,
        it_request_kw: float,
        want_mech_kw: float,
        it_drawn_kw: float,
    ) -> dict[str, float | str | None]:
        loss_kw = del_a.loss_kw + del_b.loss_kw
        mech_kw = supply.mech_kw
        facility_kw = it_drawn_kw + mech_kw + loss_kw
        sources_kw = del_a.source_kw + del_b.source_kw
        charge_kw = del_a.battery_charge_kw + del_b.battery_charge_kw
        grid_kw = del_a.grid_kw + del_b.grid_kw
        unserved_kw = max(0.0, it_want_kw - it_drawn_kw)

        temps = [rack.temp_c for rack in self.racks]
        down = sum(1 for rack in self.racks if rack.state is RackState.EMERGENCY_SHUTDOWN)
        throttling = sum(1 for rack in self.racks if rack.state is RackState.THROTTLING)
        # "Down" is measured by what the racks actually draw, not by their state
        # label. A blackout leaves every rack cool and nominally IDLE, so a
        # state-based definition would report a dark hall as fully available.
        productive = sum(1 for rack in self.racks if rack.drawn_kw > 0.01 * rack.nominal_kw)
        site_down = productive == 0

        row: dict[str, float | str | None] = {
            "t": ctx.t,
            "dt": ctx.dt,
            "tick": float(ctx.tick),
            "it_demand_kw": it_want_kw,
            "it_request_kw": it_request_kw,
            "it_drawn_kw": it_drawn_kw,
            "it_unserved_kw": unserved_kw,
            "mech_demand_kw": want_mech_kw,
            "mech_kw": mech_kw,
            "loss_kw": loss_kw,
            "facility_kw": facility_kw,
            "sources_kw": sources_kw,
            "grid_kw": grid_kw,
            "charge_kw": charge_kw,
            # Visible proof of conservation: this is zero for a correct tick, and
            # the visualization prints it in the corner rather than hiding it.
            "balance_residual_kw": sources_kw - (facility_kw + charge_kw),
            "gen_bus_ask_kw": gen_bus.total_ask_kw,
            "racks_down": float(down),
            "racks_throttling": float(throttling),
            "racks_productive": float(productive),
            "site_down": 1.0 if site_down else 0.0,
            "rack_temp_max_c": max(temps),
            "rack_temp_mean_c": sum(temps) / len(temps),
            "pue": finite_or_none(safe_ratio(facility_kw, it_drawn_kw, default=float("inf"))),
        }

        for prefix, side, delivery in (("a", self.side_a, del_a), ("b", self.side_b, del_b)):
            for key, value in side.telemetry().items():
                row[f"{prefix}_{key}"] = value
            row[f"{prefix}_delivered_kw"] = delivery.delivered_kw
            row[f"{prefix}_grid_kw"] = delivery.grid_kw
            row[f"{prefix}_generator_kw"] = delivery.generator_kw
            row[f"{prefix}_batt_out_kw"] = delivery.battery_out_kw
            row[f"{prefix}_batt_charge_kw"] = delivery.battery_charge_kw
            row[f"{prefix}_loss_kw"] = delivery.loss_kw
            row[f"{prefix}_autonomy_s"] = finite_or_none(side.ups.battery.autonomy_s(side.ups.output_kw))

        for key, value in self.genset.telemetry().items():
            row[f"gen_{key}"] = value
        for key, value in self.cooling.telemetry().items():
            row[f"cool_{key}"] = value

        energy_usd = self.economics.energy_usd(ctx.t, kwh(grid_kw, ctx.dt))
        diesel_l = self.genset.fuel_rate_l_per_h * ctx.dt / 3600.0
        row["energy_cost_usd"] = energy_usd
        row["diesel_l"] = diesel_l
        row["diesel_cost_usd"] = self.economics.diesel_usd(diesel_l)
        row["unserved_cost_usd"] = self.economics.unserved_usd(kwh(unserved_kw, ctx.dt))
        row["downtime_cost_usd"] = self.economics.downtime_usd(ctx.dt if site_down else 0.0)
        return row
