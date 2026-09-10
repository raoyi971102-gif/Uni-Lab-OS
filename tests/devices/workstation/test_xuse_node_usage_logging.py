from types import SimpleNamespace
from unittest.mock import patch

from unilabos.devices.workstation.XUSE import base_opcua_client as base_module


def _new_client():
    client = object.__new__(base_module.BaseOpcUaClient)
    import threading

    client._node_usage_lock = threading.RLock()
    client._node_usage_stats = {}
    return client


def test_node_usage_logs_once_then_summarizes_after_interval():
    client = _new_client()
    node = SimpleNamespace(node_id="ns=4;s=uniab|加粉重量")

    with patch.object(base_module.logger, "debug") as debug_log:
        with patch.object(base_module.time, "monotonic", side_effect=[0.0, 5.0, 61.0]):
            client._log_node_usage(node, display_name="加粉重量")
            client._log_node_usage(node, display_name="加粉重量")
            client._log_node_usage(node, display_name="加粉重量")

    assert debug_log.call_count == 2
    assert "首次使用节点" in debug_log.call_args_list[0].args[0]
    assert "节点使用汇总" in debug_log.call_args_list[1].args[0]
    assert "最近 60 秒访问 3 次" in debug_log.call_args_list[1].args[0]


def test_node_usage_aggregates_english_and_chinese_aliases_by_node_id():
    client = _new_client()
    node = SimpleNamespace(node_id="ns=4;s=uniab|加粉重量")

    with patch.object(base_module.logger, "debug") as debug_log:
        with patch.object(base_module.time, "monotonic", side_effect=[0.0, 61.0]):
            client._log_node_usage(node, display_name="加粉重量", alias="Powder_Weight")
            client._log_node_usage(node, display_name="加粉重量")

    assert debug_log.call_count == 2
    assert "Powder_Weight" in debug_log.call_args_list[0].args[0]
    assert "最近 60 秒访问 2 次" in debug_log.call_args_list[1].args[0]
