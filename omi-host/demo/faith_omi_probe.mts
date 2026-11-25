// Drive FAITH's own generic MCP connector (server/src/connectors/mcp.ts) against Omi, with
// the faith.yaml connector entry from omi-host/examples/faith/README.md (for peers_demo.py).
//
//   cd <FAITH server with node_modules> && npx tsx <this file> <FAITH server src> <connector.yaml> <calls.json>
//
// Env: OMI_URL, OMI_MCP_KEY_WIZARD, OMI_MCP_KEY_PARTNER (one Omi MCP key per person; no
// household key). Prints one JSON object: the tools each view gets, and each call's answer.
import { readFileSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [src, connectorYaml, callsFile] = process.argv.slice(2);
const load = (rel: string) => import(pathToFileURL(path.join(src, rel)).href);
// FAITH's own yaml package (this file lives outside FAITH's tree).
const { parse } = await import(pathToFileURL(createRequire(path.join(src, "config.ts")).resolve("yaml")).href);
const { parseConfig } = await load("config.ts");
const { McpConnector } = await load("connectors/mcp.ts");

const connector = parse(readFileSync(connectorYaml, "utf8"));
const cfg = parseConfig({
  name: "Faith",
  model: { base_url: "http://127.0.0.1:9/v1", model: "test" },
  people: [{ id: "wizard", name: "Wizard", role: "admin" }, { id: "partner", name: "Partner", role: "adult" }],
  connectors: [connector],
}, process.env);
const omi = new McpConnector(cfg.connectors[0], process.env);
const person = (id: string) => ({ scope: { kind: "person", person: cfg.people.find((p: any) => p.id === id) }, channel: "api", label: id });
const household = { scope: { kind: "household" }, channel: "hub", label: "household" };

const views: Record<string, any> = { household, wizard: person("wizard"), partner: person("partner") };
const out: any = { tools: {}, writes: {}, calls: [] };
const toolsOf: Record<string, any[]> = {};
for (const [name, caller] of Object.entries(views)) {
  toolsOf[name] = await omi.tools(caller);
  out.tools[name] = toolsOf[name].map((t) => t.name).sort();
  out.writes[name] = toolsOf[name].filter((t) => t.write).map((t) => t.name).sort();
}
for (const call of JSON.parse(readFileSync(callsFile, "utf8"))) {
  const tool = toolsOf[call.view].find((t) => t.name === call.tool);
  out.calls.push({ ...call, result: tool ? await tool.run(call.args ?? {}) : { error: "not offered in this view" } });
}
console.log("\n" + JSON.stringify(out));
