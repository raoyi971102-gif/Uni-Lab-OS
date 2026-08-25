import threading
import time

from unilabos.devices.workstation.AI4C.AI4C import (
    AI4CDevice,
    _ROBOTIC_ARM_ACTIONS,
)
from unilabos.registry.decorators import get_action_meta


def _bare_device() -> AI4CDevice:
    """构造不连接 PLC 的轻量实例，用于测试并发辅助逻辑。"""
    return object.__new__(AI4CDevice)


def test_all_configured_robotic_arm_actions_exist() -> None:
    missing = [name for name in _ROBOTIC_ARM_ACTIONS if not callable(getattr(AI4CDevice, name, None))]
    assert missing == []


def test_operation_lock_serializes_calls() -> None:
    device = _bare_device()
    device._robotic_arm_lock = threading.RLock()
    state_lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    def operation() -> None:
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.01)
        with state_lock:
            state["active"] -= 1

    locked_operation = device._make_operation_locked(operation)
    threads = [threading.Thread(target=locked_operation) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert state["max_active"] == 1


def test_operation_lock_is_reentrant() -> None:
    device = _bare_device()
    device._robotic_arm_lock = threading.RLock()
    inner = device._make_operation_locked(lambda: "done")
    outer = device._make_operation_locked(inner)

    assert outer() == "done"


def test_operation_lock_preserves_action_metadata() -> None:
    device = _bare_device()
    device._robotic_arm_lock = threading.RLock()
    original = AI4CDevice.pick_well_plate_from_loading_rack
    wrapped = device._make_operation_locked(original)

    assert get_action_meta(wrapped) == get_action_meta(original)


def test_arm_status_poller_updates_cache_and_keeps_last_value_on_error() -> None:
    device = _bare_device()
    device._arm_status_nodes = ["Robotic_Arm_Idle", "Robotic_Arm_Fault"]
    device._arm_status_cache = {
        "Robotic_Arm_Idle": False,
        "Robotic_Arm_Fault": True,
    }
    device._arm_status_poller_stop = threading.Event()

    def get_node_value(node_name: str) -> bool:
        if node_name == "Robotic_Arm_Idle":
            return True
        device._arm_status_poller_stop.set()
        raise RuntimeError("临时读取失败")

    device.get_node_value = get_node_value
    device._arm_status_poll_loop()

    assert device.is_robotic_arm_idle() is True
    assert device.robotic_arm_idle() is True
    assert device.robotic_arm_fault() is True


def test_arm_status_cache_defaults_to_false() -> None:
    device = _bare_device()
    device._arm_status_cache = {}

    assert device.is_robotic_arm_idle() is False
    assert device.robotic_arm_idle() is False
    assert device.robotic_arm_fault() is False
