# Future VRP Integration

Do not implement route optimization in Phase 0-6.

Future OR-Tools inputs from this platform:

- Hard constraints: depot, vehicle class, capacity, compartments, product compatibility, official time windows, approved tag restrictions.
- Soft preferences: observed MT-SPBU compatibility, preferred arrival windows, pairing probability, cluster membership, route patterns, time-dependent travel profiles.
- Initial solutions: high-confidence recurring route patterns.
- Travel matrix: derived from `fact_spbu_edge` and `fact_spbu_edge_time_profile` in later phases.

The VRP layer should consume published canonical and derived fact tables. It should not parse raw uploads directly.
