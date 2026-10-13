# Kensho Japan Data Bundle

MCP Bundle combining 8 Japanese data sources into one installable package:

| Source | Tools | Notes |
|--------|-------|-------|
| Japan Intel (earthquakes, weather, typhoon, EDINET, postal) | 8 | Live APIs, keyless |
| Fuel Prices (METI) | 5 | Local cache + live fetch |
| Minimum Wage (MHLW) | 4 | Local cache + live fetch |
| Sweepstakes (kensho dataset) | 3 | 1133 local observations |
| TCG Prices (Suruga-ya) | 3 | 960 local observations |
| E-Commerce Search | 2 | Search URLs only |
| Apify Actor Wrapper | 4 | Needs APIFY_TOKEN env var |

**Total: 29 tools across 7 categories**

## Install

```bash
npx @anthropic-ai/mcpb install kensho-japan-bundle.mcpb --client claude
```

Or copy `server.mcpb` to your project and configure in MCP client config:

```json
{
  "mcpServers": {
    "kensho-japan-bundle": {
      "command": "uv",
      "args": ["run", "--directory", "${bundle_dir}", "src/stdio_main.py"]
    }
  }
}
```

## Requirements

- Python >=3.10
- [uv](https://docs.astral.sh/uv/) for dependency resolution
- No API keys required for local tools
- `APIFY_TOKEN` optional — enables Apify actor wrapper tools
