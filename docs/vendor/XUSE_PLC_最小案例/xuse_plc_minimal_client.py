#!/usr/bin/env python3
"""XUSE OPC UA 最小客户端示例。

这是独立示例，不依赖 Uni-Lab-OS。PLC 作为 OPC UA Server，
客户端只负责读写约定节点并执行握手。
"""

from __future__ import annotations

import argparse
import time
from typing import Any

from opcua import Client, ua


# 与 xuse_nodes_minimal.csv 对应；如果供应商更换 NodeId，只改这一份映射。
NODE_IDS: dict[str, str] = {
    "Robotic_Arm_Idle_1": "ns=4;s=uniab|机械臂空闲_1",
    "Robotic_Arm_Fault_1": "ns=4;s=uniab|机械臂故障_1",
    "Robotic_Arm_Target_Position_Code_1": "ns=4;s=uniab|机械臂目标位置代码_1",
    "Robotic_Arm_Target_Pick_Place_Code_1": "ns=4;s=uniab|机械臂目标取放代码_1",
    "Robotic_Arm_Action_Trigger_1": "ns=4;s=uniab|机械臂动作触发_1",
    "Robotic_Arm_Action_Complete_1": "ns=4;s=uniab|机械臂动作完成_1",
    "Add_Sample_Occupied": "ns=4;s=uniab|加样占位",
    "Add_Sample_Request_Process": "ns=4;s=uniab|加样请求加工",
    "Add_Sample_Start_Process": "ns=4;s=uniab|加样开始加工",
    "Add_Sample_Process_Complete": "ns=4;s=uniab|加样加工完成",
    "Powder_Name": "ns=4;s=uniab|粉末名称",
    "Add_Sample_Weight": "ns=4;s=uniab|加样_重量",
    "Powder_Weight": "ns=4;s=uniab|加粉重量",
}


class XuseClient:
    """只封装节点读写和带超时的轮询。"""

    def __init__(self, endpoint: str, username: str | None = None, password: str | None = None):
        self.client = Client(endpoint)
        if username:
            self.client.set_user(username)
            self.client.set_password(password or "")

    def __enter__(self) -> "XuseClient":
        self.client.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.client.disconnect()

    def node(self, name: str):
        try:
            node_id = NODE_IDS[name]
        except KeyError as exc:
            raise KeyError(f"未配置节点: {name}") from exc
        return self.client.get_node(node_id)

    def read(self, name: str) -> Any:
        return self.node(name).get_value()

    def write(self, name: str, value: Any) -> None:
        node = self.node(name)
        # 按服务器节点声明的类型写入，避免 Boolean/Int16/Float 被错误转换。
        variant_type = node.get_data_type_as_variant_type()
        node.set_value(ua.DataValue(ua.Variant(value, variant_type)))

    def wait_for(self, name: str, expected: Any, timeout: float = 120.0, interval: float = 0.2) -> Any:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.read(name)
            if current == expected:
                return current
            time.sleep(interval)
        raise TimeoutError(f"等待 {name} == {expected!r} 超时；最后值为 {self.read(name)!r}")

    def run_process(self, prefix: str, timeout: float = 120.0) -> None:
        """执行 Request_Process → Start_Process → Process_Complete 握手。"""
        request = f"{prefix}_Request_Process"
        start = f"{prefix}_Start_Process"
        complete = f"{prefix}_Process_Complete"
        self.wait_for(request, True, timeout=timeout)
        self.write(start, True)
        try:
            self.wait_for(complete, True, timeout=timeout)
        finally:
            # 无论成功或异常都要复位触发位，防止 PLC 保持运行。
            self.write(start, False)
        self.wait_for(complete, False, timeout=timeout)

    def arm1_pick_from_rack(self, rack_position: int, timeout: float = 120.0) -> None:
        if not 1 <= rack_position <= 32:
            raise ValueError("rack_position 必须在 1..32")
        self.wait_for("Robotic_Arm_Idle_1", True, timeout=timeout)
        if self.read("Robotic_Arm_Fault_1"):
            raise RuntimeError("机械臂 1 当前故障，取消动作")

        # 机械臂 1 的位置代码 1 表示罐架区；取罐架第 N 位的动作代码为 N。
        self.write("Robotic_Arm_Action_Complete_1", False)
        self.write("Robotic_Arm_Target_Position_Code_1", 1)
        self.write("Robotic_Arm_Target_Pick_Place_Code_1", rack_position)
        self.write("Robotic_Arm_Action_Trigger_1", False)
        time.sleep(0.5)
        self.write("Robotic_Arm_Action_Trigger_1", True)
        try:
            self.wait_for("Robotic_Arm_Action_Complete_1", True, timeout=timeout)
        finally:
            self.write("Robotic_Arm_Action_Trigger_1", False)
        self.wait_for("Robotic_Arm_Action_Complete_1", False, timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="XUSE OPC UA 最小客户端")
    parser.add_argument("--endpoint", required=True, help="例如 opc.tcp://192.168.1.10:4840")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--action", choices=("add-sample", "arm-pick"), default="add-sample")
    parser.add_argument("--rack-position", type=int, default=1)
    args = parser.parse_args()

    with XuseClient(args.endpoint, args.username, args.password) as xuse:
        if args.action == "add-sample":
            xuse.run_process("Add_Sample", timeout=args.timeout)
            print({
                "powder_name": xuse.read("Powder_Name"),
                "target_weight": xuse.read("Add_Sample_Weight"),
                "actual_weight": xuse.read("Powder_Weight"),
            })
        else:
            xuse.arm1_pick_from_rack(args.rack_position, timeout=args.timeout)
            print(f"机械臂 1 已完成从罐架第 {args.rack_position} 位取罐")


if __name__ == "__main__":
    main()
