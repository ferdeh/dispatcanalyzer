from app.normalization import infer_tag_type, normalize_product, parse_coordinate, parse_mt_name, split_project_tags


def test_mt_registration_normalization_preserves_capacity_warning() -> None:
    registration, capacity, messages = parse_mt_name("B9796SFV-16KL")
    assert registration == "B9796SFV"
    assert capacity == "16KL"
    assert messages == []


def test_unparseable_mt_name_keeps_identifier_with_warning() -> None:
    registration, capacity, messages = parse_mt_name("BK 1234 XYZ")
    assert registration == "BK1234XYZ"
    assert capacity is None
    assert "capacity label not parsed" in messages


def test_project_tags_split_but_products_with_commas_do_not() -> None:
    assert split_project_tags("Poltaplus Medan,All In,Gunung,Darat") == ["Poltaplus Medan", "All In", "Gunung", "Darat"]
    assert normalize_product("PERTAMAX,BULK") == "PERTAMAX,BULK"


def test_coordinate_parsing_validates_ranges() -> None:
    lat, lon, messages = parse_coordinate("3.602700,98.673300")
    assert lat == 3.6027
    assert lon == 98.6733
    assert messages == []

    comma_lat, comma_lon, comma_messages = parse_coordinate("5,19182389869645 96,4368560343681")
    assert comma_lat == 5.19182389869645
    assert comma_lon == 96.4368560343681
    assert comma_messages == []

    bad_lat, bad_lon, bad_messages = parse_coordinate("103.602700,98.673300")
    assert bad_lat is None
    assert bad_lon is None
    assert "latitude outside valid range" in bad_messages


def test_tag_type_defaults_vehicle_class_only_for_capacity_tags() -> None:
    assert infer_tag_type("8") == "VEHICLE_CLASS"
    assert infer_tag_type("16") == "VEHICLE_CLASS"
    assert infer_tag_type("24") == "VEHICLE_CLASS"
    assert infer_tag_type("32") == "VEHICLE_CLASS"
    assert infer_tag_type("APMS") == "PROJECT"
    assert infer_tag_type("All In") == "PROJECT"
