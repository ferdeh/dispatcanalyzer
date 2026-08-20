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
