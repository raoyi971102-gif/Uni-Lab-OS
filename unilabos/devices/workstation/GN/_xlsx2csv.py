# -*- coding: utf-8 -*-
"""将 GN OPC_UA 协议 xlsx 转换为 opcua csv（与 opcua_gn1.3.5.csv 同格式）"""
import sys
import pandas as pd

DATATYPE_MAP = {"Int16": "INT16", "Int32": "INT32", "Double": "DOUBLE"}


def format_module_cmd(text: str) -> str:
    """把 col0 的设备模块指令块转成一行：'名称: 1=xx;2=yy;...'"""
    parts = [p.strip() for p in str(text).split("\n") if p.strip()]
    if not parts:
        return ""
    name, items = parts[0], parts[1:]
    if not items:
        return name
    return f"{name}: {';'.join(items)}"


def convert(xlsx_path: str, csv_path: str) -> None:
    df = pd.read_excel(xlsx_path, sheet_name="Sheet1", header=None)
    # 第 5 行(索引)起为数据；列: 0=设备模块 5=NodeId 6=点位名称 7=数据类型 8=读写 9=描述
    sub = df.iloc[5:, [0, 5, 6, 7, 8, 9]].reset_index(drop=True)
    sub.columns = ["module", "NodeId", "point", "dtype", "rw", "desc"]

    rows = []
    pending_module_cmd = None  # 等待写到本模块 *_CmdType 行的指令块
    for _, r in sub.iterrows():
        # 每遇到新的设备模块块，记录待附加的指令文本
        if pd.notna(r["module"]):
            pending_module_cmd = format_module_cmd(r["module"])

        node_id = str(r["NodeId"]).strip() if pd.notna(r["NodeId"]) else ""
        if "OPC_UA" not in node_id:
            continue
        name = node_id.split(".")[-1].strip()
        if not name:
            continue

        dtype = DATATYPE_MAP.get(str(r["dtype"]).strip(), str(r["dtype"]).strip())
        if not dtype or dtype == "nan":
            dtype = "INT16"  # xlsx 未填数据类型时默认 INT16（沿用 1.3.5 约定）
        point = "" if pd.isna(r["point"]) else str(r["point"]).strip()
        desc = "" if pd.isna(r["desc"]) else str(r["desc"]).strip()

        # 设备模块指令列：默认取 col9 描述；遇到 *_CmdType 行则填入模块指令块
        module_cmd = desc
        if name.endswith("_CmdType") and pending_module_cmd:
            module_cmd = pending_module_cmd
            pending_module_cmd = None

        rows.append({
            "Name": name,
            "EnglishName": name,
            "NodeType": "VARIABLE",
            "DataType": dtype,
            "NodeLanguage": "English",
            "NodeId": node_id,
            "描述": point,
            "设备模块指令": module_cmd,
        })

    out = pd.DataFrame(rows, columns=[
        "Name", "EnglishName", "NodeType", "DataType",
        "NodeLanguage", "NodeId", "描述", "设备模块指令",
    ])
    out = out.drop_duplicates(subset="Name", keep="first")
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"写出 {csv_path}: {len(out)} 行")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
