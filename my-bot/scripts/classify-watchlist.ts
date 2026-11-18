#!/usr/bin/env node
// SPDX-License-Identifier: MIT
// 雪球自选股行业分类 — 读取 xueqiu_watchlist.json，按行业分类输出 Markdown
//
// 用法:
//   npx tsx scripts/classify-watchlist.ts
//   npx tsx scripts/classify-watchlist.ts --json   # JSON 输出
//
// 分类数据来源: data/sector-map.json (从 classify_watchlist.py 的 SECTOR_MAP 提取)

import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

interface Stock {
  code: string;
  name: string;
  type?: number;
}

interface SectorMap {
  [code: string]: string;
}

// ── 加载数据 ──────────────────────────────────────────────

function loadJSON<T>(filePath: string): T {
  return JSON.parse(readFileSync(filePath, 'utf-8')) as T;
}

const stocks: Stock[] = loadJSON(resolve(ROOT, 'xueqiu_watchlist.json'));
const SECTOR_MAP: SectorMap = loadJSON(resolve(ROOT, 'data', 'sector-map.json'));

// ── 名称分类（当 SECTOR_MAP 未覆盖时） ──────────────────

const KEYWORD_RULES: [string, string[]][] = [
  ['半导体', ['芯片', '半导', '晶圆', '硅', '存储', '封测', 'EDA', 'GPU', 'CPU', 'FPGA', '光刻', '刻蚀', '芯', '微电子']],
  ['消费电子', ['电子', '精密', '声学', '光学', '镜头', '屏幕', '显示', '面板', '天线', '连接器', '结构件', '家电']],
  ['新能源', ['光伏', '锂电', '电池', '储能', '逆变器', '新能', '风电', '充电', '氢能', '太阳']],
  ['汽车', ['汽车', '整车', '零部件', '轮胎', '模具', '轴承', '电驱', '智驾', '座舱', '激光雷达']],
  ['AI/软件', ['软件', '智能', 'AI', '人工', '云计算', '大模型', '安全', '网络', '互联', '数据']],
  ['医药', ['药', '医', '生物', '疫苗', '诊断', '器械', '基因', '细胞', '蛋白', '肽', '眼科', '体检', '健康']],
  ['金融', ['银行', '保险', '证券', '券商', '信托', '金融', '期货', '基金']],
  ['消费', ['酒', '食品', '饮料', '乳', '肉', '养殖', '餐饮', '零售', '服装', '家居', '旅游', '酒店', '免税', '水', '茶', '奶', '黄金']],
  ['通信', ['通信', '光纤', '光缆', '光模块', '基站', '射频', '卫星', '北斗', '导航']],
  ['军工', ['军工', '航天', '航空', '导弹', '雷达', '舰船', '兵器', '防务', '无人机', '战斗', '中航', '沈飞', '西飞', '船舶']],
  ['传媒', ['传媒', '广告', '游戏', '出版', '影视', '视频', '广电', '阅读', '版权', '营销', 'MCN', '教育']],
  ['地产/基建', ['地产', '建筑', '建材', '水泥', '玻璃', '装修', '园区', '开发', '施工', '建设']],
  ['有色/资源', ['黄金', '铜', '铝', '锂', '钴', '稀土', '矿', '钢', '有色', '资源', '石油', '石化', '化工', '化纤', '钛', '碳', '钨', '钼']],
  ['电力/能源', ['电力', '能源', '发电', '电网', '核电', '风电', '水电', '火电', '燃气', '供热', '煤炭', '煤']],
  ['运输/物流', ['航空', '航运', '港口', '铁路', '高速', '物流', '快递', '运输', '机场', '交通']],
  ['机器人', ['机器人', '伺服', '减速器', '自控', '自动化', '激光', '数控', '机床', '传感']],
  ['机械/装备', ['机械', '重工', '装备', '轴承', '电梯', '压缩机', '电机']],
];

function classifyByName(name: string): string | null {
  for (const [cat, kws] of KEYWORD_RULES) {
    for (const kw of kws) {
      if (name.includes(kw)) return cat;
    }
  }
  return null;
}

function isETF(code: string): boolean {
  const prefixes = ['510', '511', '512', '513', '515', '516', '517', '518', '588', '159', '560', '561', '562', '563'];
  return prefixes.some(p => code.startsWith(p));
}

// ── 分类逻辑 ──────────────────────────────────────────────

const categories = new Map<string, string[]>();

for (const stock of stocks) {
  const { code, name } = stock;
  let cat: string;

  if (SECTOR_MAP[code]) {
    cat = SECTOR_MAP[code];
  } else if (isETF(code)) {
    cat = 'ETF-指数基金';
  } else {
    cat = classifyByName(name) ?? '未分类';
  }

  if (!categories.has(cat)) categories.set(cat, []);
  categories.get(cat)!.push(`${code} ${name}`);
}

// ── 输出 ──────────────────────────────────────────────────

const jsonOut = process.argv.includes('--json');

if (jsonOut) {
  const result: Record<string, string[]> = {};
  const sorted = [...categories.keys()].sort();
  for (const cat of sorted) {
    result[cat] = categories.get(cat)!.sort();
  }
  console.log(JSON.stringify(result, null, 2));
} else {
  // Markdown 输出
  const sorted = [...categories.keys()].sort();
  let total = 0;
  for (const cat of sorted) {
    const items = categories.get(cat)!.sort();
    total += items.length;
    console.log(`## ${cat}（${items.length} 只）`);
    for (const item of items) {
      console.log(`- ${item}`);
    }
    console.log();
  }
  console.log(`> 合计: ${total} 只（共 ${stocks.length} 只，覆盖率 ${(total / stocks.length * 100).toFixed(1)}%）`);
}