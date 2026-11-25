#!/usr/bin/env python3
"""Drive hermes-agent's own MCP client against Omi's MCP server (for hermes_demo.py).

Run with the Python of a hermes-agent install (its venv has the `mcp` SDK and Hermes'
tools/ package), from the hermes-agent source directory or with it on PYTHONPATH:

  HERMES_HOME=<scratch> <hermes-venv>/bin/python hermes_mcp_probe.py \
      --url http://127.0.0.1:8000/v1/mcp --key omi_mcp_... --calls calls.json

It registers one server, ``omi``, exactly as ``mcp_servers.omi`` in Hermes' config.yaml
does (url + Authorization header, Streamable HTTP), lists the tools Hermes registered for it
(``mcp__omi__<tool>``), then makes each call in --calls through Hermes' tool dispatcher
(model_tools.handle_function_call), the path a model's tool call takes. Prints one JSON
object: {"tools": [...], "results": [{"tool", "args", "result"}...]}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--calls', required=True, help='JSON file: [{"tool": "search_conversations", "args": {...}}]')
    parser.add_argument('--server', default='omi')
    args = parser.parse_args()

    sys.path.insert(0, os.getcwd())
    from model_tools import handle_function_call
    from tools.mcp_tool_discovery import register_mcp_servers

    config = {
        args.server: {
            'url': args.url,
            'headers': {'Authorization': f'Bearer {args.key}'},
            'timeout': 120,
            'connect_timeout': 30,
            'trust': 'full',
        }
    }
    names = register_mcp_servers(config)
    prefix = f'mcp__{args.server}__'
    tools = sorted(n for n in names if n.startswith(prefix))
    results = []
    with open(args.calls) as handle:
        calls = json.load(handle)
    for call in calls:
        raw = handle_function_call(prefix + call['tool'], call.get('args') or {})
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = raw
        results.append({'tool': call['tool'], 'args': call.get('args') or {}, 'result': parsed})
    print('\n' + json.dumps({'tools': tools, 'results': results}, default=str))
    sys.stdout.flush()
    os._exit(0)  # the MCP loop thread would otherwise keep the process alive


if __name__ == '__main__':
    sys.exit(main())
