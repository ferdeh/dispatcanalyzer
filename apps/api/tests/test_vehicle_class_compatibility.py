from app.compatibility import evaluate_compatibility_entities
from app.config import Settings
from app.models import MasterMT, MasterSPBU


def _compatible(mt_capacity: int, spbu_limit: int) -> bool:
    mt = MasterMT(
        mt_id=f"MT-{mt_capacity}",
        vehicle_name_raw=f"MT {mt_capacity} KL",
        vehicle_type_tag=mt_capacity,
        depot_id="DEPOT-1",
    )
    spbu = MasterSPBU(
        spbu_id=f"SPBU-{spbu_limit}",
        spbu_code=f"SPBU-{spbu_limit}",
        vehicle_type_tag=spbu_limit,
        primary_depot_id="DEPOT-1",
    )
    return evaluate_compatibility_entities(mt, spbu)["compatible"]


def test_default_vehicle_compatibility_uses_spbu_class_as_capacity_limit() -> None:
    assert Settings(_env_file=None).vehicle_compatibility_mode == "MT_CAPACITY_LE_SPBU_LIMIT"


def test_spbu_32_accepts_32_24_16_and_8_kl_mt() -> None:
    assert all(_compatible(capacity, 32) for capacity in (32, 24, 16, 8))


def test_spbu_24_accepts_smaller_mt_and_rejects_32_kl_mt() -> None:
    assert all(_compatible(capacity, 24) for capacity in (24, 16, 8))
    assert not _compatible(32, 24)


def test_spbu_8_rejects_every_larger_mt() -> None:
    assert _compatible(8, 8)
    assert all(not _compatible(capacity, 8) for capacity in (16, 24, 32))
