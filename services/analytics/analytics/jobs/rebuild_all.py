from ._bootstrap import gated, import_phase0_examples


def main() -> None:
    print("Phase 0 import rebuild:")
    print(import_phase0_examples())
    for phase, job in [
        (0, "rebuild_gps_visits"),
        (0, "reconcile_shipments"),
        (1, "calculate_tag_intelligence"),
        (2, "calculate_time_profiles"),
        (3, "calculate_pairing"),
        (3, "calculate_edges"),
        (4, "calculate_route_patterns"),
        (5, "calculate_clusters"),
    ]:
        gated(phase, job)


if __name__ == "__main__":
    main()
