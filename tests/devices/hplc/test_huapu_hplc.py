import inspect
from unittest.mock import Mock

import pytest
import requests

from unilabos.devices.hplc.huapu_hplc import HuapuHPLC


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _driver(*responses, **kwargs) -> HuapuHPLC:
    driver = HuapuHPLC(
        base_url="http://huapu.test:8001",
        default_project_id=7,
        default_instrument_id=11,
        **kwargs,
    )
    driver.session.post = Mock(side_effect=[FakeResponse(response) for response in responses])
    return driver


def test_action_signatures_expose_method_names_instead_of_ids() -> None:
    create_params = inspect.signature(HuapuHPLC.create_sequence).parameters
    run_params = inspect.signature(HuapuHPLC.run_sequence).parameters
    sample_params = inspect.signature(HuapuHPLC.run_sample).parameters

    assert "sequence_method_name" in create_params
    assert "sequence_method_id" not in create_params
    assert {"process_method_name", "report_method_name"} <= set(run_params)
    assert "process_method_id" not in run_params
    assert "report_method_id" not in run_params
    assert {
        "sequence_method_name",
        "process_method_name",
        "report_method_name",
    } <= set(sample_params)


def test_create_sequence_queries_name_then_posts_resolved_id() -> None:
    driver = _driver(
        {
            "code": 0,
            "msg": "成功",
            "data": [
                {"id": 21, "methodName": "参考方法 A"},
                {"id": 22, "methodName": "参考方法 B"},
            ],
        },
        {"code": 0, "msg": "成功", "data": None},
    )

    result = driver.create_sequence(
        proc_inst_id="flow-1",
        sequence_method_name="参考方法 B",
        sample_name="样品 1",
    )

    assert result["success"] is True
    calls = driver.session.post.call_args_list
    assert calls[0].args[0].endswith("/project/findSequenceMethodList")
    assert calls[0].kwargs["json"] == {"projectId": 7}
    assert calls[1].args[0].endswith("/project/addSequenceMethod")
    payload = calls[1].kwargs["json"]
    assert payload["sequenceMethodId"] == 22
    assert payload["projectId"] == 7
    assert payload["procInstId"] == "flow-1"


def test_run_sequence_queries_process_and_report_names_before_post() -> None:
    driver = _driver(
        [{"id": "31", "methodName": "处理方法 A"}],
        {"code": 0, "msg": "成功", "data": [{"id": 41, "methodName": "报告方法 A"}]},
        {"code": 0, "msg": "成功", "data": None},
    )

    result = driver.run_sequence(
        proc_inst_id="flow-2",
        process_method_name="处理方法 A",
        report_method_name="报告方法 A",
        shut_down=1,
    )

    assert result["success"] is True
    calls = driver.session.post.call_args_list
    assert calls[0].args[0].endswith("/project/findProcessMethodList")
    assert calls[1].args[0].endswith("/project/findReportMethodList")
    assert calls[2].args[0].endswith("/project/runSequence")
    assert calls[2].kwargs["json"]["proMethodId"] == 31
    assert calls[2].kwargs["json"]["reportMethodId"] == 41


def test_default_method_name_is_also_resolved_through_query() -> None:
    driver = _driver(
        [{"id": 51, "methodName": "默认参考方法"}],
        {"code": 0, "msg": "成功", "data": None},
        default_sequence_method_name="默认参考方法",
    )

    driver.create_sequence(proc_inst_id="flow-default", sequence_method_name="")

    assert driver.session.post.call_args_list[1].kwargs["json"]["sequenceMethodId"] == 51


def test_unknown_method_name_raises_before_create_post() -> None:
    driver = _driver([{"id": 1, "methodName": "已有方法"}])

    with pytest.raises(ValueError, match="未找到参考序列方法名称 '不存在的方法'"):
        driver.create_sequence(proc_inst_id="flow-3", sequence_method_name="不存在的方法")

    assert driver.session.post.call_count == 1
    assert driver.data["status"] == "Error"


def test_duplicate_method_name_raises_before_run_post() -> None:
    driver = _driver(
        [
            {"id": 1, "methodName": "重复方法"},
            {"id": 2, "methodName": "重复方法"},
        ]
    )

    with pytest.raises(ValueError, match="处理方法名称 '重复方法' 不唯一"):
        driver.run_sequence(
            proc_inst_id="flow-4",
            process_method_name="重复方法",
            report_method_name="报告方法",
        )

    assert driver.session.post.call_count == 1


@pytest.mark.parametrize("error_code", [1, "CODE1"])
def test_business_error_code_raises_action_failure(error_code) -> None:
    driver = _driver(
        [{"id": 61, "methodName": "参考方法"}],
        {"code": error_code, "msg": "创建失败", "data": None},
    )

    with pytest.raises(ValueError, match=rf"创建失败 \(code={error_code}\)"):
        driver.create_sequence(proc_inst_id="flow-5", sequence_method_name="参考方法")

    assert driver.data["status"] == "Error"
    assert f"code={error_code}" in driver.data["message"]


def test_http_error_raises_action_failure() -> None:
    driver = _driver()
    driver.session.post.side_effect = requests.ConnectionError("连接被拒绝")

    with pytest.raises(ValueError, match="HTTP 请求失败: 连接被拒绝"):
        driver.list_projects()

    assert driver.data["status"] == "Error"


def test_run_sample_passes_all_method_names_to_composed_actions() -> None:
    driver = _driver()
    driver.create_sequence = Mock(return_value={"success": True})
    driver.run_sequence = Mock(return_value={"success": True})

    result = driver.run_sample(
        proc_inst_id="flow-6",
        sequence_method_name="参考方法",
        process_method_name="处理方法",
        report_method_name="报告方法",
        wait=False,
    )

    assert result["success"] is True
    assert driver.create_sequence.call_args.kwargs["sequence_method_name"] == "参考方法"
    assert driver.run_sequence.call_args.kwargs["process_method_name"] == "处理方法"
    assert driver.run_sequence.call_args.kwargs["report_method_name"] == "报告方法"
    assert driver.run_sequence.call_args.kwargs["project_id"] == 0
