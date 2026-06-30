"""Allow running the MCP server via `python -m neuron`."""

import asyncio
from neuron.server import main

asyncio.run(main())
