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
  const tempDir = await mkdtemp(join(tmpdir(), 'workflow-export-test-'));
  const tempFile = join(tempDir, 'workflowExport.mjs');
  await writeFile(tempFile, transpiled.outputText, 'utf8');
  return import(tempFile);
}

const { createPseudoFlowJson } = await importTypeScriptModule(
  new URL('../src/workflowExport.ts', import.meta.url),
);

const nodes = [
  {
    id: 'stir',
    data: {
      label: '执行磁搅',
      method: 'run_stirring',
      deviceId: 'szlab_mixer_stirrer',
      params: { position: 1 },
    },
  },
  {
    id: 'pick',
    data: {
      label: '抓取孔板',
      method: 'submit_pick_from_stack',
      deviceId: 'szlab_mixer_robot',
      params: { stack: 'A', slot: 1 },
    },
  },
];

assert.deepEqual(createPseudoFlowJson('szlab_flow', nodes, [{ id: 'e1', source: 'pick', target: 'stir' }]), {
  name: 'szlab_flow',
  rules: [
    {
      name: 'szlab_flow',
      trigger: {
        node: '抓取孔板',
        value: true,
        edge: 'rising',
      },
      log_nodes: ['抓取孔板', '执行磁搅'],
      actions: [
        {
          action: {
            index: 1,
            node: '抓取孔板',
            workflow_node_id: 'pick',
            device_id: 'szlab_mixer_robot',
            method: 'submit_pick_from_stack',
            params: { stack: 'A', slot: 1 },
          },
        },
        {
          action: {
            index: 2,
            node: '执行磁搅',
            workflow_node_id: 'stir',
            device_id: 'szlab_mixer_stirrer',
            method: 'run_stirring',
            params: { position: 1 },
          },
        },
      ],
    },
  ],
});
