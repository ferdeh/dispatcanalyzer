from pathlib import Path
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.compatibility import evaluate_mt_spbu_compatibility
from app.importer import ImportProcessor
from app.models import (
    Base,
    BridgeMTTag,
    BridgeSPBUTag,
    DataQualityIssue,
    FactLoadingOrderLine,
    FactShipment,
    MasterMT,
    MasterProduct,
    MasterSPBU,
    StgLoadingOrder,
    StgMT,
    StgSPBU,
    MasterTag,
    MasterTagType,
)
from app.normalization import make_id
from app.tag_consistency import build_tag_consistency_payload

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = ROOT / "example data"


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        yield session


def test_phase0_imports_real_workbooks(db_session) -> None:
    processor = ImportProcessor(db_session)
    processor.import_master_mt(EXAMPLE_DIR / "master data MT.xlsx")
    processor.import_master_spbu(EXAMPLE_DIR / "master data spbu.xlsx")
    processor.import_loading_order(EXAMPLE_DIR / "masterdata_LO.xlsx")

    assert db_session.scalar(select(func.count()).select_from(MasterMT)) == 162
    assert db_session.scalar(select(func.count()).select_from(MasterSPBU)) == 583
    assert db_session.scalar(select(func.count()).select_from(FactLoadingOrderLine)) == 4462
    assert db_session.scalar(select(func.count()).select_from(FactLoadingOrderLine)) == db_session.scalar(
        select(func.count()).select_from(
            select(FactLoadingOrderLine.loading_order_number, FactLoadingOrderLine.source_depot_name).distinct().subquery()
        )
    )
    assert db_session.scalar(select(func.count()).select_from(FactShipment)) == 1876
    assert db_session.scalar(select(func.count()).select_from(StgMT)) == 162
    assert db_session.scalar(select(func.count()).select_from(StgSPBU)) == 583
    assert db_session.scalar(select(func.count()).select_from(StgLoadingOrder)) == 4462


def test_spbu_import_preserves_decimal_comma_coordinate(db_session, tmp_path) -> None:
    csv_path = tmp_path / "spbu_decimal_comma_coordinate.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Nama SPBU,Coordinate,Vehicle Type tag,Project tag",
                '11201199,"5,19182389869645 96,4368560343681",24,All In',
            ]
        ),
        encoding="utf-8",
    )

    processor = ImportProcessor(db_session)
    processor.import_master_spbu(csv_path, sheet_name="SPBU")

    spbu = db_session.scalar(select(MasterSPBU).where(MasterSPBU.spbu_code == "11201199"))
    assert spbu is not None
    assert spbu.source_coordinate == "5,19182389869645 96,4368560343681"
    assert spbu.latitude == 5.19182389869645
    assert spbu.longitude == 96.4368560343681


def test_loading_order_model_preserves_multi_product_shipments(db_session) -> None:
    processor = ImportProcessor(db_session)
    processor.import_master_mt(EXAMPLE_DIR / "master data MT.xlsx")
    processor.import_master_spbu(EXAMPLE_DIR / "master data spbu.xlsx")
    processor.import_loading_order(EXAMPLE_DIR / "masterdata_LO.xlsx")

    shipment = db_session.scalar(select(FactShipment).where(FactShipment.source_shipment_id == "2678638"))
    assert shipment is not None
    assert shipment.shipment_id == "2678638"
    assert shipment.source_shipment_id == "2678638"
    lines = db_session.scalars(select(FactLoadingOrderLine).where(FactLoadingOrderLine.shipment_id == shipment.shipment_id)).all()
    assert len(lines) == 2
    assert len({line.loading_order_number for line in lines}) == 2
    assert {line.source_depot_name for line in lines} == {"FUEL TERMINAL MEDAN GROUP"}
    assert {line.source_product_name for line in lines} == {"PERTALITE", "BIOSOLAR B40"}
    assert {line.shipment_id for line in lines} == {"2678638"}
    assert all(line.loading_order_number != shipment.source_shipment_id for line in lines)


def test_loading_order_number_can_repeat_across_depots(db_session, tmp_path) -> None:
    csv_path = tmp_path / "lo_duplicate_number_different_depot.csv"
    csv_path.write_text(
        "\n".join(
            [
                "shipment_id,loading_order_number,tbbm,kode_depot,nopol,nama_spbu,produk,quantity,status",
                "SHP-A,LO-001,DEPOT A,DA,B1234AA,SPBU-A,PERTALITE,1000,OPEN",
                "SHP-B,LO-001,DEPOT B,DB,B1234AA,SPBU-B,PERTALITE,2000,OPEN",
            ]
        ),
        encoding="utf-8",
    )

    processor = ImportProcessor(db_session)
    processor.import_loading_order(csv_path)

    lines = db_session.scalars(select(FactLoadingOrderLine).where(FactLoadingOrderLine.loading_order_number == "LO-001")).all()
    assert len(lines) == 2
    assert {line.source_depot_name for line in lines} == {"DEPOT A", "DEPOT B"}


def test_product_names_with_commas_are_single_products(db_session) -> None:
    processor = ImportProcessor(db_session)
    processor.import_master_mt(EXAMPLE_DIR / "master data MT.xlsx")
    processor.import_master_spbu(EXAMPLE_DIR / "master data spbu.xlsx")
    processor.import_loading_order(EXAMPLE_DIR / "masterdata_LO.xlsx")

    products = {product.product_name for product in db_session.scalars(select(MasterProduct)).all()}
    assert "PERTAMAX,BULK" in products
    assert "PERTAMAX TURBO, BULK" in products
    assert "BULK" not in products


def test_tag_links_and_aliases_are_built(db_session) -> None:
    processor = ImportProcessor(db_session)
    processor.import_master_mt(EXAMPLE_DIR / "master data MT.xlsx")
    processor.import_master_spbu(EXAMPLE_DIR / "master data spbu.xlsx")
    processor.import_loading_order(EXAMPLE_DIR / "masterdata_LO.xlsx")

    assert db_session.scalar(select(func.count()).select_from(BridgeMTTag)) > 0
    assert db_session.scalar(select(func.count()).select_from(BridgeSPBUTag)) > 0


def test_mapping_statuses_and_quality_issues_are_visible(db_session) -> None:
    processor = ImportProcessor(db_session)
    processor.import_master_mt(EXAMPLE_DIR / "master data MT.xlsx")
    processor.import_master_spbu(EXAMPLE_DIR / "master data spbu.xlsx")
    processor.import_loading_order(EXAMPLE_DIR / "masterdata_LO.xlsx")

    matched_mt = db_session.scalar(select(func.count()).select_from(FactShipment).where(FactShipment.vehicle_mapping_status == "MATCHED"))
    unmatched_mt = db_session.scalar(select(func.count()).select_from(FactShipment).where(FactShipment.vehicle_mapping_status == "UNMATCHED"))
    assert matched_mt + unmatched_mt == 1876
    assert unmatched_mt > 0
    assert db_session.scalar(select(func.count()).select_from(DataQualityIssue)) > 0


def test_compatibility_explanation_is_structured(db_session) -> None:
    processor = ImportProcessor(db_session)
    processor.import_master_mt(EXAMPLE_DIR / "master data MT.xlsx")
    processor.import_master_spbu(EXAMPLE_DIR / "master data spbu.xlsx")

    mt = db_session.scalar(select(MasterMT))
    spbu = db_session.scalar(select(MasterSPBU))
    result = evaluate_mt_spbu_compatibility(db_session, mt.mt_id, spbu.spbu_id)
    assert set(result) >= {"compatible", "vehicle_type_check", "project_tag_check", "failed_rules", "explanation"}


def test_tag_consistency_uses_vehicle_class_limit_and_tag_subset(db_session) -> None:
    project_type_id = make_id("tagtype", "PROJECT")
    vehicle_type_id = make_id("tagtype", "VEHICLE_CLASS")
    db_session.add_all(
        [
            MasterTagType(tag_type_id=project_type_id, code="PROJECT", name="Project"),
            MasterTagType(tag_type_id=vehicle_type_id, code="VEHICLE_CLASS", name="Vehicle Class"),
            MasterMT(mt_id="mt_small", vehicle_name_raw="B 9123 ABC-16KL", vehicle_registration="B9123ABC", vehicle_type_tag=16),
            MasterMT(mt_id="mt_large", vehicle_name_raw="B 9999 ABC-32KL", vehicle_registration="B9999ABC", vehicle_type_tag=32),
            MasterMT(mt_id="mt_missing_project", vehicle_name_raw="B 1111 ABC-16KL", vehicle_registration="B1111ABC", vehicle_type_tag=16),
            MasterSPBU(spbu_id="spbu_24", spbu_code="74.951.01", spbu_name="74.951.01", vehicle_type_tag=24),
        ]
    )
    tags = {
        "ALLIN": MasterTag(tag_id="tag_allin", tag_type_id=project_type_id, tag_value="ALL IN", normalized_tag="ALLIN"),
        "GUNUNG": MasterTag(tag_id="tag_gunung", tag_type_id=project_type_id, tag_value="GUNUNG", normalized_tag="GUNUNG"),
        "KOTA": MasterTag(tag_id="tag_kota", tag_type_id=project_type_id, tag_value="KOTA", normalized_tag="KOTA"),
    }
    db_session.add_all(tags.values())
    db_session.add_all(
        [
            BridgeSPBUTag(spbu_id="spbu_24", tag_id="tag_allin"),
            BridgeSPBUTag(spbu_id="spbu_24", tag_id="tag_gunung"),
            BridgeMTTag(mt_id="mt_small", tag_id="tag_allin"),
            BridgeMTTag(mt_id="mt_small", tag_id="tag_gunung"),
            BridgeMTTag(mt_id="mt_small", tag_id="tag_kota"),
            BridgeMTTag(mt_id="mt_large", tag_id="tag_allin"),
            BridgeMTTag(mt_id="mt_large", tag_id="tag_gunung"),
            BridgeMTTag(mt_id="mt_missing_project", tag_id="tag_allin"),
        ]
    )
    db_session.add_all(
        [
            FactShipment(shipment_id="shipment_match", source_shipment_id="shipment_match", operating_date=date(2026, 8, 12), mt_id="mt_small", vehicle_registration="b 9123 abc", vehicle_mapping_status="MATCHED"),
            FactShipment(shipment_id="shipment_vehicle_mismatch", source_shipment_id="shipment_vehicle_mismatch", operating_date=date(2026, 8, 12), mt_id="mt_large", vehicle_registration="B9999ABC", vehicle_mapping_status="MATCHED"),
            FactShipment(shipment_id="shipment_tag_mismatch", source_shipment_id="shipment_tag_mismatch", operating_date=date(2026, 8, 12), mt_id="mt_missing_project", vehicle_registration="B1111ABC", vehicle_mapping_status="MATCHED"),
            FactLoadingOrderLine(loading_order_number="LO-MATCH", source_depot_name="DEPOT", shipment_id="shipment_match", spbu_id="spbu_24", spbu_mapping_status="MATCHED"),
            FactLoadingOrderLine(loading_order_number="LO-VEHICLE-MISMATCH", source_depot_name="DEPOT", shipment_id="shipment_vehicle_mismatch", spbu_id="spbu_24", spbu_mapping_status="MATCHED"),
            FactLoadingOrderLine(loading_order_number="LO-TAG-MISMATCH", source_depot_name="DEPOT", shipment_id="shipment_tag_mismatch", spbu_id="spbu_24", spbu_mapping_status="MATCHED"),
        ]
    )
    db_session.commit()

    payload = build_tag_consistency_payload(db_session)
    rows = {row["loading_order_number"]: row for row in payload["rows"]}

    assert rows["LO-MATCH"]["overall_status"] == "MATCH"
    assert rows["LO-MATCH"]["details"][0]["reason"] == "16 <= 24."
    assert rows["LO-VEHICLE-MISMATCH"]["overall_status"] == "MISMATCH"
    assert rows["LO-VEHICLE-MISMATCH"]["vehicle_class_result"] == "MISMATCH"
    assert rows["LO-TAG-MISMATCH"]["overall_status"] == "MISMATCH"
    project_detail = next(detail for detail in rows["LO-TAG-MISMATCH"]["details"] if detail["tag_type"] == "PROJECT")
    assert project_detail["missing_tags"] == ["GUNUNG"]
    assert payload["summary"]["matched"] == 1
    assert payload["summary"]["mismatch"] == 2
    assert {"name": "Project", "value": 1} in payload["summary"]["mismatch_by_tag_type"]
    assert {"name": "GUNUNG", "value": 1} in payload["summary"]["mismatch_by_tag_value"]
    assert {"name": "Vehicle Class > Max 24", "value": 1} in payload["summary"]["mismatch_by_tag_value"]
