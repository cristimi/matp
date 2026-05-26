import asyncio
import json
from app.adapters.blofin import BlofinAdapter
import logging

logging.basicConfig(level=logging.INFO)

async def main():
    adapter = BlofinAdapter()
    positions = await adapter.get_open_positions()
    print(json.dumps(positions, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
