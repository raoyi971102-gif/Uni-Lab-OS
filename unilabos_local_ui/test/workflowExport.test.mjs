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
  const tempFile = join(tempDir, 'workflowDraft.mjs');
  await writeFile(tempFile, transpiled.outputText, 'utf8');
  return import(tempFile);
}

const { createImportedDraft, createWorkflowRequest } = await importTypeScriptModule(
  new URL('../src/workflowDraft.ts', import.meta.url),
);

const actions = [
  {
    method: 'move_plate',
    label: '移动孔板',
    description: '移动孔板到目标位置',
    device_id: 'robot',
    params: [{ name: 'position', type: 'integer', default: 1 }],
  },
];

const draft = createWorkflowRequest(
  'roundtrip',
  [
    {
      id: 'node_1',
      position: { x: 24, y: 48 },
      data: {
        deviceId: 'robot',
        method: 'move_plate',
        label: '移动孔板',
        description: '移动孔板到目标位置',
        params: { position: 9 },
      },
    },
  ],
  [],
);

const imported = createImportedDraft(draft, actions, { autoLayout: false });
assert.equal(imported.name, 'roundtrip');
assert.equal(imported.nodes[0].id, 'node_1');
assert.deepEqual(imported.nodes[0].position, { x: 24, y: 48 });
assert.equal(imported.nodes[0].data.deviceId, 'robot');
assert.equal(imported.nodes[0].data.params.position, 9);
