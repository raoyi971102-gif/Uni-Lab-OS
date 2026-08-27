"""PRCXI 协议启动与完成判定测试。"""

from unittest.mock import Mock, patch

import pytest

from unilabos.devices.liquid_handling.prcxi.prcxi import (
    PRCXI9300Api,
    PRCXI9300Backend,
    PRCXIError,
)


def _api_with_statuses(statuses):
    api = object.__new__(PRCXI9300Api)
    api.step_state_list = Mock(side_effect=statuses)
    return api


def test_wait_for_finish_retries_empty_and_incomplete_status() -> None:
    expected_steps = 3
    api = _api_with_statuses(
        [
            None,
            [],
            [{"State": 0}],
            [{"State": 2}, {"State": 1}, {"State": 0}],
            [{"State": 2}, {"State": 2}, {"State": 2}],
        ]
    )

    with patch("unilabos.devices.liquid_handling.prcxi.prcxi.time.sleep") as sleep:
        assert api.wait_for_finish(expected_steps) is True

    assert api.step_state_list.call_count == 5
    assert sleep.call_count == 4


def test_wait_for_finish_uses_loaded_protocol_step_count() -> None:
    api = _api_with_statuses(
        [
            [],
            [{"State": 0}, {"State": 0}],
            [{"State": 2}, {"State": 2}],
        ]
    )

    with patch("unilabos.devices.liquid_handling.prcxi.prcxi.time.sleep"):
        assert api.wait_for_finish(None) is True


def test_wait_for_finish_ignores_stale_completed_baseline() -> None:
    stale_completed = [{"State": 2}, {"State": 2}, {"State": 2}]
    api = _api_with_statuses(
        [
            stale_completed,
            [{"State": 1}, {"State": 0}, {"State": 0}],
            [{"State": 2}, {"State": 2}, {"State": 2}],
        ]
    )

    with patch("unilabos.devices.liquid_handling.prcxi.prcxi.time.sleep") as sleep:
        assert api.wait_for_finish(
            3,
            baseline_status=stale_completed,
            require_state_transition=True,
        ) is True

    assert api.step_state_list.call_count == 3
    assert sleep.call_count == 2


def test_wait_for_finish_rejects_unchanged_stale_completion() -> None:
    stale_completed = [{"State": 2}]
    api = _api_with_statuses([stale_completed])

    with pytest.raises(PRCXIError, match="上一轮完成状态"):
        api.wait_for_finish(
            1,
            poll_interval=0,
            baseline_status=stale_completed,
            require_state_transition=True,
            start_timeout=0,
        )


def test_wait_for_finish_raises_on_failed_step() -> None:
    api = _api_with_statuses([[{"State": 2}, {"State": 3}]])

    with pytest.raises(PRCXIError, match=r"第 2 步 State=3"):
        api.wait_for_finish(2, poll_interval=0)


def test_run_protocol_passes_dynamic_local_step_count() -> None:
    backend = object.__new__(PRCXI9300Backend)
    backend.matrix_id = "matrix-id"
    backend.steps_todo_list = [{"Step": 1}, {"Step": 2}, {"Step": 3}]
    backend.api_client = Mock()
    backend.api_client.get_reset_status.return_value = True
    backend.api_client.add_solution.return_value = "solution-id"
    backend.api_client.load_solution.return_value = True
    baseline_status = [{"State": 0}, {"State": 0}, {"State": 0}]
    backend.api_client.step_state_list.return_value = baseline_status
    backend.api_client.start.return_value = True
    backend.api_client.wait_for_finish.return_value = True

    with patch("unilabos.devices.liquid_handling.prcxi.prcxi.time.sleep"):
        assert backend.run_protocol() is True
    backend.api_client.wait_for_finish.assert_called_once_with(
        3,
        baseline_status=baseline_status,
        require_state_transition=True,
    )


def test_run_protocol_existing_id_discovers_step_count_from_device() -> None:
    backend = object.__new__(PRCXI9300Backend)
    backend.steps_todo_list = []
    backend.api_client = Mock()
    backend.api_client.get_reset_status.return_value = True
    backend.api_client.load_solution.return_value = True
    baseline_status = [{"State": 0}, {"State": 0}]
    backend.api_client.step_state_list.return_value = baseline_status
    backend.api_client.start.return_value = True
    backend.api_client.wait_for_finish.return_value = True

    with patch("unilabos.devices.liquid_handling.prcxi.prcxi.time.sleep"):
        assert backend.run_protocol("existing-solution") is True
    backend.api_client.add_solution.assert_not_called()
    backend.api_client.wait_for_finish.assert_called_once_with(
        None,
        baseline_status=baseline_status,
        require_state_transition=True,
    )


def test_run_protocol_raises_instead_of_returning_false() -> None:
    backend = object.__new__(PRCXI9300Backend)
    backend.matrix_id = "matrix-id"
    backend.steps_todo_list = [{"Step": 1}]
    backend.api_client = Mock()
    backend.api_client.get_reset_status.return_value = True
    backend.api_client.add_solution.return_value = "solution-id"
    backend.api_client.load_solution.return_value = True
    backend.api_client.step_state_list.return_value = [{"State": 0}]
    backend.api_client.start.return_value = False

    with patch("unilabos.devices.liquid_handling.prcxi.prcxi.time.sleep"):
        with pytest.raises(PRCXIError, match="启动协议失败"):
            backend.run_protocol()


def test_run_protocol_rejects_empty_local_protocol() -> None:
    backend = object.__new__(PRCXI9300Backend)
    backend.steps_todo_list = []
    backend.api_client = Mock()
    backend.api_client.get_reset_status.return_value = True

    with pytest.raises(PRCXIError, match="没有可执行步骤"):
        backend.run_protocol()

    backend.api_client.add_solution.assert_not_called()
