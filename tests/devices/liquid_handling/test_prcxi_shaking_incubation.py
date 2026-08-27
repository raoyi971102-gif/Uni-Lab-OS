import asyncio
from unittest.mock import AsyncMock, MagicMock

from unilabos.devices.liquid_handling.prcxi.prcxi import (
    PRCXI9300Api,
    PRCXI9300Backend,
    PRCXI9300Handler,
)


def test_api_builds_shaking_incubation_step_with_sdk_field_order() -> None:
    api = PRCXI9300Api(debug=True)

    step = api.shaking_incubation_action(
        time=60,
        module_no=1,
        amplitude=200,
        is_wait=True,
        temperature=20,
    )

    assert step == {
        "StepAxis": "Left",
        "Function": "Shaking_Incubation",
        "AssistFun1": 60,
        "AssistFun2": 1,
        "AssistFun3": 200,
        "AssistFun4": True,
        "AssistFun5": 20,
    }


def test_backend_appends_shaking_incubation_step() -> None:
    backend = PRCXI9300Backend(tablets_info=[], setup=False)
    backend.api_client = MagicMock()
    backend.api_client.shaking_incubation_action.return_value = {"step": "heat-shake"}
    backend.steps_todo_list = []

    result = asyncio.run(
        backend.shaking_incubation_action(
            time=60,
            module_no=1,
            amplitude=200,
            is_wait=True,
            temperature=20,
        )
    )

    backend.api_client.shaking_incubation_action.assert_called_once_with(
        time=60,
        module_no=1,
        amplitude=200,
        is_wait=True,
        temperature=20,
    )
    assert result == {"step": "heat-shake"}
    assert backend.steps_todo_list == [{"step": "heat-shake"}]


def test_handler_exposes_registered_shaking_incubation_action() -> None:
    handler = object.__new__(PRCXI9300Handler)
    handler._unilabos_backend = MagicMock()
    handler._unilabos_backend.shaking_incubation_action = AsyncMock(
        return_value={"step": "heat-shake"}
    )

    result = asyncio.run(
        handler.shaking_incubation_action(
            time=60,
            module_no=1,
            amplitude=200,
            is_wait=True,
            temperature=20,
        )
    )

    handler._unilabos_backend.shaking_incubation_action.assert_awaited_once_with(
        time=60,
        module_no=1,
        amplitude=200,
        is_wait=True,
        temperature=20,
    )
    assert result == {"step": "heat-shake"}
