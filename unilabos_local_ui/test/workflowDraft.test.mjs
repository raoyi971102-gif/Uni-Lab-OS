import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import ts from 'typescript';

async function importTypeScriptModule(path) {
  const source = await readFile(path, 'utf8');
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.ES2022,
      target: ts.ScriptTarget.ES2020,
      strict: true,
    },
  });
  const tempDir = await mkdtemp(join(tmpdir(), 'workflow-draft-test-'));
  const tempFile = join(tempDir, 'workflowDraft.mjs');
  await writeFile(tempFile, transpiled.outputText, 'utf8');
  return import(tempFile);
}

const { createWorkflowRequest, workflowDraftKey } = await importTypeScriptModule(
  new URL('../src/workflowDraft.ts', import.meta.url),
);
const { collectOpcChanges, formatOpcValue } = await importTypeScriptModule(
  new URL('../src/opcChanges.ts', import.meta.url),
);
const { formatUiError, buildWorkspaceSummary, groupActionsByDevice } = await importTypeScriptModule(
  new URL('../src/uiState.ts', import.meta.url),
);

const baseNodes = [
  {
    id: 'load',
    position: { x: 0, y: 0 },
    data: {
      method: 'pick_well_plate_from_loading_rack',
      label: '从上料架取孔板',
      description: '取孔板',
      params: { position: 1 },
      runStatus: 'idle',
    },
  },
];
const runningNodes = [
  {
    ...baseNodes[0],
    data: {
      ...baseNodes[0].data,
      runStatus: 'running',
    },
  },
];
const edges = [{ id: 'e1', source: 'load', target: 'unload' }];

assert.deepEqual(createWorkflowRequest('ai4c', baseNodes, edges), {
  name: 'ai4c',
  nodes: [
    {
      id: 'load',
      position: { x: 0, y: 0 },
      data: {
        method: 'pick_well_plate_from_loading_rack',
        label: '从上料架取孔板',
        description: '取孔板',
        params: { position: 1 },
      },
    },
  ],
  edges: [{ id: 'e1', source: 'load', target: 'unload' }],
});
assert.equal(
  workflowDraftKey('ai4c', baseNodes, edges),
  workflowDraftKey('ai4c', runningNodes, edges),
  '运行状态变化不应改变 workflow 草稿指纹',
);

const changedParamNodes = [
  {
    ...baseNodes[0],
    data: {
      ...baseNodes[0].data,
      params: { position: 2 },
    },
  },
];
assert.notEqual(
  workflowDraftKey('ai4c', baseNodes, edges),
  workflowDraftKey('ai4c', changedParamNodes, edges),
  '参数变化应触发 workflow 草稿重新校验',
);

const opcRowsWhileRunning = collectOpcChanges([
  {
    sequence: 1,
    message: 'OPC状态采样: 2 个变量',
    level: 'info',
    scope: 'node',
    node_id: 'node_1',
    detail: {
      before: {
        S06允许加工: {
          name: 'S06允许加工',
          label: 'S06允许加工',
          display_name: 'S06允许加工',
          node_id: 'ns=2;i=269',
          value: { success: true, value: true, node_id: 'ns=2;i=269' },
          value_goal: { success: true, value: false, node_id: 'ns=2;i=269' },
        },
        S06加工完成: {
          name: 'S06加工完成',
          label: 'S06加工完成',
          display_name: 'S06加工完成',
          node_id: 'ns=2;i=270',
          value: { success: true, value: false, node_id: 'ns=2;i=270' },
        },
      },
    },
  },
]);
assert.equal(opcRowsWhileRunning.length, 2, '运行中应显示执行前采样到的等待变量');
assert.equal(opcRowsWhileRunning[0].valueBegin.value, true);
assert.equal(opcRowsWhileRunning[0].valueGoal.value, false);
assert.equal(opcRowsWhileRunning[0].valueEnd, undefined);

assert.equal(formatOpcValue({ success: true, value: false, node_id: 'ns=2;i=270' }), 'false');
assert.equal(formatOpcValue({ success: false, error: 'bad node' }), 'bad node');

assert.equal(
  formatUiError(new TypeError('Failed to fetch'), '运行 workflow'),
  '运行 workflow 失败：无法连接本地调试服务，请确认 workflow_ui 后端仍在运行，且当前页面与后端端口一致。',
);
assert.equal(formatUiError(new Error('workflow 不能包含环'), '校验流程'), '校验流程失败：workflow 不能包含环');

const summary = buildWorkspaceSummary({
  nodes: [
    { data: { deviceId: 'szlab_mixer_robot', runStatus: 'success' } },
    { data: { deviceId: 'szlab_mixer_stirrer', runStatus: 'running' } },
    { data: { deviceId: 'szlab_mixer_photoshotting', runStatus: 'idle' } },
  ],
  edges: [{}, {}],
  opcChangeCount: 6,
  runStatus: 'running',
});
assert.deepEqual(summary, {
  totalNodes: 3,
  totalEdges: 2,
  runningNodes: 1,
  completedNodes: 1,
  deviceCount: 3,
  opcChangeCount: 6,
  runStatusText: '运行中',
});

assert.deepEqual(
  groupActionsByDevice([
    {
      method: 'submit_place_to_magnetic_stirrer',
      label: '放置到 S04 磁搅位',
      description: '放置到 S04 磁搅位',
      device_id: 'szlab_mixer_robot',
    },
    {
      method: 'run_stirring',
      label: '执行 S04 磁搅加工',
      description: '执行 S04 磁搅加工',
      device_id: 'szlab_mixer_stirrer',
    },
    {
      method: 'take_photo',
      label: '拍照并保存结果',
      description: '拍照并保存结果',
      device_id: 'szlab_mixer_photoshotting',
    },
  ]),
  [
    {
      id: 'szlab_mixer_robot',
      title: '机械臂转运',
      device: 'szlab_mixer_robot',
      actions: [
        {
          method: 'submit_place_to_magnetic_stirrer',
          label: '放置到 S04 磁搅位',
          description: '放置到 S04 磁搅位',
          device_id: 'szlab_mixer_robot',
        },
      ],
    },
    {
      id: 'process_devices',
      title: '设备工艺',
      device: 'S04 / S05',
      actions: [
        {
          method: 'run_stirring',
          label: '执行 S04 磁搅加工',
          description: '执行 S04 磁搅加工',
          device_id: 'szlab_mixer_stirrer',
        },
        {
          method: 'take_photo',
          label: '拍照并保存结果',
          description: '拍照并保存结果',
          device_id: 'szlab_mixer_photoshotting',
        },
      ],
    },
  ],
  '动作面板应按机械臂和设备工艺分层显示',
);
