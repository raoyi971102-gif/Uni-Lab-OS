"""
机械臂 X 坐标移动测试脚本（独立于 robotic_arm.py 的交互菜单）

只测“小车 X 轴移动”这一件事：输入目标 X（或选工站）→ move_x 移动 → 回读 Robot_XPosFB 核对。
不涉及抓/放、旋转堆栈等动作，用于先单独验证 X 坐标是否走得准。

运行：
    python -m unilabos.devices.workstation.GN.test_x_move
或   python unilabos/devices/workstation/GN/test_x_move.py
"""

import time
import logging

from unilabos.utils.log import logger
from unilabos.devices.workstation.GN.robotic_arm import (
    RoboticArmDevice,
    DEFAULT_CSV_PATH,
    STATIONS,
    ITEM_PLATE,
    ITEM_BOTTLE,
)

URL = "opc.tcp://192.168.6.6:4840"
DEFAULT_SPEED = 300


def read_x(robot) -> object:
    """安全回读当前 X（Robot_XPosFB）。"""
    try:
        return robot.get_node_value("Robot_XPosFB", force_read=True)
    except Exception as e:
        logger.error(f"读取 Robot_XPosFB 失败: {e}")
        return None


def test_move(robot, target_x: int, speed: int = DEFAULT_SPEED) -> None:
    """移动到目标 X 并核对到位误差。"""
    before = read_x(robot)
    logger.info(f"→ 目标 X={target_x}（当前 X={before}，速度={speed}）")
    t0 = time.time()
    try:
        robot.move_x(int(target_x), x_speed=speed, timeout=120.0)
    except Exception as e:
        logger.error(f"✗ 移动失败: {e}")
        return
    after = read_x(robot)
    dt = time.time() - t0
    err = (after - target_x) if isinstance(after, (int, float)) else "N/A"
    logger.info(f"✓ 到位：X={after}（目标 {target_x}，误差 {err}，耗时 {dt:.1f}s）")


def _print_stations() -> list:
    keys = list(STATIONS)
    print("\n工站 X 坐标表：")
    for i, (key, st) in enumerate(STATIONS.items(), start=1):
        print(f"  {i:>2} {key:<14} X板={st.x_plate}  X瓶={st.x_bottle}")
    return keys


def print_status(robot) -> None:
    """读取并打印机械臂状态（get_status）。"""
    status = robot.get_status()
    print("\n机械臂状态：")
    for key, val in status.items():
        print(f"  {key:<16} = {val}")


# 可下发的维护动作：序号 → (名称, 方法名)
ROBOT_ACTIONS = {
    "1": ("使能", "enable"),
    "2": ("失能", "disable"),
    "3": ("复位", "reset"),
    "4": ("回原点", "go_home"),
    "5": ("停止", "stop"),
}


def dispatch_action(robot) -> None:
    """选择并下发一个机械臂维护动作。"""
    print("\n可下发动作：")
    for k, (name, _) in ROBOT_ACTIONS.items():
        print(f"  {k} {name}")
    sel = input("动作序号：").strip()
    if sel not in ROBOT_ACTIONS:
        print("无效动作")
        return
    name, method = ROBOT_ACTIONS[sel]
    logger.info(f"下发动作：{name}")
    result = getattr(robot, method)()
    logger.info(f"{name} 结果：{result}")


def _pick_station(title: str = "选择工站") -> str:
    """打印工站表并让用户选一个，返回工站 key（无效返回 None）。"""
    print(f"\n{title}：")
    keys = list(STATIONS)
    for i, (key, st) in enumerate(STATIONS.items(), start=1):
        flag = " [旋转堆栈,需工位号]" if getattr(st, "rotary", False) else ""
        print(f"  {i:>2} {key:<14} module={st.module_no} X板={st.x_plate} X瓶={st.x_bottle}{flag}")
    idx = input("工站编号：").strip()
    if not idx.isdigit() or not (1 <= int(idx) <= len(keys)):
        print("无效编号")
        return None
    return keys[int(idx) - 1]


def _ask_item() -> str:
    """选择物料，默认板。"""
    return ITEM_BOTTLE if input("物料 plate/bottle [plate]：").strip().lower() == "bottle" else ITEM_PLATE


def _ask_int(prompt: str, default: int) -> int:
    """读取整数，空输入用默认值。"""
    raw = input(f"{prompt} [{default}]：").strip()
    return int(raw) if raw.lstrip("-").isdigit() else default


def run_pick_or_place(robot, mode: str) -> None:
    """抓取或放置调试：选工站 + 物料 + 工位号 → 下发。"""
    key = _pick_station(f"选择{mode}工站")
    if not key:
        return
    item = _ask_item()
    number = _ask_int("工位号(旋转堆栈按此自动转列)", 1)
    fn = robot.pick if mode == "抓取" else robot.place
    logger.info(f"下发{mode}：station={key} item={item} number={number}")
    result = fn(station=key, item_type=item, number=number)
    logger.info(f"{mode}结果：{result}")


def run_transfer(robot) -> None:
    """搬运调试：源工站抓 → 目标工站放。"""
    src = _pick_station("选择 源 工站")
    if not src:
        return
    dst = _pick_station("选择 目标 工站")
    if not dst:
        return
    item = _ask_item()
    src_num = _ask_int("源 工位号", 1)
    dst_num = _ask_int("目标 工位号", 1)
    logger.info(f"下发搬运：{src}→{dst} item={item} src_num={src_num} dst_num={dst_num}")
    result = robot.transfer(src, dst, item, src_num, dst_num)
    logger.info(f"搬运结果：{result}")


def main() -> None:
    logging.getLogger("unilabos").setLevel(logging.INFO)
    logging.getLogger("opcua").setLevel(logging.WARNING)

    logger.info(f"连接机械臂 {URL} ...")
    robot = RoboticArmDevice(url=URL, csv_path=DEFAULT_CSV_PATH, use_subscription=False)
    time.sleep(1.0)
    logger.info(f"当前 X={read_x(robot)}")

    try:
        while True:
            print("\n===== X 坐标测试 =====")
            print("1 读取当前 X")
            print("2 输入目标 X 直接移动")
            print("3 选工站移动（板/瓶）")
            print("4 遍历所有工站板位 X（依次移动，谨慎！）")
            print("5 读取机械臂状态")
            print("6 下发动作（使能/失能/复位/回原点/停止）")
            print("7 抓取 pick（选工站/物料/工位号）")
            print("8 放置 place（选工站/物料/工位号）")
            print("9 搬运 transfer（源→目标）")
            print("99 退出")
            choice = input("请输入序号：").strip()

            if choice == "99":
                break
            elif choice == "1":
                logger.info(f"当前 X={read_x(robot)}")
            elif choice == "2":
                raw = input("目标 X（整数，如 -8582）：").strip()
                if not raw.lstrip("-").isdigit():
                    print("无效数字")
                    continue
                spd = input(f"速度 [{DEFAULT_SPEED}]：").strip()
                test_move(robot, int(raw), int(spd) if spd.isdigit() else DEFAULT_SPEED)
            elif choice == "3":
                keys = _print_stations()
                idx = input("工站编号：").strip()
                if not idx.isdigit() or not (1 <= int(idx) <= len(keys)):
                    print("无效编号")
                    continue
                st = STATIONS[keys[int(idx) - 1]]
                item = ITEM_BOTTLE if input("物料 plate/bottle [plate]：").strip().lower() == "bottle" else ITEM_PLATE
                x = st.x_for(item)
                if x is None:
                    print(f"该工站 {item} 未标定 X")
                    continue
                test_move(robot, int(x))
            elif choice == "4":
                if input("将依次移动到每个工站板位，确认？(y/N) ").strip().lower() != "y":
                    continue
                for key, st in STATIONS.items():
                    if st.x_plate is None:
                        continue
                    logger.info(f"== 工站 {key} 板位 X={st.x_plate} ==")
                    test_move(robot, int(st.x_plate))
                    time.sleep(0.5)
            elif choice == "5":
                print_status(robot)
            elif choice == "6":
                dispatch_action(robot)
            elif choice == "7":
                run_pick_or_place(robot, "抓取")
            elif choice == "8":
                run_pick_or_place(robot, "放置")
            elif choice == "9":
                run_transfer(robot)
            else:
                print("无效序号")
    finally:
        try:
            robot.disconnect()
        except Exception:
            pass
        print("测试结束。")


if __name__ == "__main__":
    main()
