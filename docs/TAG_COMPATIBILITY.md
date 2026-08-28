# Tag Compatibility

The Phase 0 compatibility function is available at:

`POST /api/v1/master/compatibility/check`

It returns:

- `compatible`
- `vehicle_type_check`
- `project_tag_check`
- `product_check`
- `depot_check`
- `matched_tags`
- `failed_rules`
- `warnings`
- `explanation`

The canonical vehicle mode is `MT_CAPACITY_LE_SPBU_LIMIT`. The vehicle class on
an SPBU is the maximum MT capacity that may enter: an SPBU tagged `32` accepts
MT classes `32`, `24`, `16`, and `8`; an SPBU tagged `24` accepts `24`, `16`,
and `8`. `EXACT_MATCH` remains available only for explicit legacy diagnostics.
Product compatibility is marked `NOT_AVAILABLE` until an explicit product-rule
source exists.
