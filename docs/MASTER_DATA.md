# Master Data

Master data is authoritative only after publishing from staging. Historical LO and GPS evidence can create issues or recommendations, but it must not silently overwrite canonical MT, SPBU, depot, product, or tag records.

MT normalization separates:

- `vehicle_name_raw`
- `vehicle_registration`
- `capacity_label`

SPBU codes are strings. Coordinates are parsed into latitude and longitude only when valid.
