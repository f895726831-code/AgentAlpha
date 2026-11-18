#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// 宏观 EDB 数据拉取 — 命令行生成器
//
// 由于 execSync 在 Windows 上的 shell 引号兼容性问题，
// 此脚本输出可直接在终端中执行的 Bash 命令。
//
// 用法:
//   npx tsx scripts/fetch-macro.ts              # 输出全部命令
//   npx tsx scripts/fetch-macro.ts --run        # 逐条执行（需 Git Bash）
//   npx tsx scripts/fetch-macro.ts --json       # JSON 输出
//   npx tsx scripts/fetch-macro.ts --markdown   # Markdown 表格输出

import { MACRO_INDICATORS, type MacroIndicator, judgeSignal } from '../src/data/macro-indicators.js';
import { resolve } from 'node:path';
import { homedir } from 'node:os';

// 优先使用环境变量，否则回退到用户级技能安装目录（~/.agents/skills）
const CLI_PATH = process.env.WIND_MCP_CLI_PATH
  ?? resolve(homedir(), '.agents', 'skills', 'wind-mcp-skill', 'scripts', 'cli.mjs');

interface EdbResult {
  meta: { code: string; name: string; unit: string; freq: string };
  date: string[];
  value: number[];
}

interface FetchResult {
  indicator: MacroIndicator;
  data: EdbResult | null;
  error?: string;
}

function buildCommand(indicator: MacroIndicator): string {
  const params = JSON.stringify({
    executionMode: 'searchFetch',
    question: indicator.query,
    observation: '3',
  });
  return `node "${CLI_PATH}" call economic_data natural_language_get_edb_data '${params}'`;
}

function formatValue(value: number, unit: string): string {
  if (unit === '%') return `${value.toFixed(1)}%`;
  if (unit === '倍') return `${value.toFixed(2)}x`;
  if (unit === '亿元' && Math.abs(value) >= 10000) {
    return `${(value / 10000).toFixed(2)}万亿`;
  }
  if (unit === '亿元') return `${value.toFixed(0)}亿`;
  return `${value}${unit}`;
}

function main() {
  const args = process.argv.slice(2);
  const jsonOut = args.includes('--json');
  const mdOut = args.includes('--markdown');
  const run = args.includes('--run');

  if (run) {
    console.error('Fetching macro indicators (sequential, ~30s each)...');
    console.error('Use the Bash tool or run these commands manually in Git Bash.\n');
  }

  for (const ind of MACRO_INDICATORS) {
    const cmd = buildCommand(ind);
    if (run) {
      console.error(`  ${ind.name}...`);
      try {
        const { execSync } = require('child_process');
        const stdout = execSync(cmd, {
          encoding: 'utf-8',
          timeout: 30_000,
          stdio: ['ignore', 'pipe', 'pipe'],
          shell: 'C:/Program Files/Git/bin/bash.exe',
        });
        const response = JSON.parse(stdout);
        const text = response?.content?.[0]?.text;
        if (text) {
          const parsed = JSON.parse(text);
          const items: EdbResult[] = parsed?.data?.data ?? [];
          const match = items.find((item) => item.meta.code === ind.code) ?? items[0];
          if (match) {
            const vals = match.value;
            const latest = vals[vals.length - 1];
            const signal = judgeSignal(ind, latest);
            const emoji = signal === 'bullish' ? '🟢' : signal === 'bearish' ? '🔴' : '🟡';
            console.log(`${emoji} ${ind.name}: ${formatValue(latest, match.meta.unit)}`);
            continue;
          }
        }
        console.log(`⚠ ${ind.name}: no data`);
      } catch (err: any) {
        console.log(`⚠ ${ind.name}: ${err.message?.slice(0, 100)}`);
      }
    } else {
      console.log(cmd);
    }
  }

  if (mdOut && !run) {
    console.log('\n# 拉取后手动生成 Markdown 表格');
    console.log('| 指标 | 最新值 | 前值 | 趋势 | 信号 |');
    console.log('|---|---|---|---|---|');
    for (const ind of MACRO_INDICATORS) {
      console.log(`| ${ind.name} | — | — | — | — |`);
    }
  }
}

main();