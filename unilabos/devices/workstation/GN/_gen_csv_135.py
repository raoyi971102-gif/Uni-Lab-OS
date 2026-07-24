"""把 OPC_UA协议1.3.5.csv（协议文档格式）转换为加载器可用的节点 CSV。

加载器（base_opcua_client.load_csv）按列名读取 Name/NodeType/DataType，
可选 EnglishName/NodeLanguage/NodeId；其余列忽略。驱动代码用 English 名
（NodeId 末段）访问节点，故 Name 直接取 English 名，保证唯一且与驱动一致。

额外：源表最左"设备模块"合并单元格里含每个模块的指令清单（如 1=X向左…），
解析出来后写到该模块 *_CmdType 节点的"设备模块指令"列，方便查阅。
"""

import csv
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(_DIR, "OPC_UA协议1.3.5.csv")
DST = os.path.join(_DIR, "opcua_gn1.3.5.csv")

DTYPE_MAP = {
    "int16": "INT16",
    "uint16": "UINT16",
    "int32": "INT32",
    "uint32": "UINT32",
    "int64": "INT64",
    "double": "DOUBLE",
    "float": "FLOAT",
    "real": "FLOAT",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "string": "STRING",
}


def find_node_cell(row):
    """返回 (nodeid, 点位名称, 数据类型, 读写)"""
    for i, cell in enumerate(row):
        if cell and "OPC_UA." in cell and cell.strip().startswith("ns="):
            rest = row[i + 1 : i + 4]
            rest += [""] * (3 - len(rest))
            return cell.strip(), rest[0].strip(), rest[1].strip(), rest[2].strip()
    return None


def parse_commands(module_cell):
    """从设备模块合并单元格解析指令清单：首行是模块名，其余是 N=动作。"""
    lines = [ln.strip() for ln in module_cell.splitlines() if ln.strip()]
    if not lines:
        return "", ""
    name = lines[0]
    cmds = ";".join(re.sub(r"\s+", "", ln) for ln in lines[1:])
    return name, cmds


def main():
    rows = []
    seen = set()
    unknown = set()
    module_name, module_cmds = "", ""
    pending = []  # 当前模块累积的节点，等遇到 CmdType 回填指令

    with open(SRC, encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            module_cell = row[0] if row else ""
            if module_cell.strip():
                module_name, module_cmds = parse_commands(module_cell)

            hit = find_node_cell(row)
            if not hit:
                continue
            node_id, cn_name, dtype_raw, _rw = hit
            eng = node_id.split(".")[-1].strip()
            if not eng or eng in seen:
                continue
            seen.add(eng)
            dtype = DTYPE_MAP.get(dtype_raw.lower())
            if dtype is None:
                unknown.add(dtype_raw)
                dtype = "INT16"

            cmd_col = f"{module_name}: {module_cmds}" if (eng.endswith("CmdType") and module_cmds) else ""
            rows.append([eng, eng, "VARIABLE", dtype, "English", node_id, cn_name, cmd_col])

    with open(DST, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Name", "EnglishName", "NodeType", "DataType", "NodeLanguage", "NodeId", "描述", "设备模块指令"])
        w.writerows(rows)

    print(f"生成 {DST}，共 {len(rows)} 个节点")
    if unknown:
        print(f"未识别数据类型(已按 INT16 处理): {unknown}")


if __name__ == "__main__":
    main()
