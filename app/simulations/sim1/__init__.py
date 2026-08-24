"""sim1 — a 2N data-centre model with a closed cooling loop.

Independent of sim0: nothing here imports from ``app.simulations.sim0``. Where a
concept exists in both (racks, workload profiles) sim1 re-derives it rather than
sharing code, so the two simulations can evolve without coupling.
"""
