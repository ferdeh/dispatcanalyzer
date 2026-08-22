from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class ImportAudit(Base):
    __tablename__ = "import_audit"

    import_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_checksum: Mapped[str | None] = mapped_column(String(128))
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    uploaded_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    uploaded_by: Mapped[str | None] = mapped_column(String(120))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, default=0)
    rejected_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="STAGED")
    published_at = mapped_column(DateTime(timezone=True), nullable=True)
    mapping_version: Mapped[str] = mapped_column(String(40), default="phase0.v1")


class StagingMixin:
    staging_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    import_id: Mapped[str] = mapped_column(String(64), ForeignKey("import_audit.import_id"), index=True)
    source_row_number: Mapped[int] = mapped_column(Integer)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    normalized_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_status: Mapped[str] = mapped_column(String(20), default="VALID")
    validation_messages: Mapped[list] = mapped_column(JSON, default=list)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class StgMT(StagingMixin, Base):
    __tablename__ = "stg_mt"


class StgSPBU(StagingMixin, Base):
    __tablename__ = "stg_spbu"


class StgLoadingOrder(StagingMixin, Base):
    __tablename__ = "stg_loading_order"


class StgGPSData(StagingMixin, Base):
    __tablename__ = "stg_gps_data"


class MasterDepot(Base):
    __tablename__ = "master_depot"

    depot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_code: Mapped[str | None] = mapped_column(String(80), unique=True)
    depot_name: Mapped[str] = mapped_column(String(255))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    region: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str | None] = mapped_column(String(80), default="Asia/Jakarta")
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class DepotIdentifierAlias(Base):
    __tablename__ = "depot_identifier_alias"

    depot_identifier_alias_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"))
    identifier_type: Mapped[str] = mapped_column(String(40))
    identifier_value: Mapped[str] = mapped_column(String(255))
    normalized_identifier: Mapped[str] = mapped_column(String(255), index=True)
    source_system: Mapped[str | None] = mapped_column(String(80))
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class MasterTagType(Base):
    __tablename__ = "master_tag_type"

    tag_type_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    admin_editable: Mapped[bool] = mapped_column(Boolean, default=True)


class MasterTag(Base):
    __tablename__ = "master_tag"

    tag_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tag_type_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_tag_type.tag_type_id"))
    tag_value: Mapped[str] = mapped_column(String(255))
    normalized_tag: Mapped[str] = mapped_column(String(255), unique=True)
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class TagAlias(Base):
    __tablename__ = "tag_alias"

    tag_alias_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    alias_value: Mapped[str] = mapped_column(String(255))
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True)
    canonical_tag_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_tag.tag_id"))
    source_domain: Mapped[str | None] = mapped_column(String(80))
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class MasterMT(Base):
    __tablename__ = "master_mt"

    mt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_mt_id: Mapped[str | None] = mapped_column(String(120))
    vehicle_name_raw: Mapped[str] = mapped_column(String(255))
    vehicle_registration: Mapped[str | None] = mapped_column(String(80), index=True)
    capacity_label: Mapped[str | None] = mapped_column(String(80))
    vehicle_type_tag: Mapped[int | None] = mapped_column(Integer)
    project_tag_raw: Mapped[str | None] = mapped_column(Text)
    number_of_compartments: Mapped[int | None] = mapped_column(Integer)
    depot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_depot.depot_id"))
    source_hub_id: Mapped[str | None] = mapped_column(String(120))
    assignee: Mapped[str | None] = mapped_column(String(255))
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    vehicle_height_mm: Mapped[int | None] = mapped_column(Integer)
    vehicle_length_mm: Mapped[int | None] = mapped_column(Integer)
    vehicle_weight_kg: Mapped[int | None] = mapped_column(Integer)
    vehicle_width_mm: Mapped[int | None] = mapped_column(Integer)
    vehicle_axle_count: Mapped[int | None] = mapped_column(Integer)
    hazmat_category: Mapped[str | None] = mapped_column(Text)
    large_vehicle_profile_status: Mapped[str] = mapped_column(String(20), default="NOT_REQUIRED")
    effective_start_date = mapped_column(Date, nullable=True)
    effective_end_date = mapped_column(Date, nullable=True)
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class BridgeMTTag(Base):
    __tablename__ = "bridge_mt_tag"

    mt_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_mt.mt_id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_tag.tag_id"), primary_key=True)
    source_import_id: Mapped[str | None] = mapped_column(String(64))


class MasterSPBU(Base):
    __tablename__ = "master_spbu"

    spbu_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    spbu_code: Mapped[str] = mapped_column(String(120), unique=True)
    spbu_name: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(120))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    source_coordinate: Mapped[str | None] = mapped_column(String(255))
    master_distance_km: Mapped[float | None] = mapped_column(Float)
    master_travel_time_min: Mapped[float | None] = mapped_column(Float)
    vehicle_type_tag: Mapped[int | None] = mapped_column(Integer)
    project_tag_raw: Mapped[str | None] = mapped_column(Text)
    primary_depot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_depot.depot_id"))
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    official_window_start = mapped_column(Time, nullable=True)
    official_window_end = mapped_column(Time, nullable=True)
    effective_start_date = mapped_column(Date, nullable=True)
    effective_end_date = mapped_column(Date, nullable=True)
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class BridgeSPBUTag(Base):
    __tablename__ = "bridge_spbu_tag"

    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_tag.tag_id"), primary_key=True)
    source_import_id: Mapped[str | None] = mapped_column(String(64))


class SpbuIdentifierAlias(Base):
    __tablename__ = "spbu_identifier_alias"

    spbu_identifier_alias_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"))
    identifier_type: Mapped[str] = mapped_column(String(40))
    identifier_value: Mapped[str] = mapped_column(String(255))
    normalized_identifier: Mapped[str] = mapped_column(String(255), index=True)
    source_system: Mapped[str | None] = mapped_column(String(80))
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class MasterProduct(Base):
    __tablename__ = "master_product"

    product_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_name: Mapped[str] = mapped_column(String(255))
    normalized_product: Mapped[str] = mapped_column(String(255), unique=True)
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductAlias(Base):
    __tablename__ = "product_alias"

    product_alias_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_product.product_id"))
    alias_value: Mapped[str] = mapped_column(String(255))
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True)
    source_system: Mapped[str | None] = mapped_column(String(80))
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class MasterPersonnel(Base):
    __tablename__ = "master_personnel"

    personnel_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_parent_id: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str | None] = mapped_column(String(255))
    nip: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(40))
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactShipment(Base):
    __tablename__ = "fact_shipment"

    shipment_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_shipment_id: Mapped[str] = mapped_column(String(120), unique=True)
    operating_date = mapped_column(Date, nullable=True)
    area_id: Mapped[str | None] = mapped_column(String(80))
    area: Mapped[str | None] = mapped_column(String(120))
    depot_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_depot.depot_id"))
    mt_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_mt.mt_id"))
    vehicle_registration: Mapped[str | None] = mapped_column(String(80))
    vehicle_mapping_status: Mapped[str] = mapped_column(String(40), default="UNMATCHED")
    vehicle_type_tag_observed: Mapped[str | None] = mapped_column(String(80))
    project_tag_raw: Mapped[str | None] = mapped_column(Text)
    validation_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    gate_out_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    shipment_end_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    driver_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_personnel.personnel_id"))
    assistant_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_personnel.personnel_id"))
    status: Mapped[str | None] = mapped_column(String(80))
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactLoadingOrderLine(Base):
    __tablename__ = "fact_loading_order_line"

    loading_order_number: Mapped[str] = mapped_column(String(120), primary_key=True)
    source_depot_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    shipment_id: Mapped[str] = mapped_column(String(120), ForeignKey("fact_shipment.shipment_id"))
    spbu_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"))
    spbu_mapping_status: Mapped[str] = mapped_column(String(40), default="UNMATCHED")
    source_spbu_code: Mapped[str | None] = mapped_column(String(120))
    shipto: Mapped[str | None] = mapped_column(String(120))
    product_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_product.product_id"))
    source_product_name: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(80))
    source_distance_km: Mapped[float | None] = mapped_column(Float)
    actual_km: Mapped[float | None] = mapped_column(Float)
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactShipmentSPBU(Base):
    __tablename__ = "fact_shipment_spbu"

    shipment_id: Mapped[str] = mapped_column(String(120), ForeignKey("fact_shipment.shipment_id"), primary_key=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), primary_key=True)
    assignment_source: Mapped[str] = mapped_column(String(40), default="LO")
    source_import_id: Mapped[str | None] = mapped_column(String(64))


class FactGPSEvent(Base):
    __tablename__ = "fact_gps_event"

    gps_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mt_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_mt.mt_id"))
    vehicle_registration: Mapped[str | None] = mapped_column(String(80))
    vehicle_mapping_status: Mapped[str] = mapped_column(String(40), default="UNMATCHED")
    source_device_id: Mapped[str | None] = mapped_column(String(120))
    event_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    ignition_status: Mapped[str | None] = mapped_column(String(40))
    event_type: Mapped[str | None] = mapped_column(String(80))
    nearest_spbu_id: Mapped[str | None] = mapped_column(String(64))
    nearest_depot_id: Mapped[str | None] = mapped_column(String(64))
    distance_to_spbu_m: Mapped[float | None] = mapped_column(Float)
    distance_to_depot_m: Mapped[float | None] = mapped_column(Float)
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    raw_event_reference: Mapped[str | None] = mapped_column(String(255))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class SpbuGeofence(Base):
    __tablename__ = "spbu_geofence"

    spbu_geofence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"))
    geofence_type: Mapped[str] = mapped_column(String(40), default="CIRCLE")
    radius_m: Mapped[float] = mapped_column(Float)
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    effective_start = mapped_column(Date, nullable=True)
    effective_end = mapped_column(Date, nullable=True)


class DepotGeofence(Base):
    __tablename__ = "depot_geofence"

    depot_geofence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"))
    geofence_type: Mapped[str] = mapped_column(String(40), default="CIRCLE")
    radius_m: Mapped[float] = mapped_column(Float)
    active_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    effective_start = mapped_column(Date, nullable=True)
    effective_end = mapped_column(Date, nullable=True)


class FactSPBUVisit(Base):
    __tablename__ = "fact_spbu_visit"

    spbu_visit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mt_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_mt.mt_id"))
    shipment_id: Mapped[str | None] = mapped_column(String(120), ForeignKey("fact_shipment.shipment_id"))
    spbu_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"))
    arrival_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    departure_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    dwell_minutes: Mapped[float | None] = mapped_column(Float)
    first_gps_event_id: Mapped[str | None] = mapped_column(String(64))
    last_gps_event_id: Mapped[str | None] = mapped_column(String(64))
    gps_event_count: Mapped[int | None] = mapped_column(Integer)
    min_distance_to_spbu_m: Mapped[float | None] = mapped_column(Float)
    visit_match_method: Mapped[str | None] = mapped_column(String(80))
    visit_confidence: Mapped[float | None] = mapped_column(Float)
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class FactShipmentStop(Base):
    __tablename__ = "fact_shipment_stop"

    shipment_stop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shipment_id: Mapped[str | None] = mapped_column(String(120), ForeignKey("fact_shipment.shipment_id"))
    spbu_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"))
    stop_sequence: Mapped[int | None] = mapped_column(Integer)
    arrival_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    departure_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    dwell_minutes: Mapped[float | None] = mapped_column(Float)
    sequence_source: Mapped[str] = mapped_column(String(40), default="UNKNOWN")
    sequence_confidence: Mapped[float | None] = mapped_column(Float)
    source_import_id: Mapped[str | None] = mapped_column(String(64))


class FactSPBUPair(Base):
    __tablename__ = "fact_spbu_pair"
    __table_args__ = (
        UniqueConstraint(
            "depot_id",
            "spbu_a_id",
            "spbu_b_id",
            "analysis_start_date",
            "analysis_end_date",
            "algorithm_version",
            name="uq_fact_spbu_pair_scope",
        ),
        Index("ix_fact_spbu_pair_depot_dates", "depot_id", "analysis_start_date", "analysis_end_date"),
        Index("ix_fact_spbu_pair_spbus", "spbu_a_id", "spbu_b_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    spbu_a_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    spbu_b_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    pair_count: Mapped[int] = mapped_column(Integer, default=0)
    shipment_a_count: Mapped[int] = mapped_column(Integer, default=0)
    shipment_b_count: Mapped[int] = mapped_column(Integer, default=0)
    total_shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    probability_b_given_a: Mapped[float] = mapped_column(Float, default=0.0)
    probability_a_given_b: Mapped[float] = mapped_column(Float, default=0.0)
    support: Mapped[float] = mapped_column(Float, default=0.0)
    lift: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_level: Mapped[str] = mapped_column(String(40), default="INSUFFICIENT_DATA")
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    analysis_start_date = mapped_column(Date, nullable=False)
    analysis_end_date = mapped_column(Date, nullable=False)
    calculated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    algorithm_version: Mapped[str] = mapped_column(String(80), default="pairing_v1")


class FactSPBUTransition(Base):
    __tablename__ = "fact_spbu_transition"
    __table_args__ = (
        UniqueConstraint(
            "depot_id",
            "from_spbu_id",
            "to_spbu_id",
            "analysis_start_date",
            "analysis_end_date",
            "algorithm_version",
            name="uq_fact_spbu_transition_scope",
        ),
        Index("ix_fact_spbu_transition_depot_dates", "depot_id", "analysis_start_date", "analysis_end_date"),
        Index("ix_fact_spbu_transition_spbus", "from_spbu_id", "to_spbu_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    from_spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    to_spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    transition_count: Mapped[int] = mapped_column(Integer, default=0)
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    transition_probability: Mapped[float] = mapped_column(Float, default=0.0)
    analysis_start_date = mapped_column(Date, nullable=False)
    analysis_end_date = mapped_column(Date, nullable=False)
    calculated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_level: Mapped[str] = mapped_column(String(40), default="INSUFFICIENT_DATA")
    algorithm_version: Mapped[str] = mapped_column(String(80), default="spbu_transition.consecutive_v1")


class FactSPBUMTPair(Base):
    __tablename__ = "fact_spbu_mt_pair"
    __table_args__ = (
        UniqueConstraint(
            "depot_id",
            "spbu_id",
            "mt_id",
            "analysis_start_date",
            "analysis_end_date",
            "product_filter",
            "algorithm_version",
            name="uq_fact_spbu_mt_pair_scope",
        ),
        Index("ix_fact_spbu_mt_pair_depot_dates", "depot_id", "analysis_start_date", "analysis_end_date"),
        Index("ix_fact_spbu_mt_pair_entities", "spbu_id", "mt_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    mt_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_mt.mt_id"), index=True)
    shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    total_spbu_shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    total_mt_shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    probability_mt_given_spbu: Mapped[float] = mapped_column(Float, default=0.0)
    probability_spbu_given_mt: Mapped[float] = mapped_column(Float, default=0.0)
    first_observed = mapped_column(Date, nullable=False)
    last_observed = mapped_column(Date, nullable=False)
    operating_day_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_level: Mapped[str] = mapped_column(String(20), default="LOW")
    analysis_start_date = mapped_column(Date, nullable=False)
    analysis_end_date = mapped_column(Date, nullable=False)
    product_filter: Mapped[str] = mapped_column(String(120), default="ALL")
    calculated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    algorithm_version: Mapped[str] = mapped_column(String(80), default="spbu_mt_affinity.jsd_v1")


class FactSPBUMTProfile(Base):
    __tablename__ = "fact_spbu_mt_profile"
    __table_args__ = (
        UniqueConstraint(
            "depot_id",
            "spbu_id",
            "analysis_start_date",
            "analysis_end_date",
            "product_filter",
            "temporal_bucket",
            "algorithm_version",
            name="uq_fact_spbu_mt_profile_scope",
        ),
        Index("ix_fact_spbu_mt_profile_depot_dates", "depot_id", "analysis_start_date", "analysis_end_date"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    operating_day_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_mt_count: Mapped[int] = mapped_column(Integer, default=0)
    dominant_mt_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_mt.mt_id"))
    dominant_mt_probability: Mapped[float] = mapped_column(Float, default=0.0)
    second_mt_probability: Mapped[float] = mapped_column(Float, default=0.0)
    top3_mt_share: Mapped[float] = mapped_column(Float, default=0.0)
    hhi: Mapped[float] = mapped_column(Float, default=0.0)
    normalized_hhi: Mapped[float] = mapped_column(Float, default=0.0)
    normalized_entropy: Mapped[float] = mapped_column(Float, default=0.0)
    consistency_score: Mapped[float] = mapped_column(Float, default=0.0)
    variability_score: Mapped[float] = mapped_column(Float, default=0.0)
    dominant_mt_persistence: Mapped[float] = mapped_column(Float, default=0.0)
    temporal_stability_score: Mapped[float] = mapped_column(Float, default=0.0)
    pattern_shift_level: Mapped[str] = mapped_column(String(40), default="STABLE")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_level: Mapped[str] = mapped_column(String(20), default="LOW")
    analysis_start_date = mapped_column(Date, nullable=False)
    analysis_end_date = mapped_column(Date, nullable=False)
    product_filter: Mapped[str] = mapped_column(String(120), default="ALL")
    temporal_bucket: Mapped[str] = mapped_column(String(20), default="WEEKLY")
    calculated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    algorithm_version: Mapped[str] = mapped_column(String(80), default="spbu_mt_affinity.jsd_v1")


class FactSPBUMTTemporalProfile(Base):
    __tablename__ = "fact_spbu_mt_temporal_profile"
    __table_args__ = (
        UniqueConstraint(
            "depot_id",
            "spbu_id",
            "mt_id",
            "period_type",
            "period_start",
            "analysis_start_date",
            "analysis_end_date",
            "algorithm_version",
            name="uq_fact_spbu_mt_temporal_scope",
        ),
        Index("ix_fact_spbu_mt_temporal_depot_period", "depot_id", "period_type", "period_start"),
        Index("ix_fact_spbu_mt_temporal_entities", "spbu_id", "mt_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    mt_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_mt.mt_id"), index=True)
    period_type: Mapped[str] = mapped_column(String(20))
    period_start = mapped_column(Date, nullable=False)
    period_end = mapped_column(Date, nullable=False)
    shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    total_spbu_shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    probability_mt_given_spbu: Mapped[float] = mapped_column(Float, default=0.0)
    is_dominant_mt: Mapped[bool] = mapped_column(Boolean, default=False)
    analysis_start_date = mapped_column(Date, nullable=False)
    analysis_end_date = mapped_column(Date, nullable=False)
    calculated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    algorithm_version: Mapped[str] = mapped_column(String(80), default="spbu_mt_affinity.jsd_v1")


class MLConcentrationAnalysisRun(Base):
    __tablename__ = "ml_concentration_analysis_run"
    __table_args__ = (
        Index("ix_ml_concentration_run_depot_dates", "depot_id", "baseline_start_date", "baseline_end_date"),
        Index("ix_ml_concentration_run_status_created", "status", "created_at"),
    )

    analysis_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    baseline_start_date = mapped_column(Date, nullable=False)
    baseline_end_date = mapped_column(Date, nullable=False)
    minimum_shipment_observation: Mapped[int] = mapped_column(Integer, default=10)
    algorithm_name: Mapped[str] = mapped_column(String(80), default="IsolationForest")
    algorithm_version: Mapped[str] = mapped_column(String(80), default="phase5.concentration.iforest.v1")
    algorithm_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    master_compatibility_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120), default="local-user")
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at = mapped_column(DateTime(timezone=True), nullable=True)


class MLSPBUConcentrationProfile(Base):
    __tablename__ = "ml_spbu_concentration_profile"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "spbu_id", name="uq_ml_concentration_profile_run_spbu"),
        Index("ix_ml_concentration_profile_run_score", "analysis_run_id", "concentration_anomaly_score"),
        Index("ix_ml_concentration_profile_depot_spbu", "depot_id", "spbu_id"),
    )

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    analysis_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ml_concentration_analysis_run.analysis_run_id", ondelete="CASCADE"), index=True
    )
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    shipment_observation_count: Mapped[int] = mapped_column(Integer, default=0)
    compatible_mt_count: Mapped[int] = mapped_column(Integer, default=0)
    historically_used_mt_count: Mapped[int] = mapped_column(Integer, default=0)
    utilization_breadth: Mapped[float] = mapped_column(Float, default=0.0)
    dominant_mt_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_mt.mt_id"))
    dominant_mt_share: Mapped[float] = mapped_column(Float, default=0.0)
    hhi: Mapped[float] = mapped_column(Float, default=0.0)
    entropy: Mapped[float] = mapped_column(Float, default=0.0)
    normalized_entropy: Mapped[float] = mapped_column(Float, default=0.0)
    raw_ml_anomaly_score: Mapped[float | None] = mapped_column(Float)
    concentration_anomaly_score: Mapped[float | None] = mapped_column(Float, index=True)
    concentration_classification: Mapped[str] = mapped_column(String(50), default="INSUFFICIENT_DATA")
    data_sufficiency_status: Mapped[str] = mapped_column(String(40), default="INSUFFICIENT_DATA")
    peer_statistics: Mapped[dict] = mapped_column(JSON, default=dict)
    mt_distribution: Mapped[list] = mapped_column(JSON, default=list)


class MLTrainingRun(Base):
    __tablename__ = "ml_training_run"
    __table_args__ = (
        Index("ix_ml_training_run_depot_dates", "depot_id", "training_start_date", "training_end_date"),
        Index("ix_ml_training_run_status_created", "status", "created_at"),
    )

    training_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    training_start_date = mapped_column(Date, nullable=False)
    training_end_date = mapped_column(Date, nullable=False)
    minimum_shipment_observation: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    training_configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    shift_definition_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    master_compatibility_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    algorithm_version: Mapped[str] = mapped_column(String(80), default="phase5.behavioral.portable_n2v_umap_hdbscan.v2")
    library_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_temp_path: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120), default="local-user")
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at = mapped_column(DateTime(timezone=True), nullable=True)


class MLBehavioralModel(Base):
    __tablename__ = "ml_behavioral_model"
    __table_args__ = (
        UniqueConstraint("depot_id", "model_name", "model_version", name="uq_ml_behavioral_model_name_version"),
        Index("ix_ml_behavioral_model_depot_status", "depot_id", "model_status"),
        Index("ix_ml_behavioral_model_created", "created_at"),
    )

    model_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), index=True)
    model_description: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[int] = mapped_column(Integer, default=1)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    source_training_run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("ml_training_run.training_run_id"))
    training_start_date = mapped_column(Date, nullable=False)
    training_end_date = mapped_column(Date, nullable=False)
    training_shipment_count: Mapped[int] = mapped_column(Integer, default=0)
    training_spbu_count: Mapped[int] = mapped_column(Integer, default=0)
    minimum_shipment_observation: Mapped[int] = mapped_column(Integer, default=10)
    tag_feature_configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    tag_encoder_reference: Mapped[dict] = mapped_column(JSON, default=dict)
    shift_definition_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    feature_weights: Mapped[dict] = mapped_column(JSON, default=dict)
    node2vec_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    umap_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    hdbscan_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    dependency_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    cluster_count: Mapped[int] = mapped_column(Integer, default=0)
    noise_spbu_count: Mapped[int] = mapped_column(Integer, default=0)
    average_membership_probability: Mapped[float] = mapped_column(Float, default=0.0)
    algorithm_version: Mapped[str] = mapped_column(String(80), default="phase5.behavioral.portable_n2v_umap_hdbscan.v2")
    library_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    random_seed: Mapped[int] = mapped_column(Integer, default=42)
    model_status: Mapped[str] = mapped_column(String(30), default="SAVED", index=True)
    created_by: Mapped[str] = mapped_column(String(120), default="local-user")
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MLModelArtifact(Base):
    __tablename__ = "ml_model_artifact"
    __table_args__ = (UniqueConstraint("model_id", "artifact_type", name="uq_ml_model_artifact_type"),)

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(64), ForeignKey("ml_behavioral_model.model_id", ondelete="CASCADE"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(60))
    storage_uri: Mapped[str] = mapped_column(Text)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class MLSPBUClusterAssignment(Base):
    __tablename__ = "ml_spbu_cluster_assignment"
    __table_args__ = (
        UniqueConstraint("model_id", "spbu_id", name="uq_ml_cluster_assignment_model_spbu"),
        Index("ix_ml_cluster_assignment_depot_spbu", "depot_id", "spbu_id"),
        Index("ix_ml_cluster_assignment_model_cluster", "model_id", "cluster_id"),
    )

    assignment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(64), ForeignKey("ml_behavioral_model.model_id", ondelete="CASCADE"), index=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    cluster_id: Mapped[int | None] = mapped_column(Integer)
    cluster_label: Mapped[str] = mapped_column(String(120))
    membership_probability: Mapped[float] = mapped_column(Float, default=0.0)
    is_noise: Mapped[bool] = mapped_column(Boolean, default=False)
    dominant_shift: Mapped[str | None] = mapped_column(String(120))
    key_tags: Mapped[list] = mapped_column(JSON, default=list)
    visualization_x: Mapped[float | None] = mapped_column(Float)
    visualization_y: Mapped[float | None] = mapped_column(Float)


class MLClusterProfile(Base):
    __tablename__ = "ml_cluster_profile"
    __table_args__ = (UniqueConstraint("model_id", "cluster_id", name="uq_ml_cluster_profile_model_cluster"),)

    cluster_profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(64), ForeignKey("ml_behavioral_model.model_id", ondelete="CASCADE"), index=True)
    cluster_id: Mapped[int] = mapped_column(Integer)
    cluster_label: Mapped[str] = mapped_column(String(120))
    cluster_size: Mapped[int] = mapped_column(Integer, default=0)
    training_spbu_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    common_tags: Mapped[list] = mapped_column(JSON, default=list)
    shift_distribution: Mapped[list] = mapped_column(JSON, default=list)
    dominant_shift: Mapped[str | None] = mapped_column(String(120))
    top_internal_pairings: Mapped[list] = mapped_column(JSON, default=list)
    average_membership_probability: Mapped[float] = mapped_column(Float, default=0.0)
    low_confidence_member_count: Mapped[int] = mapped_column(Integer, default=0)


class PredictionRun(Base):
    __tablename__ = "prediction_run"
    __table_args__ = (
        Index("ix_prediction_run_depot_created", "depot_id", "created_at"),
        Index("ix_prediction_run_model_created", "model_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prediction_run_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    depot_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_depot.depot_id"), index=True)
    model_id: Mapped[str] = mapped_column(String(64), ForeignKey("ml_behavioral_model.model_id"), index=True)
    model_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    created_by: Mapped[str] = mapped_column(String(120), default="local-user")
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at = mapped_column(DateTime(timezone=True), nullable=True)
    input_loading_order_filename: Mapped[str] = mapped_column(String(255))
    input_mt_availability_filename: Mapped[str] = mapped_column(String(255))
    input_loading_order_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    input_mt_availability_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    validation_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    parameter_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    model_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    original_prediction_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    final_prediction_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    routing_configuration_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    routing_metrics_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    algorithm_version: Mapped[str] = mapped_column(String(100))
    validation_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    shipment_prediction_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    mt_prediction_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    assignment_optimization_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_prediction_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)


class PredictionShipment(Base):
    __tablename__ = "prediction_shipment"
    __table_args__ = (
        UniqueConstraint("prediction_run_id", "predicted_shipment_id", name="uq_prediction_shipment_run_number"),
        Index("ix_prediction_shipment_run_shift", "prediction_run_id", "shift_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prediction_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("prediction_run.id", ondelete="CASCADE"), index=True
    )
    predicted_shipment_id: Mapped[str] = mapped_column(String(120), index=True)
    shift_id: Mapped[str] = mapped_column(String(80), index=True)
    shift_name: Mapped[str] = mapped_column(String(120))
    planned_start_datetime = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    shipment_prediction_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_level: Mapped[str] = mapped_column(String(20), default="LOW")
    low_confidence: Mapped[bool] = mapped_column(Boolean, default=False)
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class PredictionShipmentLine(Base):
    __tablename__ = "prediction_shipment_line"
    __table_args__ = (
        UniqueConstraint("prediction_run_id", "loading_order_no", name="uq_prediction_line_run_lo"),
        Index("ix_prediction_line_shipment_spbu", "prediction_shipment_id", "spbu_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prediction_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("prediction_run.id", ondelete="CASCADE"), index=True
    )
    prediction_shipment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("prediction_shipment.id", ondelete="CASCADE"), index=True
    )
    loading_order_no: Mapped[str] = mapped_column(String(120), index=True)
    spbu_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_spbu.spbu_id"), index=True)
    spbu_no: Mapped[str] = mapped_column(String(120))
    order_quantity_kl: Mapped[float | None] = mapped_column(Float)
    shipment_start_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    model_predicted_shipment_id: Mapped[str] = mapped_column(String(120))


class PredictionMTCandidate(Base):
    __tablename__ = "prediction_mt_candidate"
    __table_args__ = (
        UniqueConstraint("prediction_shipment_id", "vehicle_id", name="uq_prediction_candidate_shipment_vehicle"),
        Index("ix_prediction_candidate_shipment_rank", "prediction_shipment_id", "candidate_rank"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prediction_shipment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("prediction_shipment.id", ondelete="CASCADE"), index=True
    )
    vehicle_id: Mapped[str] = mapped_column(String(64), ForeignKey("master_mt.mt_id"), index=True)
    prediction_score: Mapped[float] = mapped_column(Float, default=0.0)
    compatibility_status: Mapped[str] = mapped_column(String(20))
    candidate_rank: Mapped[int | None] = mapped_column(Integer)
    exclusion_reason: Mapped[str | None] = mapped_column(String(120))
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)


class PredictionAssignment(Base):
    __tablename__ = "prediction_assignment"
    __table_args__ = (UniqueConstraint("prediction_shipment_id", name="uq_prediction_assignment_shipment"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prediction_shipment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("prediction_shipment.id", ondelete="CASCADE"), index=True
    )
    original_vehicle_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_mt.mt_id"))
    original_assignment_score: Mapped[float | None] = mapped_column(Float)
    final_vehicle_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_mt.mt_id"), index=True)
    final_assignment_score: Mapped[float | None] = mapped_column(Float)
    assignment_status: Mapped[str] = mapped_column(String(40), default="UNASSIGNED")
    unassigned_reason: Mapped[str | None] = mapped_column(String(80))
    override_reason: Mapped[str | None] = mapped_column(Text)
    override_user: Mapped[str | None] = mapped_column(String(120))
    override_timestamp = mapped_column(DateTime(timezone=True), nullable=True)


class PredictionTrip(Base):
    __tablename__ = "prediction_trip"
    __table_args__ = (
        UniqueConstraint("prediction_shipment_id", name="uq_prediction_trip_shipment"),
        UniqueConstraint("prediction_run_id", "trip_id", name="uq_prediction_trip_run_number"),
        Index("ix_prediction_trip_vehicle_departure", "vehicle_id", "predicted_departure_datetime"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prediction_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("prediction_run.id", ondelete="CASCADE"), index=True
    )
    prediction_shipment_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("prediction_shipment.id", ondelete="CASCADE"), index=True
    )
    trip_id: Mapped[str] = mapped_column(String(120), index=True)
    trip_number: Mapped[int | None] = mapped_column(Integer)
    vehicle_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("master_mt.mt_id"), index=True)
    planned_start_datetime = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    predicted_departure_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0)
    estimated_visit_sequence: Mapped[list] = mapped_column(JSON, default=list)
    routing_provider: Mapped[str | None] = mapped_column(String(80))
    routing_mode: Mapped[str | None] = mapped_column(String(40))
    routing_preference: Mapped[str | None] = mapped_column(String(40))
    large_vehicle_used: Mapped[bool] = mapped_column(Boolean, default=False)
    route_distance_meters: Mapped[int | None] = mapped_column(Integer)
    route_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    static_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    service_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    turnaround_buffer_seconds: Mapped[int | None] = mapped_column(Integer)
    total_cycle_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    estimated_return_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    next_available_datetime = mapped_column(DateTime(timezone=True), nullable=True)
    routing_confidence: Mapped[str | None] = mapped_column(String(20))
    route_estimation_source: Mapped[str | None] = mapped_column(String(80))
    service_time_source: Mapped[str | None] = mapped_column(String(80))
    assignment_status: Mapped[str] = mapped_column(String(40), default="UNASSIGNED")
    unassigned_reason: Mapped[str | None] = mapped_column(String(120))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    warning_codes: Mapped[list] = mapped_column(JSON, default=list)
    vehicle_profile_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoogleRoutesConfiguration(Base):
    __tablename__ = "google_routes_configuration"

    configuration_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    masked_api_key: Mapped[str | None] = mapped_column(String(40))
    connection_status: Mapped[str] = mapped_column(String(40), default="NOT_CONFIGURED")
    truck_routing_status: Mapped[str] = mapped_column(String(40), default="DISABLED_FOR_INDONESIA")
    routing_mode: Mapped[str] = mapped_column(String(20), default="DRIVE")
    routing_preference: Mapped[str] = mapped_column(String(40), default="TRAFFIC_AWARE")
    fallback_policy: Mapped[str] = mapped_column(String(50), default="NOT_APPLICABLE")
    cache_ttl_minutes: Mapped[int] = mapped_column(Integer, default=60)
    departure_time_bucket_minutes: Mapped[int] = mapped_column(Integer, default=15)
    default_depot_processing_minutes: Mapped[int] = mapped_column(Integer, default=30)
    default_spbu_service_minutes: Mapped[int] = mapped_column(Integer, default=45)
    default_return_processing_minutes: Mapped[int] = mapped_column(Integer, default=15)
    default_turnaround_buffer_minutes: Mapped[int] = mapped_column(Integer, default=30)
    default_route_duration_minutes: Mapped[int] = mapped_column(Integer, default=120)
    configuration_version: Mapped[int] = mapped_column(Integer, default=1)
    last_test_result: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(120))
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RouteEstimationCache(Base):
    __tablename__ = "route_estimation_cache"
    __table_args__ = (
        Index("ix_route_cache_expires", "expires_at"),
        Index("ix_route_cache_locations_mode", "origin_location_id", "destination_location_id", "routing_mode"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    origin_location_id: Mapped[str] = mapped_column(String(120))
    destination_location_id: Mapped[str] = mapped_column(String(120))
    origin_latitude: Mapped[float] = mapped_column(Float)
    origin_longitude: Mapped[float] = mapped_column(Float)
    destination_latitude: Mapped[float] = mapped_column(Float)
    destination_longitude: Mapped[float] = mapped_column(Float)
    departure_time_bucket = mapped_column(DateTime(timezone=True), nullable=True)
    vehicle_profile_hash: Mapped[str] = mapped_column(String(64))
    routing_mode: Mapped[str] = mapped_column(String(40))
    routing_preference: Mapped[str] = mapped_column(String(40))
    distance_meters: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    static_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    provider_source: Mapped[str] = mapped_column(String(80))
    response_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    calculated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at = mapped_column(DateTime(timezone=True), nullable=False)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issue"

    issue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(120))
    source_import_id: Mapped[str | None] = mapped_column(String(64))
    rule_code: Mapped[str] = mapped_column(String(120))
    severity: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="OPEN")
    detected_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at = mapped_column(DateTime(timezone=True), nullable=True)
