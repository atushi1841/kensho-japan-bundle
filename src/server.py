"""Kensho Japan Data Bundle — unified MCP server.

Combines 8 data sources into a single MCP server with 29 tools:
  - japan-intel-mcp: earthquakes, weather, typhoons, postal (live APIs)
  - japan-fuel-price-mcp: METI fuel prices (local cache + live fetch)
  - japan-minimum-wage-mcp: MHLW min wage (local cache + live fetch)
  - kensho-sweep-mcp: sweepstakes (1133 local observations)
  - tcg-price-japan: TCG used prices (960 local observations)
  - japan-ec-mcp: marketplace search URLs
  - kensho-apify: Apify actor wrapper (needs APIFY_TOKEN)

Local tools require no API keys. Apify tools need APIFY_TOKEN env var.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP

# Add project root to path for imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Data paths
# ─────────────────────────────────────────────────────────────

_BUNDLE_DATA_DIR = _PROJECT_ROOT / "data"
_SWEEP_DATA = _BUNDLE_DATA_DIR / "sweep_data.jsonl"
_TCG_DATA = _BUNDLE_DATA_DIR / "tcg_data.jsonl"
_FUEL_CACHE = _BUNDLE_DATA_DIR / "fuel_prices.json"
_MINWAGE_CACHE = _BUNDLE_DATA_DIR / "minwage.json"


# ─────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────

def _envelope(data: Any, sources: list[str], gaps: list[str]) -> dict[str, Any]:
    return {
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sources": sources,
        "data_gaps": gaps,
        "data": data,
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _match_jsonl(rows: list[dict], keyword: str, fields: list[str]) -> list[dict]:
    q = keyword.strip().lower()
    if not q:
        return rows
    out = []
    for r in rows:
        blob = " ".join(str(r.get(f, "")) for f in fields).lower()
        if q in blob:
            out.append(r)
    return out


# ─────────────────────────────────────────────────────────────
# Live API modules — import lazily to keep stdio startup fast
# ─────────────────────────────────────────────────────────────

def _ensure_modules_loaded():
    """Lazily load sub-modules that may have heavy deps (openpyxl etc)."""
    global _INTEL_SERVER, _FUEL_GET_SERVER, _MINWAGE_GET_SERVER, _EC_MCP
    if getattr(_ensure_modules_loaded, "_done", False):
        return
    _ensure_modules_loaded._done = True

    # 1. Japan Intel (live APIs — earthquakes, weather, typhoon, postal, EDINET)
    try:
        from mcp.japan_intel_mcp.src.server import server as _intel_server
        _INTEL_SERVER = _intel_server
    except Exception as e:
        logger.warning("japan-intel-mcp not available: %s", e)
        _INTEL_SERVER = None

    # 2. Fuel price (local cache + live METI fetch)
    try:
        sys.path.insert(0, str(Path("/mnt/d/Project2/japan-fuel-price-mcp")))
        from src.server import get_server as _fuel_get_server
        _FUEL_GET_SERVER = _fuel_get_server
    except Exception as e:
        logger.warning("japan-fuel-price-mcp not available: %s", e)
        _FUEL_GET_SERVER = None

    # 3. Minimum wage (local cache + live MHLW fetch)
    try:
        sys.path.insert(0, str(Path("/mnt/d/Project2/japan-minimum-wage-mcp")))
        from src.server import get_server as _minwage_get_server
        _MINWAGE_GET_SERVER = _minwage_get_server
    except Exception as e:
        logger.warning("japan-minimum-wage-mcp not available: %s", e)
        _MINWAGE_GET_SERVER = None

    # 4. E-Commerce search (search URLs — no network needed)
    try:
        sys.path.insert(0, str(_PROJECT_ROOT / "mcp_servers" / "japan_ec_mcp"))
        from server import mcp as _ec_mcp
        _EC_MCP = _ec_mcp
    except Exception as e:
        logger.warning("japan-ec-mcp not available: %s", e)
        _EC_MCP = None

    # 5. Apify wrapper (needs APIFY_TOKEN)
    try:
        sys.path.insert(0, str(_PROJECT_ROOT / "mcp" / "kensho-apify"))
        from server import server as _apify_server
        _APIFY_SERVER = _apify_server
    except Exception as e:
        logger.warning("kensho-apify not available: %s", e)
        _APIFY_SERVER = None


# ─────────────────────────────────────────────────────────────
# Sweepstakes tools (bundled local data)
# ─────────────────────────────────────────────────────────────

def _sweep_load() -> list[dict[str, Any]]:
    return _load_jsonl(_SWEEP_DATA)


def _sweep_match(rows: list[dict], keyword: str) -> list[dict]:
    q = keyword.strip().lower()
    if not q:
        return rows
    out = []
    for r in rows:
        blob = " ".join(str(v) for v in r.values()).lower()
        if q in blob:
            out.append(r)
    return out


# ─────────────────────────────────────────────────────────────
# TCG tools (bundled local data)
# ─────────────────────────────────────────────────────────────

def _tcg_load() -> list[dict[str, Any]]:
    return _load_jsonl(_TCG_DATA)


def _tcg_match(rows: list[dict], item: str) -> list[dict]:
    q = item.strip().lower()
    out = []
    for r in rows:
        blob = " ".join([
            str(r.get("name", "")),
            str(r.get("keyword", "")),
            str(r.get("url", "")),
        ]).lower()
        if q and q in blob:
            out.append(r)
    return out


# ─────────────────────────────────────────────────────────────
# Main server
# ─────────────────────────────────────────────────────────────

server = FastMCP(
    "kensho-japan-bundle",
    version="1.0.0",
)


@server.tool()
async def current_sweep(keyword: str) -> dict[str, Any]:
    """Latest sweep matching keyword in the kensho Japan sweepstakes dataset.

    Searches across knshow.com, kenshou.club, ken-kaku.com, cp.meikan.org
    sources (1133 observations, read-only local data).

    Args:
        keyword: item name, prize, or URL substring (e.g. "Amazonギフト", "iPhone", "リザードン").
    """
    try:
        _ensure_modules_loaded()
        rows = _sweep_match(_sweep_load(), keyword)
        if not rows:
            return {"keyword": keyword, "matches": 0, "error": "no matching sweep found"}
        latest = max(rows, key=lambda r: r.get("collected_at", r.get("date", "")))
        return {"keyword": keyword, "matches": len(rows), "latest": latest}
    except Exception as e:
        logger.exception("current_sweep error")
        return {"keyword": keyword, "error": str(e)}


@server.tool()
async def sweep_history(keyword: str, limit: int = 50) -> dict[str, Any]:
    """Time series of sweeps matching keyword in the kensho dataset.

    Args:
        keyword: item name, prize, or URL substring.
        limit: max history rows to return (default 50).
    """
    try:
        _ensure_modules_loaded()
        rows = sorted(_sweep_match(_sweep_load(), keyword),
                      key=lambda r: r.get("collected_at", r.get("date", "")))
        if not rows:
            return {"keyword": keyword, "matches": 0, "history": []}
        return {"keyword": keyword, "matches": len(rows), "history": rows[-limit:]}
    except Exception as e:
        logger.exception("sweep_history error")
        return {"keyword": keyword, "error": str(e)}


@server.tool()
async def top_prize_movers(direction: Optional[str] = None, limit: int = 10) -> dict[str, Any]:
    """Biggest prize value movers in the kensho sweepstakes dataset.

    Args:
        direction: None/"both" = up+down, "up" = value rose, "down" = value fell.
        limit: max movers to return (default 10).
    """
    try:
        _ensure_modules_loaded()
        rows = _sweep_load()
        by_key: dict[str, list[dict]] = {}
        for r in rows:
            key = r.get("url") or r.get("prize") or str(r)
            by_key.setdefault(key, []).append(r)

        movers = []
        for key, series in by_key.items():
            series = sorted(series, key=lambda r: r.get("collected_at", r.get("date", "")))
            if len(series) < 2:
                continue
            vals = [s.get("value_jpy") for s in series if s.get("value_jpy") is not None]
            if len(vals) < 2:
                continue
            first, last = vals[0], vals[-1]
            delta = last - first
            if direction == "up" and delta <= 0:
                continue
            if direction == "down" and delta >= 0:
                continue
            movers.append({
                "prize": series[-1].get("prize"),
                "first_value_jpy": first,
                "last_value_jpy": last,
                "delta_jpy": delta,
                "observations": len(series),
            })
        movers.sort(key=lambda m: abs(m["delta_jpy"]), reverse=True)
        return {"direction": direction, "count": len(movers), "movers": movers[:limit]}
    except Exception as e:
        logger.exception("top_prize_movers error")
        return {"direction": direction, "error": str(e)}


@server.tool()
async def tcg_current_price(item: str) -> dict[str, Any]:
    """Current used/new/list price for a Pokemon/TCG item in the Japan (Suruga-ya) dataset.

    Args:
        item: item name or URL substring (e.g. "リザードン", "pokemon card").
    """
    try:
        _ensure_modules_loaded()
        rows = sorted(_tcg_match(_tcg_load(), item),
                      key=lambda r: r.get("collected_at", ""))
        if not rows:
            return {"item": item, "matches": 0, "error": "no matching item"}
        latest = rows[-1]
        return {
            "item": item,
            "matches": len(rows),
            "name": latest.get("name"),
            "url": latest.get("url"),
            "used_price_jpy": latest.get("used_price_jpy"),
            "new_price_jpy": latest.get("new_price_jpy"),
            "list_price_jpy": latest.get("list_price_jpy"),
            "collected_at": latest.get("collected_at"),
            "source": latest.get("source"),
        }
    except Exception as e:
        logger.exception("tcg_current_price error")
        return {"item": item, "error": str(e)}


@server.tool()
async def tcg_price_history(item: str, limit: int = 50) -> dict[str, Any]:
    """Price time series for a TCG item in the Japan Suruga-ya dataset.

    Args:
        item: item name or URL substring.
        limit: max history rows (default 50).
    """
    try:
        _ensure_modules_loaded()
        rows = sorted(_tcg_match(_tcg_load(), item),
                      key=lambda r: r.get("collected_at", ""))
        if not rows:
            return {"item": item, "matches": 0, "history": []}
        hist = [{
            "collected_at": r.get("collected_at"),
            "used_price_jpy": r.get("used_price_jpy"),
            "new_price_jpy": r.get("new_price_jpy"),
            "list_price_jpy": r.get("list_price_jpy"),
        } for r in rows[-limit:]]
        return {"item": item, "matches": len(rows), "history": hist}
    except Exception as e:
        logger.exception("tcg_price_history error")
        return {"item": item, "error": str(e)}


@server.tool()
async def tcg_top_movers(direction: Optional[str] = None, limit: int = 10) -> dict[str, Any]:
    """Biggest TCG used-price movers in the Japan dataset.

    Args:
        direction: None/"both" = up+down, "up" = price rose, "down" = price fell.
        limit: max movers to return (default 10).
    """
    try:
        _ensure_modules_loaded()
        rows = _tcg_load()
        by_key: dict[str, list[dict]] = {}
        for r in rows:
            key = r.get("url") or r.get("name") or str(r)
            by_key.setdefault(key, []).append(r)

        movers = []
        for key, series in by_key.items():
            series = sorted(series, key=lambda r: r.get("collected_at", ""))
            if len(series) < 2:
                continue
            first = series[0].get("used_price_jpy")
            last = series[-1].get("used_price_jpy")
            if first is None or last is None:
                continue
            delta = last - first
            if direction == "up" and delta <= 0:
                continue
            if direction == "down" and delta >= 0:
                continue
            movers.append({
                "name": series[-1].get("name"),
                "url": key,
                "first_used_price_jpy": first,
                "last_used_price_jpy": last,
                "delta_jpy": delta,
                "pct": round(delta * 100.0 / first, 2) if first else None,
                "observations": len(series),
            })
        movers.sort(key=lambda m: abs(m["delta_jpy"]), reverse=True)
        return {"direction": direction, "count": len(movers), "movers": movers[:limit]}
    except Exception as e:
        logger.exception("tcg_top_movers error")
        return {"direction": direction, "error": str(e)}


@server.tool()
async def search_japan_marketplace(keyword: str, platform: str = "all", limit: int = 10) -> dict[str, Any]:
    """Search Japanese secondary marketplace listings (Mercari, Yahoo Auctions, Suruga-ya).

    Returns active search URLs with pricing context. No API key needed.

    Args:
        keyword: Product name in Japanese or English.
        platform: "mercari", "yahoo", "surugaya", or "all".
        limit: Max results per platform (default 10).
    """
    try:
        _ensure_modules_loaded()
        if _EC_MCP is not None:
            # Delegate to the original japan-ec-mcp server
            result = await _EC_MCP.call_tool(
                "search_japan_marketplace",
                {"keyword": keyword, "platform": platform, "limit": limit}
            )
            return result
        # Fallback: return search URLs directly
        import urllib.parse
        encoded = urllib.parse.quote(keyword)
        urls = {
            "mercari": f"https://jp.mercari.com/search?keyword={encoded}",
            "yahoo": f"https://auctions.yahoo.co.jp/search/search?rk=b&p={encoded}",
            "surugaya": f"https://www.suruga-ya.jp/search?keyword={encoded}",
        }
        return {"keyword": keyword, "platform": platform, "urls": urls}
    except Exception as e:
        logger.exception("search_japan_marketplace error")
        return {"keyword": keyword, "error": str(e)}


@server.tool()
async def get_japan_resale_guidance(category: str) -> dict[str, Any]:
    """Get arbitrage/sourcing guidance for Japanese goods categories.

    Args:
        category: "tcg", "camera", "luxury", "figure", "watch", or "car".
    """
    try:
        _ensure_modules_loaded()
        if _EC_MCP is not None:
            result = await _EC_MCP.call_tool(
                "get_japan_resale_guidance",
                {"category": category}
            )
            return result
        guidance = {
            "tcg": "Pokemon/Yu-Gi-Oh: check Suruga-ya, Mercari, Yahoo Auctions. Look for sealed booster boxes and chase cards.",
            "camera": "Check Mercari and Yahoo Auctions for vintageJapanese cameras (Pentax, Minolta, Nikon).",
            "luxury": "Mercari and Suruga-ya for bags/watches. Verify authenticity before reselling.",
            "figure": "Suruga-ya and Yahoo Auctions for figures. Sealed = premium.",
        }
        return {"category": category, "guidance": guidance.get(category, "No guidance available")}
    except Exception as e:
        logger.exception("get_japan_resale_guidance error")
        return {"category": category, "error": str(e)}


# ─────────────────────────────────────────────────────────────
# Apify actor wrapper tools (require APIFY_TOKEN)
# ─────────────────────────────────────────────────────────────

_APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
_APIFY_API_BASE = "https://api.apify.com/v2"


@server.tool()
async def list_actors() -> list[dict[str, Any]]:
    """List all Apify actors owned by the user (requires APIFY_TOKEN).

    Returns actor id, name, and userId for each.
    """
    if not _APIFY_TOKEN:
        return [{"error": "APIFY_TOKEN not set in environment"}]
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{_APIFY_API_BASE}/users/me/acts?limit=50",
            headers={"Authorization": f"Bearer {_APIFY_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            acts = data.get("data", [])
            return [
                {"id": a["id"], "name": a["name"], "userId": a.get("userId")}
                for a in acts
            ]
    except Exception as e:
        return [{"error": str(e)}]


@server.tool()
async def run_actor(actor_id: str, input_data: Optional[dict[str, Any]] = None,
                    timeout_seconds: int = 300) -> dict[str, Any]:
    """Run an Apify actor with input parameters.

    Args:
        actor_id: The actor ID or slug to run.
        input_data: Input parameters for the actor.
        timeout_seconds: Wait time in seconds (default 300).
    """
    if not _APIFY_TOKEN:
        return {"error": "APIFY_TOKEN not set in environment"}
    try:
        import urllib.request
        body = json.dumps({"input": input_data or {}, "waitSecs": timeout_seconds}).encode()
        req = urllib.request.Request(
            f"{_APIFY_API_BASE}/runs?actionStarts={actor_id}",
            data=body,
            headers={
                "Authorization": f"Bearer {_APIFY_TOKEN}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return {
                "runId": data.get("id"),
                "status": data.get("status"),
            }
    except Exception as e:
        return {"error": str(e)}


@server.tool()
async def get_run(run_id: str) -> dict[str, Any]:
    """Get status of an Apify actor run.

    Args:
        run_id: The run ID to check.
    """
    if not _APIFY_TOKEN:
        return {"error": "APIFY_TOKEN not set in environment"}
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{_APIFY_API_BASE}/runs/{run_id}",
            headers={"Authorization": f"Bearer {_APIFY_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {
                "runId": data.get("id"),
                "status": data.get("status"),
                "statusMessage": data.get("statusMessage"),
                "stats": data.get("stats"),
            }
    except Exception as e:
        return {"error": str(e)}


@server.tool()
async def get_dataset(run_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Get items from an Apify actor run's dataset.

    Args:
        run_id: The run ID.
        limit: Max items to return (default 100).
    """
    if not _APIFY_TOKEN:
        return []
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{_APIFY_API_BASE}/runs/{run_id}/dataset/items?limit={limit}",
            headers={"Authorization": f"Bearer {_APIFY_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("data", [])
    except Exception as e:
        return [{"error": str(e)}]


# ─────────────────────────────────────────────────────────────
# Japan Intel tools (live APIs — earthquakes, weather, typhoon, postal, EDINET)
# ─────────────────────────────────────────────────────────────

def _proxy_intel_tool(name: str, args: dict) -> dict:
    """Proxy a call to the japan-intel-mcp server if available."""
    if _INTEL_SERVER is None:
        return {"error": "japan-intel-mcp not available (live API deps missing)"}
    try:
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            _INTEL_SERVER.call_tool(name, args)
        )
    except Exception as e:
        return {"error": str(e)}


@server.tool()
async def japan_earthquakes(min_magnitude: float = 0.0, min_intensity: str = "",
                             limit: int = 20) -> dict[str, Any]:
    """Japan earthquake info from JMA (live API).

    Args:
        min_magnitude: Filter by minimum magnitude.
        min_intensity: Filter by minimum intensity (e.g. "3").
        limit: Max results (default 20).
    """
    result = _proxy_intel_tool("japan_earthquakes", {
        "min_magnitude": min_magnitude, "min_intensity": min_intensity, "limit": limit
    })
    return _envelope(result.get("data", result), ["jma.go.jp"], result.get("data_gaps", []))


@server.tool()
async def japan_weather_warnings(area: str = "") -> dict[str, Any]:
    """Japan weather warnings/alerts from JMA (live API).

    Args:
        area: Prefecture name to filter (e.g. "東京都"). Empty = all.
    """
    result = _proxy_intel_tool("japan_weather_warnings", {"area": area})
    return _envelope(result.get("data", result), ["jma.go.jp"], result.get("data_gaps", []))


@server.tool()
async def japan_weather_overview(prefecture: str = "東京都") -> dict[str, Any]:
    """Japan weather overview from JMA (live API).

    Args:
        prefecture: Target prefecture (default 東京都).
    """
    result = _proxy_intel_tool("japan_weather_overview", {"prefecture": prefecture})
    return _envelope(result.get("data", result), ["jma.go.jp"], result.get("data_gaps", []))


@server.tool()
async def japan_typhoons() -> dict[str, Any]:
    """Current typhoons monitored by JMA (live API)."""
    result = _proxy_intel_tool("japan_typhoons", {})
    return _envelope(result.get("data", result), ["jma.go.jp"], result.get("data_gaps", []))


@server.tool()
async def japan_postal_lookup(zipcode: str) -> dict[str, Any]:
    """Look up Japanese address by postal code via zipcloud (live API).

    Args:
        zipcode: 7-digit postal code (with or without hyphens).
    """
    result = _proxy_intel_tool("japan_postal_lookup", {"zipcode": zipcode})
    return _envelope(result.get("data", result), ["zipcloud.ibsnet.co.jp"],
                    result.get("data_gaps", []))


@server.tool()
async def japan_weekly_forecast(prefecture: str = "東京都") -> dict[str, Any]:
    """Japan weekly weather forecast from JMA (live API).

    Args:
        prefecture: Target prefecture (default 東京都).
    """
    result = _proxy_intel_tool("japan_weekly_forecast", {"prefecture": prefecture})
    return _envelope(result.get("data", result), ["jma.go.jp"], result.get("data_gaps", []))


@server.tool()
async def japan_edinet_filings(date: str = "") -> dict[str, Any]:
    """Japan EDINET corporate filings from FSA API (live API).

    Args:
        date: Date filter (YYYY-MM-DD). Empty = latest.
    """
    result = _proxy_intel_tool("japan_edinet_filings", {"date": date})
    return _envelope(result.get("data", result), ["edinet-fsa.go.jp"],
                    result.get("data_gaps", []))


@server.tool()
async def japan_watch_summary() -> dict[str, Any]:
    """Summary of all active Japan weather watches/warnings (live API)."""
    result = _proxy_intel_tool("japan_watch_summary", {})
    return _envelope(result.get("data", result), ["jma.go.jp"],
                    result.get("data_gaps", []))


# ─────────────────────────────────────────────────────────────
# Fuel price tools (local cache + live METI fetch)
# ─────────────────────────────────────────────────────────────

def _proxy_fuel_tool(name: str, args: dict) -> dict:
    """Proxy a call to the japan-fuel-price-mcp server if available."""
    if _FUEL_GET_SERVER is None:
        return {"error": "japan-fuel-price-mcp not available (openpyxl missing)"}
    try:
        import asyncio
        srv = _FUEL_GET_SERVER()
        return asyncio.get_event_loop().run_until_complete(
            srv.call_tool(name, args)
        )
    except Exception as e:
        logger.warning("fuel tool %s error: %s", name, e)
        return {"error": str(e)}


@server.tool()
async def get_latest_fuel_price(prefecture: str = "全国", product: str = "regular") -> dict:
    """Latest weekly average retail fuel price from METI (local cache + live fetch).

    Args:
        prefecture: 都道府県名 or 全国 (default: 全国).
        product: premium | regular | diesel | kerosene_store | kerosene_delivery.
    """
    return _proxy_fuel_tool("get_latest_fuel_price", {"prefecture": prefecture, "product": product})


@server.tool()
async def get_fuel_price_history(prefecture: str = "全国", product: str = "regular",
                                  weeks: int = 12) -> dict:
    """Weekly fuel price history from METI (local cache + live fetch).

    Args:
        prefecture: 都道府県名 or 全国.
        product: premium | regular | diesel | kerosene_store | kerosene_delivery.
        weeks: number of recent weeks (default 12, max 260).
    """
    return _proxy_fuel_tool("get_fuel_price_history", {
        "prefecture": prefecture, "product": product, "weeks": weeks
    })


@server.tool()
async def cheapest_prefectures(product: str = "regular", limit: int = 10) -> dict:
    """Rank Japan's 47 prefectures by latest fuel price (cheapest first).

    Args:
        product: premium | regular | diesel | kerosene_store | kerosene_delivery.
        limit: entries per end of ranking (default 10, max 47).
    """
    return _proxy_fuel_tool("cheapest_prefectures", {"product": product, "limit": limit})


@server.tool()
async def national_fuel_trend(products: str = "premium,regular,diesel",
                               weeks: int = 52) -> dict:
    """Nationwide fuel price trend across products (inflation indicator).

    Args:
        products: comma-separated product keys.
        weeks: lookback window (default 52, max 260).
    """
    return _proxy_fuel_tool("national_fuel_trend", {"products": products, "weeks": weeks})


@server.tool()
async def list_fuel_regions() -> dict:
    """List all queryable regions and products for fuel price tools."""
    return _proxy_fuel_tool("list_fuel_regions", {})


# ─────────────────────────────────────────────────────────────
# Minimum wage tools (local cache + live MHLW fetch)
# ─────────────────────────────────────────────────────────────

def _proxy_minwage_tool(name: str, args: dict) -> dict:
    """Proxy a call to the japan-minimum-wage-mcp server if available."""
    if _MINWAGE_GET_SERVER is None:
        return {"error": "japan-minimum-wage-mcp not available (openpyxl missing)"}
    try:
        import asyncio
        srv = _MINWAGE_GET_SERVER()
        return asyncio.get_event_loop().run_until_complete(
            srv.call_tool(name, args)
        )
    except Exception as e:
        logger.warning("minwage tool %s error: %s", name, e)
        return {"error": str(e)}


@server.tool()
async def get_minimum_wage(prefecture: str = "東京") -> dict:
    """Get current regional minimum wage for a Japanese prefecture (MHLW).

    Args:
        prefecture: 都道府県名 or 全国 (default: 東京).
    """
    return _proxy_minwage_tool("get_minimum_wage", {"prefecture": prefecture})


@server.tool()
async def get_minimum_wage_history(prefecture: str = "東京", years: int = 5) -> dict:
    """Get minimum wage revision history for a prefecture (MHLW).

    Args:
        prefecture: 都道府県名.
        years: number of recent fiscal years (default 5, max 24).
    """
    return _proxy_minwage_tool("get_minimum_wage_history", {
        "prefecture": prefecture, "years": years
    })


@server.tool()
async def rank_minimum_wages(limit: int = 10) -> dict:
    """Rank Japan's 47 prefectures by current minimum wage (cheapest first).

    Args:
        limit: entries per end of ranking (default 10, max 47).
    """
    return _proxy_minwage_tool("rank_minimum_wages", {"limit": limit})


@server.tool()
async def list_prefectures() -> dict:
    """List all 47 prefectures with romaji aliases and available fiscal years."""
    return _proxy_minwage_tool("list_prefectures", {})


# ─────────────────────────────────────────────────────────────
# Server startup helper
# ─────────────────────────────────────────────────────────────

def get_server() -> FastMCP:
    """Return the bundled FastMCP server instance."""
    return server


# Auto-register sub-server tools if their modules are importable
def _register_sub_servers():
    """Register tools from sub-servers if their dependencies are available."""
    _ensure_modules_loaded()

    # Register ec-mcp tools if available
    if _EC_MCP is not None:
        for tool_name in getattr(_EC_MCP, "_tool_map", {}).keys():
            if tool_name not in server._tool_map:
                server.add_tool(_EC_MCP._tool_map[tool_name])


_register_sub_servers()
