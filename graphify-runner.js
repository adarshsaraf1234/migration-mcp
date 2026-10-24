#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { existsSync, realpathSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.dirname(fileURLToPath(import.meta.url));
const defaultTarget = path.resolve(projectRoot, '../legacy-demo-app');

export function buildCommands(target) {
  return [
    ['extract', target, '--code-only', '--no-cluster'],
    ['cluster-only', target, '--no-label']
  ];
}

export function run(target = defaultTarget) {
  const absoluteTarget = path.resolve(target);
  if (!existsSync(absoluteTarget)) {
    throw new Error(`Target repository does not exist: ${absoluteTarget}`);
  }
  if (!existsSync(path.join(absoluteTarget, 'package.json'))) {
    throw new Error(`Target does not look like a repository (package.json missing): ${absoluteTarget}`);
  }
  if (realpathSync(absoluteTarget) === realpathSync(projectRoot)) {
    throw new Error('Refusing to analyze the product repository; pass a separate target repository.');
  }

  for (const args of buildCommands(absoluteTarget)) {
    const result = spawnSync('graphify', args, { cwd: projectRoot, encoding: 'utf8' });
    if (result.stdout) process.stdout.write(result.stdout);
    if (result.stderr) process.stderr.write(result.stderr);
    if (result.error) {
      throw new Error(`Could not run Graphify: ${result.error.message}`);
    }
    if (result.status !== 0) {
      throw new Error(`Graphify ${args[0]} failed with exit code ${result.status}`);
    }
  }

  const outputDir = path.join(absoluteTarget, 'graphify-out');
  for (const artifact of ['graph.json', 'GRAPH_REPORT.md', 'graph.html']) {
    if (!existsSync(path.join(outputDir, artifact))) {
      throw new Error(`Graphify completed but expected artifact is missing: ${path.join(outputDir, artifact)}`);
    }
  }
  process.stdout.write(`Verified Graphify artifacts in ${outputDir}\n`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    run(process.argv[2] ?? defaultTarget);
  } catch (error) {
    console.error(`Graphify runner: ${error.message}`);
    process.exitCode = 1;
  }
}
