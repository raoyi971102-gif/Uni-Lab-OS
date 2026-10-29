"""检查图片资产完整性、逐型号映射以及注册表引用；无需联网。"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    folder = root / 'unilabos/registry/device_images/ivi'
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    registry = json.loads((root / 'unilabos/registry/devices/ivi_family_models.yaml').read_text(encoding='utf-8'))
    assets = {a['id']: a for a in manifest['assets']}
    assert len(assets) == len(manifest['assets']), '图片 ID 重复'
    assert len({a['sha256'] for a in assets.values()}) == len(assets), '重复图片未去重'
    for asset in assets.values():
        path = (folder / asset['file']).resolve()
        assert path.is_relative_to(folder.resolve()), '图片路径越界'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset['sha256'], f'校验失败：{path}'
        assert path.stat().st_size == asset['bytes']
        assert asset['page_url'].startswith('https://') and asset['image_url'].startswith('https://')
    rows = manifest['models']
    assert len(rows) == len(registry) == 699
    assert {r['registry_id'] for r in rows} == set(registry)
    used = set()
    for row in rows:
        entry = registry[row['registry_id']]
        assert row['model'] == entry['init_param_enforce']['model']
        if row['match_kind'] == 'missing':
            assert not entry.get('icon'), '缺图型号不得虚构图标'
            continue
        asset = assets[row['asset_id']]
        used.add(asset['id'])
        assert row['file'] == asset['file']
        assert entry['icon'].endswith('/unilabos/registry/device_images/ivi/' + asset['file'])
        evidence = entry['image_evidence']
        assert evidence['match_kind'] == row['match_kind']
        assert evidence['sha256'] == asset['sha256']
        if row['match_kind'] == 'model_source_image':
            assert row['model'] == asset['pictured_model'], '具体型号配图不一致'
        else:
            assert row['match_kind'] == 'series_reference'
            assert '系列参考图' in evidence['note'] and '系列参考图' in entry['description']
    assert used == set(assets), '存在未使用的图片资产'
    summary = dict(collections.Counter(r['match_kind'] for r in rows))
    assert summary == manifest['summary']
    print(json.dumps({'assets': len(assets), 'models': len(rows), 'matches': summary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
