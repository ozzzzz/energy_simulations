"""The contract every sim1 component obeys.

sim0 collapsed the whole electrical chain into a single replaceable
``step(t, dt, requested_kw) -> delivered_kw``. That works for one opaque signal
but not for a chain: capacity has to travel *down* from the grid and demand has
to travel *up* from the racks before anything can be committed. So the single
method is split into its three real jobs — ``probe`` (capacity down),
``request`` (demand up), ``deliver`` (power down, and the only mutation).

The payoff is that a side which died on this very tick reports zero capacity in
``probe``, therefore receives zero demand in ``request``, therefore fails over
inside the same tick — with no failover branch anywhere in the code.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TickContext:
    """Everything a component may know about the current tick."""

    t: float
    """Simulated seconds since the start of the run."""

    dt: float
    """Length of *this* tick. Varies across a run — see ``engine`` refinement."""

    tick: int
    """Monotonic tick index. Useful for row ids; never for integration."""


@dataclass(frozen=True, slots=True)
class Delivery:
    """What one component did with power over one tick.

    The five terms exist so that a single identity covers passives, sources and
    storage alike::

        drawn_kw + injected_kw == delivered_kw + loss_kw + stored_kw

    ``loss_kw`` is electrical loss that became heat inside the component.
    ``injected_kw`` is power created here rather than passed through (battery
    discharge onto the DC bus, alternator output). ``stored_kw`` is power that
    left the electrical bus into storage. Losses *internal* to a battery sit
    outside this identity, because the stored energy is outside it too.
    """

    delivered_kw: float = 0.0
    drawn_kw: float = 0.0
    loss_kw: float = 0.0
    injected_kw: float = 0.0
    stored_kw: float = 0.0

    @property
    def residual_kw(self) -> float:
        """Signed violation of the balance identity. Zero for a correct component."""
        return (self.drawn_kw + self.injected_kw) - (self.delivered_kw + self.loss_kw + self.stored_kw)

    @classmethod
    def passive(cls, drawn_kw: float, delivered_kw: float) -> "Delivery":
        """A pass-through component: everything not delivered was lost as heat."""
        return cls(delivered_kw=delivered_kw, drawn_kw=drawn_kw, loss_kw=drawn_kw - delivered_kw)


@dataclass(frozen=True, slots=True)
class LoadResult:
    """What one load did with the power it was granted."""

    drawn_kw: float
    """Electrical draw. For a rack this equals the grant, exactly, by construction."""

    heat_kw: float
    """Heat generated. For IT equipment this equals ``drawn_kw``."""

    removed_kw: float
    """Average heat actually shed to the cooling sink over the tick."""

    temp_c: float
    """Temperature at the *end* of the tick."""

    state: str


@runtime_checkable
class PowerComponent(Protocol):
    """One link in the chain from the grid to the load."""

    name: str
    kind: str

    def probe(self, ctx: TickContext, upstream_kw: float) -> float:
        """Phase 1. Given what upstream could supply, what could I pass down?

        Applies rating, efficiency and availability. **Must not mutate**
        integrating state — this is a question, not an action, and it is asked
        before demand is known.
        """
        ...

    def request(self, ctx: TickContext, demand_kw: float) -> float:
        """Phase 2. Given downstream demand, what do I need from upstream?

        Grosses the demand up by my own losses and parasitics. **Must not
        mutate** integrating state.
        """
        ...

    def deliver(self, ctx: TickContext, supply_kw: float, demand_kw: float) -> Delivery:
        """Phase 3. Upstream granted ``supply_kw`` against ``demand_kw``.

        Passes power down, integrates state (charge, fuel, timers, winding
        temperature) and latches transitions. Called exactly once per tick.

        Both quantities are passed explicitly rather than stashed during
        ``request``: the *gap* between them is precisely what a battery has to
        cover, and passing them makes every component testable with three
        scalars and no phase-ordering ritual.
        """
        ...

    def telemetry(self) -> dict[str, float | str]:
        """Flat, JSON-safe snapshot. Keys are unprefixed; owners add the prefix."""
        ...


@runtime_checkable
class Load(Protocol):
    """Something that consumes power and produces heat."""

    name: str

    def request(self, ctx: TickContext) -> float:
        """Phase 2. Power wanted this tick, already capped by my own state."""
        ...

    def apply(self, ctx: TickContext, granted_kw: float, sink_c: float, ua_scale: float = 1.0) -> LoadResult:
        """Phase 4. Draw exactly ``granted_kw`` and integrate the physics.

        ``ua_scale`` is the fraction of design coolant flow available. Scaling
        the conductance rather than clipping a heat rate keeps the temperature
        solve in closed form: reduced flow means a worse ΔT, not a capped kW.
        """
        ...
