from __future__ import annotations

from typing import Any

from .robot_tasks import build_variables


class SzlabRobotS01Mixin:
    def _run_s01_pick(
        self,
        product_type: int,
        source_sensor_variable: str,
    ) -> dict[str, Any]:
        return self._submit_robot_task(
            task="pick",
            station="S01",
            task_number=1,
            variables=build_variables("pick_from_s01", S01出入料产品=product_type),
            reset_variables={"S01出入料产品": 0, "PLC_R任务号": 0},
            precheck=lambda: self._ensure_sensor_gate(source_sensor_variable, True, "S01 取料源位必须有物料"),
            product_type=int(product_type),
            source_sensor_variable=source_sensor_variable,
        )

    def _run_s01_pick_position(
        self,
        position: int,
        source_sensor_variable: str,
    ) -> dict[str, Any]:
        return self._submit_robot_task(
            task="pick",
            station="S01",
            task_number=2,
            variables=build_variables("pick_from_s01_position", S01取放料编号=position),
            reset_variables={"S01取放料编号": 0, "PLC_R任务号": 0},
            precheck=lambda: self._ensure_sensor_gate(source_sensor_variable, True, "S01 取料源位必须有物料"),
            position=int(position),
            source_sensor_variable=source_sensor_variable,
        )
