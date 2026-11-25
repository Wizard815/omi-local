#!/usr/bin/env node
// Drive ALLGlance's own Omi connector (gateway/src/adapters/OmiAdapter.js) against Omi
// (for peers_demo.py). Standard library only: the adapter uses fetch and its own MCP client.
//
//   node allglance_omi_probe.mjs <ALLGlance checkout> <omi url> <omi mcp key> <date>
//
// Prints one JSON object: the omi_* tools it offers, allglance_get_day's Omi summary for
// <date> (daySummary), and what omi_get_action_items / omi_search_conversations return.
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [repo, url, token, date] = process.argv.slice(2);
const { OmiAdapter } = await import(pathToFileURL(path.join(repo, 'gateway/src/adapters/OmiAdapter.js')).href);
const adapter = new OmiAdapter({ roster: null, config: { omi: { enabled: true, url, token, tools: 'core' } } });
const tools = await adapter.dynamicTools();
const byName = Object.fromEntries(tools.map((t) => [t.name, t]));
const call = async (name, args) => {
  const out = await byName[name].handler(args);
  const text = out?.content?.find?.((c) => c.type === 'text')?.text;
  return out?.structuredContent ?? (text ? JSON.parse(text) : out);
};
const result = {
  tools: tools.map((t) => t.name).sort(),
  day: await adapter.daySummary(date),
  action_items: await call('omi_get_action_items', {}),
  search: await call('omi_search_conversations', { query: 'plumber' }),
};
console.log('\n' + JSON.stringify(result));
