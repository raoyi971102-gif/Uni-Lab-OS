from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping


STACK_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "s10_liquid_reagent": {
        "display_name": "S10液体试剂瓶仓",
        "warehouse_name": "S10液体试剂瓶仓占位",
        "managed_resource": "reagent",
        "content_type": ["liquid_reagent"],
    },
    "powder_container": {
        "display_name": "固体粉桶仓",
        "warehouse_name": "固体粉桶仓占位",
        "managed_resource": "reagent",
        "content_type": ["powder_reagent"],
    },
    "s2_tip": {
        "display_name": "S2枪头仓",
        "warehouse_name": "S2枪头仓占位",
        "managed_resource": "physical_only",
        "content_type": ["tip"],
    },
    "s3_unused_beaker": {
        "display_name": "S3未使用烧杯仓",
        "warehouse_name": "S3未使用烧杯仓",
        "managed_resource": "physical_only",
        "content_type": ["beaker"],
    },
    "s3_unused_sample_vial": {
        "display_name": "S3未使用样品瓶仓",
        "warehouse_name": "S3未使用样品瓶仓",
        "managed_resource": "physical_only",
        "content_type": ["sample_vial"],
    },
    "s11_used_beaker": {
        "display_name": "S11使用烧杯成品仓",
        "warehouse_name": "S11使用烧杯成品仓",
        "managed_resource": "physical_only",
        "content_type": ["beaker"],
    },
    "s11_used_sample_vial": {
        "display_name": "S11使用样品瓶成品仓",
        "warehouse_name": "S11使用样品瓶成品仓",
        "managed_resource": "physical_only",
        "content_type": ["sample_vial"],
    },
}


def _json_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def build_stack_status(
    sensor_groups: Mapping[str, Mapping[str, Any]],
    *,
    reagent_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    updated_at: str | None = None,
) -> Dict[str, Any]:
    bindings = reagent_bindings or {}
    stacks: Dict[str, Dict[str, Any]] = {}

    for group_name, group_status in sensor_groups.items():
        definition = STACK_DEFINITIONS.get(group_name)
        if definition is None:
            continue
        slots: Dict[str, Dict[str, Any]] = {}
        for site_key in sorted(group_status):
            binding = bindings.get(group_name, {}).get(site_key, {})
            slots[site_key] = {
                "site_key": site_key,
                "occupied": _json_bool(group_status[site_key]),
                "reagent_id": binding.get("reagent_id"),
                "qr_code": binding.get("qr_code"),
                "remaining_amount": binding.get("remaining_amount"),
                "unit": binding.get("unit"),
            }

        stacks[group_name] = {
            "id": group_name,
            "display_name": definition["display_name"],
            "warehouse_name": definition["warehouse_name"],
            "managed_resource": definition["managed_resource"],
            "content_type": list(definition["content_type"]),
            "slots": slots,
        }

    return {
        "success": True,
        "schema": "szlab_poly_studio.stack_status.v1",
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
        "stacks": stacks,
    }
