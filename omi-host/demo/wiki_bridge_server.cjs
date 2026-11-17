#!/usr/bin/env node
// The family wiki for omi-host/demo/integrations_demo.py: the REAL LLMWIKI MCP bridge plugin
// (Wizard815/LLM-will-remember-this, .obsidian/plugins/llmwiki-mcp-bridge/main.js), its own
// /family-mcp handler (serveFamilyMcp), over the in-memory two-person vault its own tests use
// (scripts/family-harness.cjs: Wizard and Sam, member tokens "t-wizard" and "t-sam").
//
//   node wiki_bridge_server.cjs --repo <LLM-will-remember-this checkout> --host <ip> --port <n>
//
// POST /family-mcp   the plugin's handler, unchanged (Bearer family token, stateless JSON-RPC)
// GET  /__vault      the demo's window into the vault: every file path and its text
"use strict";
const http = require("node:http");
const path = require("node:path");

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, i, all) => (value.startsWith("--") ? [...pairs, [value.slice(2), all[i + 1]]] : pairs), []),
);
const repo = args.repo || process.env.LLMWIKI_REPO;
if (!repo) {
  console.error("--repo <LLM-will-remember-this checkout> is required");
  process.exit(2);
}
const { setup } = require(path.join(path.resolve(repo), "scripts", "family-harness.cjs"));
const { vault, plugin } = setup();

/** The Express-like response the plugin's routes are written against (Local REST API). */
function adapt(res) {
  const r = {
    headersSent: false,
    status(code) { res.statusCode = code; return r; },
    set(name, value) { res.setHeader(name, value); return r; },
    json(body) { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(body)); r.headersSent = true; return r; },
    end() { res.end(); r.headersSent = true; return r; },
  };
  return r;
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://wiki");
  if (url.pathname === "/family-mcp") {
    plugin.serveFamilyMcp(req, adapt(res)).catch((e) => plugin.fail(adapt(res), 500, e));
    return;
  }
  if (url.pathname === "/__vault" && req.method === "GET") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(Object.fromEntries(vault.store)));
    return;
  }
  res.statusCode = 404;
  res.end();
});
server.listen(Number(args.port || 27123), args.host || "127.0.0.1", () => {
  console.log(`llmwiki bridge (real plugin handler) on http://${args.host || "127.0.0.1"}:${args.port || 27123}/family-mcp`);
});
