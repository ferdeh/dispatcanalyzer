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

The default vehicle mode is `EXACT_MATCH`. The implementation also supports `MT_CAPACITY_LE_SPBU_LIMIT` as a configurable mode. Product compatibility is marked `NOT_AVAILABLE` until an explicit product-rule source exists.
