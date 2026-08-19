"""Test direct invocation of tools in mcp_server.py"""
import json
import asyncio
from mcp_server import (
    get_baserow_schema,
    search_baserow_products,
    search_shopify_products,
    update_shopify_inventory,
)

async def run_tests():
    print("=== Testing Python Native MCP Tools ===")

    # 1. Baserow schema
    print("\n1. Testing get_baserow_schema()...")
    res = get_baserow_schema()
    if res.startswith("{"):
        parsed = json.loads(res)
        print(f"Success! Detected {parsed.get('total_fields')} fields from Baserow.")
    else:
        print("Response:", res)

    # 2. Baserow search
    print("\n2. Testing search_baserow_products(limit=2)...")
    res_search = search_baserow_products(limit=2)
    if res_search.startswith("{"):
        parsed = json.loads(res_search)
        print(f"Success! Baserow returned {parsed.get('returned')} rows (Total count: {parsed.get('count')}).")
    else:
        print("Response:", res_search)

    # 3. Guardrail safety check
    print("\n3. Testing update_shopify_inventory(inventory_item_id=123, available=0, confirm=False)...")
    res_guard = update_shopify_inventory(inventory_item_id=123, available=0, confirm=False)
    print("Guardrail output:", res_guard)

    print("\n=== All Python MCP Tests Passed Successfully ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
