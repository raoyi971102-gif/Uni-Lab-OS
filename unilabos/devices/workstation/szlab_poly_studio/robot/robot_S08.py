from __future__ import annotations

from typing import Any

from .robot_tasks import build_variables


class SzlabRobotS08Mixin:
    def _run_s08_place(self, product_type: int, position: int, target_sensor_variable: str) -> dict[str, Any]:
        return self._submit_robot_task(
            task="place",
            station="S08",
            task_number=17,
            variables=build_variables("place_to_s08", S08取放料产品=product_type, S08取放料编号=position),
            reset_variables={"S08取放料产品": 0, "S08取放料编号": 0, "PLC_R任务号": 0},
            precheck=lambda: self._ensure_sensor_gate(target_sensor_variable, False, "S08 放料目标位必须为空"),
            product_type=int(product_type),
            position=int(position),
            target_sensor_variable=target_sensor_variable,
        )

    def _run_s08_pick(self, product_type: int, position: int, source_sensor_variable: str) -> dict[str, Any]:
        return self._submit_robot_task(
            task="pick",
            station="S08",
            task_number=18,
            variables=build_variables("pick_from_s08", S08取放料产品=product_type, S08取放料编号=position),
            reset_variables={"S08取放料产品": 0, "S08取放料编号": 0, "PLC_R任务号": 0},
            precheck=lambda: self._ensure_sensor_gate(source_sensor_variable, True, "S08 取料源位必须有物料"),
            product_type=int(product_type),
            position=int(position),
            source_sensor_variable=source_sensor_variable,
        )
