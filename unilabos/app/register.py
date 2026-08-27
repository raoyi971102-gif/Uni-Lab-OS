import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from unilabos.utils.log import logger
from unilabos.utils.tools import normalize_json as _normalize_device


# 资源注册表包含大量带孔位信息的 config_info，一次性上传会让 gzip 请求体超过
# 服务端可稳定解析的大小。固定分批可将单次请求控制在较小范围内。
_RESOURCE_REGISTRY_BATCH_SIZE = 50


def _iter_batches(items: Iterable[Dict[str, Any]], batch_size: int) -> Iterable[List[Dict[str, Any]]]:
    """按固定大小切分注册表条目。"""
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")

    batch: List[Dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _registry_response_succeeded(response: Any) -> bool:
    """同时检查 HTTP 状态和响应体业务码。"""
    if response.status_code not in (200, 201):
        return False
    try:
        data = response.json()
    except Exception:
        return False
    return data.get("code", 0) == 0


def register_devices_and_resources(lab_registry, gather_only=False) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    注册设备和资源到服务器（仅支持HTTP）
    """

    from unilabos.app.web.client import http_client

    logger.info("[UniLab Register] 开始注册设备和资源...")

    devices_to_register = {}
    for device_info in lab_registry.obtain_registry_device_info():
        devices_to_register[device_info["id"]] = _normalize_device(device_info)
        logger.trace(f"[UniLab Register] 收集设备: {device_info['id']}")

    resources_to_register = {}
    for resource_info in lab_registry.obtain_registry_resource_info():
        resources_to_register[resource_info["id"]] = resource_info
        logger.trace(f"[UniLab Register] 收集资源: {resource_info['id']}")

    if gather_only:
        return devices_to_register, resources_to_register

    if devices_to_register:
        try:
            start_time = time.time()
            response = http_client.resource_registry(
                {"resources": list(devices_to_register.values())},
                tag="device_registry",
            )
            cost_time = time.time() - start_time
            res_data = response.json() if response.status_code == 200 else {}
            skipped = res_data.get("data", {}).get("skipped", False)
            if skipped:
                logger.info(
                    f"[UniLab Register] 设备注册跳过（内容未变化）"
                    f" {len(devices_to_register)} 个 {cost_time:.3f}s"
                )
            elif response.status_code in [200, 201]:
                logger.info(f"[UniLab Register] 成功注册 {len(devices_to_register)} 个设备 {cost_time:.3f}s")
            else:
                logger.error(f"[UniLab Register] 设备注册失败: {response.status_code}, {response.text} {cost_time:.3f}s")
        except Exception as e:
            logger.error(f"[UniLab Register] 设备注册异常: {e}")

    if resources_to_register:
        try:
            start_time = time.time()
            resources = list(resources_to_register.values())
            batches = list(_iter_batches(resources, _RESOURCE_REGISTRY_BATCH_SIZE))
            registered_count = 0
            failed_response = None

            for batch_index, batch in enumerate(batches, start=1):
                response = http_client.resource_registry(
                    {"resources": batch},
                    tag="resource_registry",
                )
                if not _registry_response_succeeded(response):
                    failed_response = response
                    logger.error(
                        f"[UniLab Register] 资源注册批次失败: "
                        f"{batch_index}/{len(batches)}, {response.status_code}, {response.text}"
                    )
                    break
                registered_count += len(batch)
                logger.info(
                    f"[UniLab Register] 资源注册批次完成: "
                    f"{batch_index}/{len(batches)}，本批 {len(batch)} 个"
                )

            cost_time = time.time() - start_time
            if failed_response is None:
                logger.info(f"[UniLab Register] 成功注册 {registered_count} 个资源 {cost_time:.3f}s")
            else:
                logger.error(
                    f"[UniLab Register] 资源注册中止: 已完成 {registered_count}/{len(resources)} 个，"
                    f"耗时 {cost_time:.3f}s"
                )
        except Exception as e:
            logger.error(f"[UniLab Register] 资源注册异常: {e}")
