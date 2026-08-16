# Tag Model

Project tags are parsed into canonical rows:

- `master_tag_type`
- `master_tag`
- `tag_alias`
- `bridge_mt_tag`
- `bridge_spbu_tag`

Aliases normalize values such as `All In`, `ALLIN`, and `ALL IN` to a canonical tag. Tag type assignment is stored in data and is intended to be admin-editable.

Default Phase 0 tag type rules:

- Tag values `8`, `16`, `24`, and `32` default to `VEHICLE_CLASS`.
- All other tag values default to `PROJECT`.
- The Tag CRUD page can override `tag_type_id` with a dropdown populated from `master_tag_type`.
