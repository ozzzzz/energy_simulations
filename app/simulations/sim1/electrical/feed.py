"""One side of the 2N electrical chain, and the rule that shares load between sides."""

from dataclasses import dataclass, field

from app.simulations.sim1.electrical.ats import AtsSource, AutomaticTransferSwitch
from app.simulations.sim1.electrical.generator import GenBus
from app.simulations.sim1.electrical.grid import GridFeed
from app.simulations.sim1.electrical.pdu import Pdu
from app.simulations.sim1.electrical.transformer import Transformer
from app.simulations.sim1.electrical.ups import Ups
from app.simulations.sim1.models import TickContext


def split_2n(cap_a: float, cap_b: float, demand_kw: float, cord_limit_kw: float) -> tuple[float, float]:
    """Split demand across the two feeds in proportion to their probed capacity.

    There is deliberately no failover branch. Two identical healthy sides split
    50/50 because their capacities are equal; a dead side takes nothing because
    its capacity is zero. Failover is the general case evaluated at a boundary,
    not a special case — which is also why it costs no extra tick and needs no
    detection logic.
    """
    total = cap_a + cap_b
    if total <= 0.0 or demand_kw <= 0.0:
        return 0.0, 0.0

    a = demand_kw * cap_a / total
    b = demand_kw - a

    # In a proper 2N build each cord is rated for the whole rack, so this only
    # binds in the `undersized_cords` scenario. One redistribution pass is
    # provably enough: with two sides, clamping one can only push work onto the
    # other.
    a_max = min(cap_a, cord_limit_kw)
    b_max = min(cap_b, cord_limit_kw)
    if a > a_max:
        a, b = a_max, min(b + (a - a_max), b_max)
    elif b > b_max:
        b, a = b_max, min(a + (b - b_max), a_max)
    return a, b


@dataclass(frozen=True, slots=True)
class FeedAsk:
    """What one side needs from upstream, split by priority.

    Critical load and battery recharge are asked for separately so that the
    generator can satisfy the load first and charge the batteries with whatever
    is left, instead of curtailing IT to top up a battery.
    """

    critical_kw: float
    optional_kw: float

    @property
    def total_kw(self) -> float:
        return self.critical_kw + self.optional_kw


@dataclass(frozen=True, slots=True)
class FeedDelivery:
    delivered_kw: float
    grid_kw: float
    generator_kw: float
    battery_out_kw: float
    battery_charge_kw: float
    transformer_loss_kw: float
    ups_loss_kw: float
    pdu_loss_kw: float

    @property
    def loss_kw(self) -> float:
        return self.transformer_loss_kw + self.ups_loss_kw + self.pdu_loss_kw

    @property
    def source_kw(self) -> float:
        """Everything that entered this side from outside its own boundary."""
        return self.grid_kw + self.generator_kw + self.battery_out_kw

    @property
    def residual_kw(self) -> float:
        return self.source_kw - (self.delivered_kw + self.loss_kw + self.battery_charge_kw)


@dataclass
class Feed:
    """Grid -> transformer -> ATS -> UPS(+battery) -> PDU, for one side."""

    side: str
    grid: GridFeed
    transformer: Transformer
    ats: AutomaticTransferSwitch
    ups: Ups
    pdu: Pdu

    _pdu_demand_kw: float = field(default=0.0, init=False, repr=False)
    _ups_demand_kw: float = field(default=0.0, init=False, repr=False)
    _ask: FeedAsk = field(default=FeedAsk(0.0, 0.0), init=False, repr=False)

    @property
    def mains_lost(self) -> bool:
        return self.grid.available_kw <= 0.0 or self.transformer.failed

    @property
    def on_generator(self) -> bool:
        return self.ats.on_generator

    def probe(self, ctx: TickContext, generator_kw: float) -> float:
        grid_cap = self.grid.probe(ctx)
        tx_cap = self.transformer.probe(ctx, grid_cap)
        ats_cap = self.ats.probe(ctx, primary_kw=tx_cap, generator_kw=generator_kw)
        ups_cap = self.ups.probe(ctx, ats_cap)
        return self.pdu.probe(ctx, ups_cap)

    def request(self, ctx: TickContext, demand_kw: float) -> FeedAsk:
        self._pdu_demand_kw = demand_kw
        self._ups_demand_kw = self.pdu.request(ctx, demand_kw)
        # Stops at the UPS input, i.e. the transformer's *output* requirement.
        # Transformer loss is added by `Transformer.deliver` itself, and is
        # absent entirely when this side is running off the generator.
        critical = self.ups.request(ctx, self._ups_demand_kw)
        optional = self.ups.charge_request(ctx, self._ups_demand_kw)
        self._ask = FeedAsk(critical_kw=critical, optional_kw=optional)
        return self._ask

    def deliver(self, ctx: TickContext, gen_bus: GenBus) -> FeedDelivery:
        source = self.ats.step(
            ctx,
            primary_available=self.transformer.probe(ctx, self.grid.available_kw) > 0.0,
            generator_running=gen_bus.running,
        )

        want_kw = self._ask.total_kw
        grid_kw = 0.0
        generator_kw = 0.0
        transformer_loss_kw = 0.0
        supplied_kw = 0.0

        if source is AtsSource.PRIMARY:
            tx_out = self.transformer.deliver(ctx, supply_kw=self.grid.available_kw, demand_kw=want_kw)
            supplied_kw = tx_out.delivered_kw
            transformer_loss_kw = tx_out.loss_kw
            grid_kw = self.grid.deliver(ctx, supply_kw=0.0, demand_kw=tx_out.drawn_kw).delivered_kw
        elif source is AtsSource.GENERATOR:
            supplied_kw = gen_bus.allocate(want_kw)
            generator_kw = supplied_kw
            self.transformer.idle(ctx)
        else:
            # Mid-transfer: the switch feeds nothing at all. This is the gap the
            # UPS exists to cover.
            self.transformer.idle(ctx)

        ups_out = self.ups.deliver(
            ctx,
            supply_kw=supplied_kw,
            demand_kw=self._ups_demand_kw,
            charge_kw=self._ask.optional_kw,
        )
        pdu_out = self.pdu.deliver(ctx, supply_kw=ups_out.delivered_kw, demand_kw=self._pdu_demand_kw)

        return FeedDelivery(
            delivered_kw=pdu_out.delivered_kw,
            grid_kw=grid_kw,
            generator_kw=generator_kw,
            battery_out_kw=ups_out.injected_kw,
            battery_charge_kw=ups_out.stored_kw,
            transformer_loss_kw=transformer_loss_kw,
            ups_loss_kw=ups_out.loss_kw,
            pdu_loss_kw=pdu_out.loss_kw,
        )

    def telemetry(self) -> dict[str, float | str]:
        rows: dict[str, float | str] = {}
        for prefix, component in (
            ("grid", self.grid),
            ("tx", self.transformer),
            ("ats", self.ats),
            ("ups", self.ups),
            ("pdu", self.pdu),
        ):
            for key, value in component.telemetry().items():
                rows[f"{prefix}_{key}"] = value
        for key, value in self.ups.battery.telemetry().items():
            rows[f"batt_{key}"] = value
        return rows
