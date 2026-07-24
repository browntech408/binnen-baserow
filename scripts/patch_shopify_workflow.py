"""Patch Binnen Shopify n8n workflow with image budget logic."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNIPPET = ROOT / "scripts" / "build_n8n_workflow_snippet.py"
WORKFLOW = ROOT / "output" / "Binnen Design Baserow → Shopify Sync.json"

ns: dict = {}
exec(SNIPPET.read_text(encoding="utf-8").split("import json")[0], ns)
build_code = ns["BUILD"].strip()
expand_code = ns["EXPAND"].strip()
collect_code = ns["COLLECT"].strip()
filter_code = ns["FILTER"].strip()

wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
remove_names = {"Needs HEAD Check", "HEAD Image Size", "Resolve Safe URL"}
wf["nodes"] = [n for n in wf["nodes"] if n["name"] not in remove_names]

for n in wf["nodes"]:
    if n["name"] == "Build Product JSON":
        n["parameters"]["jsCode"] = build_code
    elif n["name"] == "Expand Image Items":
        n["parameters"]["jsCode"] = expand_code
    elif n["name"] == "Collect Images For Shopify":
        n["parameters"]["jsCode"] = collect_code
    elif n["name"] == "Filter New Products":
        n["parameters"]["jsCode"] = filter_code
    elif n["name"] == "Download Baserow Image":
        n["position"] = [3040, -32]
    elif n["name"] == "Can Download":
        n["position"] = [2816, 48]
    elif n["name"] == "Collect Images For Shopify":
        n["position"] = [3616, 48]
    elif n["name"] == "Create Shopify Product":
        n["position"] = [3840, 48]
    elif n["name"] == "Update Baserow":
        n["position"] = [4064, 48]

if not any(n["name"] == "Compress Image" for n in wf["nodes"]):
    wf["nodes"].append(
        {
            "parameters": {
                "operation": "resize",
                "width": 1600,
                "height": 1600,
                "resizeOption": "maximumArea",
                "options": {"format": "jpeg", "quality": 80},
            },
            "type": "n8n-nodes-base.editImage",
            "typeVersion": 1,
            "position": [3392, -32],
            "id": "b7c8d9e0-0000-1111-2222-333344445555",
            "name": "Compress Image",
        }
    )

if not any(n["name"] == "Needs Compress" for n in wf["nodes"]):
    wf["nodes"].append(
        {
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "strict",
                        "version": 2,
                    },
                    "conditions": [
                        {
                            "id": "needs-compress",
                            "leftValue": "={{ Number($binary.data.fileSize || 0) }}",
                            "rightValue": 900000,
                            "operator": {"type": "number", "operation": "gt"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [3264, 48],
            "id": "i1f2c3o4-4444-5555-6666-777788889999",
            "name": "Needs Compress",
        }
    )

wf["connections"] = {
    "When clicking 'Execute workflow'": {
        "main": [[{"node": "Get Baserow Products", "type": "main", "index": 0}]]
    },
    "Get Baserow Products": {
        "main": [[{"node": "Get Shopify Products", "type": "main", "index": 0}]]
    },
    "Get Shopify Products": {
        "main": [[{"node": "Filter New Products", "type": "main", "index": 0}]]
    },
    "Filter New Products": {
        "main": [[{"node": "Loop Over Items", "type": "main", "index": 0}]]
    },
    "Loop Over Items": {
        "main": [[], [{"node": "Build Product JSON", "type": "main", "index": 0}]]
    },
    "Build Product JSON": {
        "main": [[{"node": "Expand Image Items", "type": "main", "index": 0}]]
    },
    "Expand Image Items": {
        "main": [[{"node": "Can Download", "type": "main", "index": 0}]]
    },
    "Can Download": {
        "main": [
            [{"node": "Download Baserow Image", "type": "main", "index": 0}],
            [{"node": "Collect Images For Shopify", "type": "main", "index": 0}],
        ]
    },
    "Download Baserow Image": {
        "main": [[{"node": "Needs Compress", "type": "main", "index": 0}]]
    },
    "Needs Compress": {
        "main": [
            [{"node": "Compress Image", "type": "main", "index": 0}],
            [{"node": "Collect Images For Shopify", "type": "main", "index": 0}],
        ]
    },
    "Compress Image": {
        "main": [[{"node": "Collect Images For Shopify", "type": "main", "index": 0}]]
    },
    "Collect Images For Shopify": {
        "main": [[{"node": "Create Shopify Product", "type": "main", "index": 0}]]
    },
    "Create Shopify Product": {
        "main": [[{"node": "Update Baserow", "type": "main", "index": 0}]]
    },
    "Update Baserow": {
        "main": [[{"node": "Loop Over Items", "type": "main", "index": 0}]]
    },
}

WORKFLOW.write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Patched {WORKFLOW} ({len(wf['nodes'])} nodes)")
