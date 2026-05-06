from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from statistics import median
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


CACHE_TTL_SECONDS = 90
FETCH_TIMEOUT_SECONDS = 12
USER_AGENT = "GoldOilRadar/1.0"
GOOGLE_NEWS_ENDPOINT = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
GOOGLE_SITE_FILTER = (
    "(site:reuters.com OR site:cnbc.com OR site:bloomberg.com OR "
    "site:apnews.com OR site:wsj.com OR site:ft.com)"
)

TRUSTED_PUBLISHERS = [
    "Reuters",
    "CNBC",
    "Bloomberg",
    "Associated Press",
    "AP News",
    "The Wall Street Journal",
    "Financial Times",
]

SOURCE_TRUST = {
    "Reuters": 15,
    "CNBC": 13,
    "Bloomberg": 15,
    "Associated Press": 12,
    "AP News": 12,
    "The Wall Street Journal": 14,
    "Financial Times": 14,
    "Federal Reserve": 16,
    "U.S. Energy Information Administration": 16,
    "Forex Factory": 15,
}

PRICE_FEEDS = {
    "gold": {
        "label": "XAUUSD",
        "proxyLabel": "XAU/USD spot",
        "symbol": "XAUUSD=X",
        "spotUrl": "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD",
        "spotLabel": "Swissquote XAU/USD",
        "chartRange": "5d",
        "chartInterval": "30m",
        "priceDigits": 2,
    },
    "oil": {
        "label": "WTI Crude",
        "proxyLabel": "WTI front-month",
        "symbol": "CL=F",
        "chartRange": "5d",
        "chartInterval": "30m",
        "priceDigits": 2,
    },
    "eurusd": {
        "label": "EUR/USD",
        "proxyLabel": "EUR/USD spot",
        "symbol": "EURUSD=X",
        "spotUrl": "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/EUR/USD",
        "spotLabel": "Swissquote EUR/USD",
        "chartRange": "1d",
        "chartInterval": "5m",
        "priceDigits": 5,
    },
}

NEWS_FEEDS = [
    {
        "id": "gold-watch",
        "name": "Trusted gold scan",
        "kind": "google_rss",
        "url": GOOGLE_NEWS_ENDPOINT.format(
            query=quote_plus(
                f'(gold OR xauusd OR bullion OR "safe haven" OR fed OR inflation) '
                f"{GOOGLE_SITE_FILTER}"
            )
        ),
    },
    {
        "id": "oil-watch",
        "name": "Trusted oil scan",
        "kind": "google_rss",
        "url": GOOGLE_NEWS_ENDPOINT.format(
            query=quote_plus(
                f'(wti OR crude oil OR brent OR opec OR inventories OR eia) '
                f"{GOOGLE_SITE_FILTER}"
            )
        ),
    },
    {
        "id": "geo-watch",
        "name": "Trusted geopolitics scan",
        "kind": "google_rss",
        "url": GOOGLE_NEWS_ENDPOINT.format(
            query=quote_plus(
                f'((iran OR hormuz OR "middle east" OR sanctions OR ceasefire OR '
                f'"shipping route" OR tanker) AND (gold OR oil OR crude OR xauusd)) '
                f"{GOOGLE_SITE_FILTER}"
            )
        ),
    },
    {
        "id": "fx-watch",
        "name": "Trusted EUR/USD scan",
        "kind": "google_rss",
        "url": GOOGLE_NEWS_ENDPOINT.format(
            query=quote_plus(
                f'(("EUR/USD" OR EURUSD OR euro OR ECB OR "European Central Bank") '
                f'AND (fed OR inflation OR yields OR rates OR payrolls OR dollar OR ECB)) '
                f"{GOOGLE_SITE_FILTER}"
            )
        ),
    },
    {
        "id": "fed-press",
        "name": "Federal Reserve releases",
        "kind": "rss",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "forcedSource": "Federal Reserve",
    },
    {
        "id": "eia-today",
        "name": "EIA Today in Energy",
        "kind": "rss",
        "url": "https://www.eia.gov/rss/todayinenergy.xml",
        "forcedSource": "U.S. Energy Information Administration",
    },
]

CALENDAR_FEEDS = [
    {
        "id": "ff-week",
        "name": "Forex Factory weekly calendar",
        "kind": "ff_xml",
        "url": "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
    },
    {
        "id": "ff-week-cdn",
        "name": "Forex Factory weekly calendar CDN",
        "kind": "ff_xml",
        "url": "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.xml",
    },
]

GOLD_RELEVANCE = re.compile(
    r"\b(xauusd|gold|bullion|precious metal|safe haven|treasury yield|real yield|gold etf)\b",
    re.IGNORECASE,
)
OIL_RELEVANCE = re.compile(
    r"\b(wti|brent|crude|oil|opec\+?|inventor(?:y|ies)|eia|gasoline|diesel|refinery|pipeline|rig count)\b",
    re.IGNORECASE,
)
FX_RELEVANCE = re.compile(
    r"\b(eur\/usd|eurusd|eur usd|euro|ecb|european central bank|eurozone|us dollar|u\.s\. dollar|dollar index)\b",
    re.IGNORECASE,
)
MACRO_RELEVANCE = re.compile(
    r"\b(fed|fomc|powell|cpi|pce|inflation|nonfarm|nfp|payrolls|jobless|unemployment|"
    r"interest rate|rate cut|rate hike|gdp|pmi|retail sales|tariff|sanctions|"
    r"iran|israel|gaza|ukraine|russia|china|ceasefire|hormuz|middle east|ecb|lagarde|euro|dollar)\b",
    re.IGNORECASE,
)

SIGNALS = [
    {
        "label": "Middle East escalation",
        "pattern": re.compile(
            r"(\b(iran|hormuz|strait of hormuz|red sea|shipping route|shipping lane)\b.*"
            r"\b(threat|closure|closed|attack|missile|drone|airstrike|military strike|"
            r"disruption|retaliat(?:e|ion)|crisis|war|conflict|shut-?ins?)\b)|"
            r"(\b(threat|closure|closed|attack|missile|drone|airstrike|military strike|"
            r"disruption|retaliat(?:e|ion)|crisis|war|conflict|shut-?ins?)\b.*"
            r"\b(iran|hormuz|strait of hormuz|red sea|shipping route|shipping lane)\b)",
            re.IGNORECASE,
        ),
        "effects": {"gold": 18, "oil": 22, "eurusd": -8},
        "reason": "Supply disruption risk tends to lift crude and support gold as a safe haven.",
    },
    {
        "label": "War escalation",
        "pattern": re.compile(
            r"\b(escalat(?:e|ion)|offensive|troop buildup|retaliat(?:e|ion)|conflict widens|"
            r"backs? .*war|support for .*war)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": 12, "oil": 14, "eurusd": -8},
        "reason": "Broader conflict usually sends traders into safety and prices in oil supply risk.",
    },
    {
        "label": "De-escalation",
        "pattern": re.compile(
            r"\b(ceasefire|truce|peace talks|de-escalation|shipping resumes|route reopens?|"
            r"route opens?|open strait|exports resume|sanctions relief|deal reached|"
            r"ease concerns|safe(?: [a-z]+){0,4} passage|allow(?:s|ed|ing)?(?: [a-z]+){0,4} passage|"
            r"war will end|ends?(?: [a-z]+){0,3} soon|lower oil prices|oil prices fall|tame prices|"
            r"flow through|oil and gas to flow|route stays open|helping to open)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": -20, "oil": -24, "eurusd": 8},
        "reason": "De-escalation usually removes safe-haven demand and lowers crude risk premiums.",
    },
    {
        "label": "Sanctions tightening",
        "pattern": re.compile(
            r"\b(sanctions? tighten|new sanctions|embargo|export curbs|blacklist|price cap)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": 10, "oil": 18, "eurusd": -6},
        "reason": "Tighter sanctions can choke supply and increase defensive positioning.",
    },
    {
        "label": "Supply normalization",
        "pattern": re.compile(
            r"\b(production returns?|supply returns?|output recovers?|exports rise|exports resume|"
            r"resume pumping|restores? output|removing sanctions|allow safe passage|"
            r"sale of iran oil|brings? supply to ports)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": -8, "oil": -20, "eurusd": 6},
        "reason": "More barrels back on the market usually weigh on crude and cool defensive gold demand.",
    },
    {
        "label": "OPEC cuts",
        "pattern": re.compile(
            r"\b(opec\+?|opec plus).*\b(cut|cuts|extend cuts|voluntary cuts)\b|"
            r"\b(output cuts?|production cuts?)\b",
            re.IGNORECASE,
        ),
        "effects": {"oil": 24},
        "reason": "Actual or expected supply cuts are typically bullish for crude.",
    },
    {
        "label": "OPEC output increase",
        "pattern": re.compile(
            r"\b(opec\+?|opec plus).*\b(raise output|increase output|boost production|restore barrels)\b|"
            r"\b(output increase|production increase)\b",
            re.IGNORECASE,
        ),
        "effects": {"oil": -22},
        "reason": "Higher planned production normally leans bearish for crude.",
    },
    {
        "label": "Inventory draw",
        "pattern": re.compile(
            r"\b(crude inventories|stockpiles?)\b.*\b(draw|drawdown|fall|drop|decline)\b",
            re.IGNORECASE,
        ),
        "effects": {"oil": 18},
        "reason": "A deeper draw can signal tighter supply and supports oil.",
    },
    {
        "label": "Inventory build",
        "pattern": re.compile(
            r"\b(crude inventories|stockpiles?)\b.*\b(build|rise|increase|surge)\b",
            re.IGNORECASE,
        ),
        "effects": {"oil": -18},
        "reason": "A larger build usually signals softer near-term supply-demand balance for oil.",
    },
    {
        "label": "Fed dovish",
        "pattern": re.compile(
            r"\b(rate cuts?|cuts expected|dovish|pause likely|lower yields?|softer dollar)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": 18, "oil": 6, "eurusd": 18},
        "reason": "Lower-rate expectations usually help gold and can support risk assets.",
    },
    {
        "label": "Fed hawkish",
        "pattern": re.compile(
            r"\b(higher for longer|rate hikes?|hawkish|yields? jump|firm dollar|stronger dollar)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": -18, "oil": -8, "eurusd": -18},
        "reason": "Higher yields and a stronger dollar usually weigh on gold and can pressure oil.",
    },
    {
        "label": "Hot inflation",
        "pattern": re.compile(
            r"\b(cpi|pce|inflation)\b.*\b(hotter|accelerat(?:e|es|ed)|sticky|re-accelerat(?:e|ion)|surprise upside)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": -12, "oil": 4, "eurusd": -14},
        "reason": "Hot inflation often lifts yields and the dollar first, which can hit gold.",
    },
    {
        "label": "Cooling inflation",
        "pattern": re.compile(
            r"\b(cpi|pce|inflation)\b.*\b(cools?|eases?|slows?|softens?)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": 12, "eurusd": 14},
        "reason": "Cooling inflation can reduce rate pressure and help gold.",
    },
    {
        "label": "Recession risk",
        "pattern": re.compile(
            r"\b(recession|slowdown|demand fears|hard landing|growth scare|manufacturing slump)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": 10, "oil": -18, "eurusd": -10},
        "reason": "Growth scares usually support safety trades and hurt demand-linked oil.",
    },
    {
        "label": "China demand support",
        "pattern": re.compile(
            r"\b(china)\b.*\b(stimulus|support|recovery|demand picks up|industrial rebound)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": 6, "oil": 16, "eurusd": 4},
        "reason": "Stronger China demand is usually constructive for crude and commodities broadly.",
    },
    {
        "label": "Trade conflict",
        "pattern": re.compile(
            r"\b(tariffs?|trade war|retaliatory duties|new duties)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": 10, "oil": -10, "eurusd": -10},
        "reason": "Trade conflict typically boosts safety demand while hurting growth-sensitive oil.",
    },
    {
        "label": "ECB hawkish",
        "pattern": re.compile(
            r"\b(ecb|lagarde|european central bank)\b.*\b(hawkish|rate hike|higher for longer|"
            r"inflation fight|tightening)\b",
            re.IGNORECASE,
        ),
        "effects": {"eurusd": 18},
        "reason": "A hawkish ECB usually supports the euro against the dollar.",
    },
    {
        "label": "ECB dovish",
        "pattern": re.compile(
            r"\b(ecb|lagarde|european central bank)\b.*\b(dovish|rate cut|easing|stimulus|"
            r"support growth|looser policy)\b",
            re.IGNORECASE,
        ),
        "effects": {"eurusd": -18},
        "reason": "A dovish ECB usually weighs on EUR/USD.",
    },
    {
        "label": "Gold demand",
        "pattern": re.compile(
            r"\b(central bank gold|gold reserves?|bullion demand|gold etf inflows?)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": 16},
        "reason": "Direct gold demand headlines can extend upside pressure in XAUUSD.",
    },
    {
        "label": "Gold liquidation",
        "pattern": re.compile(
            r"\b(gold etf outflows?|bullion selling|profit taking in gold|gold liquidation)\b",
            re.IGNORECASE,
        ),
        "effects": {"gold": -14},
        "reason": "Direct liquidation headlines can pressure XAUUSD lower.",
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(*parts: str) -> str:
    raw = "|".join(part or "" for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    value = unescape(re.sub(r"<[^>]+>", " ", value))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _local_name(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1].lower()


def _first_child_text(node: ET.Element, names: List[str]) -> str:
    wanted = {name.lower() for name in names}
    for child in node.iter():
        if _local_name(child.tag) in wanted:
            text = _clean_text("".join(child.itertext()))
            if text:
                return text
    return ""


def _first_link(node: ET.Element) -> str:
    for child in node.iter():
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        text = _clean_text(child.text)
        if text:
            return text
    return ""


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None

    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    for pattern in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m-%d-%Y %I:%M%p",
        "%m-%d-%Y",
        "%b %d %Y %I:%M%p",
        "%b %d %Y",
    ):
        try:
            parsed = datetime.strptime(value, pattern)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _format_time_label(value: str) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return value or "Unknown time"
    return parsed.strftime("%b %d, %H:%M UTC")


def _calendar_timing_note(time_text: str) -> Optional[str]:
    normalized = _clean_text(time_text).lower()
    if not normalized:
        return None
    if normalized in {"tentative", "all day"}:
        return normalized.title()
    if re.fullmatch(r"day\s+\d+", normalized):
        return normalized.title()
    return None


def _parse_calendar_scheduled_at(date_text: str, time_text: str) -> Optional[datetime]:
    clean_date = _clean_text(date_text).replace(",", "")
    clean_time = _clean_text(time_text).replace(".", "").upper()
    if not clean_date or _calendar_timing_note(clean_time):
        return None

    now_utc = datetime.now(timezone.utc)
    time_candidates = [clean_time] if clean_time else []
    if clean_time.endswith("AM") or clean_time.endswith("PM"):
        time_candidates.append(clean_time[:-2] + clean_time[-2:].lower())

    dated_candidates = []
    if time_candidates:
        dated_candidates.extend(f"{clean_date} {candidate}".strip() for candidate in time_candidates)
    dated_candidates.append(clean_date)

    for candidate in dated_candidates:
        parsed = _parse_datetime(candidate)
        if parsed:
            if parsed.year == 1900:
                parsed = parsed.replace(year=now_utc.year)
            return parsed

    date_patterns = (
        "%a %b %d",
        "%A %b %d",
        "%b %d",
        "%m-%d",
    )
    time_patterns = ("%I:%M%p", "%H:%M")

    base_date: Optional[datetime] = None
    for pattern in date_patterns:
        try:
            base_date = datetime.strptime(clean_date, pattern).replace(year=now_utc.year, tzinfo=timezone.utc)
            break
        except ValueError:
            continue

    if base_date is None:
        return None

    for pattern in time_patterns:
        for candidate in time_candidates:
            try:
                parsed_time = datetime.strptime(candidate, pattern)
                return base_date.replace(hour=parsed_time.hour, minute=parsed_time.minute)
            except ValueError:
                continue

    return None


def _canonical_source(source: str) -> str:
    lowered = source.lower()
    if "reuters" in lowered:
        return "Reuters"
    if "cnbc" in lowered:
        return "CNBC"
    if "bloomberg" in lowered:
        return "Bloomberg"
    if "associated press" in lowered:
        return "Associated Press"
    if lowered == "ap news" or "ap news" in lowered:
        return "AP News"
    if "wall street journal" in lowered or lowered == "wsj":
        return "The Wall Street Journal"
    if "financial times" in lowered or lowered == "ft":
        return "Financial Times"
    if "federal reserve" in lowered or "fomc" in lowered:
        return "Federal Reserve"
    if "energy information administration" in lowered or lowered == "eia":
        return "U.S. Energy Information Administration"
    if "forex factory" in lowered:
        return "Forex Factory"
    return source.strip() or "Unknown source"


def _source_trust(source: str) -> int:
    return SOURCE_TRUST.get(_canonical_source(source), 9)


def _is_relevant_text(text: str) -> bool:
    return bool(
        GOLD_RELEVANCE.search(text)
        or OIL_RELEVANCE.search(text)
        or FX_RELEVANCE.search(text)
        or MACRO_RELEVANCE.search(text)
    )


def _score_to_bias(score: int) -> str:
    if score >= 6:
        return "up"
    if score <= -6:
        return "down"
    if abs(score) >= 2:
        return "mixed"
    return "neutral"


def _reaction_pct(asset: str, confidence: int) -> Tuple[float, float]:
    if asset == "gold":
        peak = 0.25 + (1.45 - 0.25) * (confidence / 100.0)
    elif asset == "eurusd":
        peak = 0.08 + (0.75 - 0.08) * (confidence / 100.0)
    else:
        peak = 0.70 + (3.25 - 0.70) * (confidence / 100.0)
    return round(peak * 0.45, 2), round(peak, 2)


def _price_zone(
    price: Optional[float], bias: str, move_low_pct: float, move_high_pct: float, digits: int = 2
) -> Optional[Dict[str, Any]]:
    if price is None or bias not in {"up", "down"}:
        return None

    if bias == "up":
        low = round(price * (1 + move_low_pct / 100), digits)
        high = round(price * (1 + move_high_pct / 100), digits)
    else:
        low = round(price * (1 - move_high_pct / 100), digits)
        high = round(price * (1 - move_low_pct / 100), digits)

    low_label = f"{min(low, high):,.{digits}f}"
    high_label = f"{max(low, high):,.{digits}f}"

    return {
        "low": min(low, high),
        "high": max(low, high),
        "label": f"{low_label} - {high_label}",
    }


def _impact_payload(
    asset: str,
    score: int,
    source: str,
    reason: str,
    price: Optional[float],
    digits: int = 2,
    relevance_boost: int = 0,
    mixed_confidence: int = 0,
) -> Dict[str, Any]:
    bias = _score_to_bias(score)
    trust = _source_trust(source)
    if bias == "neutral":
        confidence = max(0, mixed_confidence)
    elif bias == "mixed":
        confidence = max(mixed_confidence or 48, min(78, abs(score) * 3 + trust + relevance_boost))
    else:
        confidence = min(96, abs(score) * 3 + trust + relevance_boost)

    move_low_pct, move_high_pct = _reaction_pct(asset, confidence or 42)
    zone = _price_zone(price, bias, move_low_pct, move_high_pct, digits=digits)
    return {
        "bias": bias,
        "confidence": int(confidence),
        "moveLowPct": move_low_pct,
        "moveHighPct": move_high_pct,
        "zone": zone,
        "reason": reason,
    }


def _severity_from_confidence(confidence: int) -> str:
    if confidence >= 85:
        return "critical"
    if confidence >= 68:
        return "major"
    if confidence >= 50:
        return "moderate"
    return "watch"


def _impact_rank(impact_text: str) -> int:
    normalized = (impact_text or "").lower()
    if "high" in normalized:
        return 3
    if "medium" in normalized:
        return 2
    return 1


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _parse_numeric_value(value: str) -> Optional[float]:
    if not value:
        return None

    cleaned = value.replace(",", "").strip()
    match = re.search(r"[-+]?\d*\.?\d+", cleaned)
    if not match:
        return None

    number = float(match.group(0))
    suffix_match = re.search(r"[-+]?\d*\.?\d+\s*([kmb])\b", cleaned, re.IGNORECASE)
    if suffix_match:
        suffix = suffix_match.group(1).lower()
        if suffix == "k":
            number *= 1_000
        elif suffix == "m":
            number *= 1_000_000
        elif suffix == "b":
            number *= 1_000_000_000
    return number


def _parse_swissquote_spot_quote(payload: Any) -> Tuple[float, Optional[datetime]]:
    mids: List[float] = []
    timestamps: List[datetime] = []

    if not isinstance(payload, list):
        raise ValueError("Swissquote quote payload is not a list")

    for venue in payload:
        ts = venue.get("ts")
        if ts is not None:
            try:
                timestamps.append(datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc))
            except (TypeError, ValueError, OSError):
                pass

        for quote_row in venue.get("spreadProfilePrices") or []:
            bid = quote_row.get("bid")
            ask = quote_row.get("ask")
            if bid is None or ask is None:
                continue
            mids.append((float(bid) + float(ask)) / 2.0)

    if not mids:
        raise ValueError("Swissquote quote payload did not contain bid/ask data")

    return float(median(mids)), max(timestamps) if timestamps else None


class MarketMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached_payload: Dict[str, Any] = {}
        self._cached_at = 0.0

    def get_snapshot(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            if (
                not force
                and self._cached_payload
                and (time.time() - self._cached_at) < CACHE_TTL_SECONDS
            ):
                return self._cached_payload

        payload = self._build_snapshot()

        with self._lock:
            self._cached_payload = payload
            self._cached_at = time.time()

        return payload

    def _build_snapshot(self) -> Dict[str, Any]:
        prices, price_status = self._fetch_prices()
        news_items, news_status = self._fetch_news_items(prices)
        calendar_items, calendar_status = self._fetch_calendar_items(prices)

        all_items = sorted(
            news_items + calendar_items,
            key=lambda item: item.get("sortScore", 0),
            reverse=True,
        )

        assets = {
            asset: self._build_asset_summary(asset, prices.get(asset), all_items)
            for asset in PRICE_FEEDS
        }

        alerts = self._build_alerts(all_items)
        breaking_news = [item for item in news_items if item.get("breaking")]
        warnings = self._build_warnings(news_items, calendar_items, news_status, calendar_status, price_status)

        return {
            "generatedAt": _now_iso(),
            "cacheSeconds": CACHE_TTL_SECONDS,
            "trustedPublishers": TRUSTED_PUBLISHERS,
            "assets": assets,
            "alerts": alerts[:8],
            "breakingNews": breaking_news[:8],
            "news": news_items[:18],
            "calendar": calendar_items[:12],
            "sources": price_status + news_status + calendar_status,
            "warnings": warnings,
        }

    def _fetch_text(self, url: str, accept: str = "*/*") -> str:
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": accept,
                "Connection": "close",
            },
        )
        with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="replace")

    def _fetch_prices(self) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
        prices: Dict[str, Dict[str, Any]] = {}
        status_rows: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=len(PRICE_FEEDS)) as executor:
            futures = {
                executor.submit(self._fetch_single_price, asset, config): asset
                for asset, config in PRICE_FEEDS.items()
            }
            for future in as_completed(futures):
                asset, price_payload, status = future.result()
                prices[asset] = price_payload
                status_rows.append(status)

        status_rows.sort(key=lambda row: row["id"])
        return prices, status_rows

    def _fetch_single_price(
        self, asset: str, config: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote_plus(config['symbol'])}?range={config.get('chartRange', '1d')}"
            f"&interval={config.get('chartInterval', '5m')}"
        )
        status = {
            "id": f"price-{asset}",
            "type": "price",
            "label": f"{config['label']} live quote",
            "healthy": False,
        }

        chart_meta: Dict[str, Any] = {}
        closes: List[float] = []
        chart_error: Optional[Exception] = None

        try:
            payload = json.loads(self._fetch_text(url, accept="application/json"))
            result = payload.get("chart", {}).get("result", [])
            if not result:
                raise ValueError("No price data returned")
            chart_meta = result[0].get("meta", {})
            quote = (result[0].get("indicators", {}).get("quote") or [{}])[0]
            closes = [float(value) for value in quote.get("close", []) if value is not None]
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            chart_error = exc

        price = chart_meta.get("regularMarketPrice") or (closes[-1] if closes else None) or chart_meta.get("previousClose")
        updated_at = _now_iso()
        spot_error: Optional[Exception] = None

        if config.get("spotUrl"):
            try:
                spot_payload = json.loads(self._fetch_text(config["spotUrl"], accept="application/json"))
                spot_price, spot_updated = _parse_swissquote_spot_quote(spot_payload)
                price = spot_price
                if spot_updated:
                    updated_at = spot_updated.isoformat()
            except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                spot_error = exc

        if price is None:
            price_payload = {
                "asset": asset,
                "label": config["label"],
                "proxyLabel": config["proxyLabel"],
                "symbol": config["symbol"],
                "price": None,
                "changePct": None,
                "currency": "USD",
                "priceDigits": config.get("priceDigits", 2),
                "series": [],
                "dayLow": None,
                "dayHigh": None,
                "updatedAt": updated_at,
            }
            status["error"] = str(spot_error or chart_error or "No price data returned")
            return asset, price_payload, status

        previous_close = chart_meta.get("previousClose")
        change_pct = chart_meta.get("regularMarketChangePercent")
        if change_pct is None and previous_close not in {None, 0}:
            change_pct = ((float(price) - float(previous_close)) / float(previous_close)) * 100.0

        rounded_price = round(float(price), config.get("priceDigits", 2))
        rounded_series = [
            round(value, config.get("priceDigits", 2))
            for value in closes[-42:]
        ]
        if rounded_series:
            rounded_series[-1] = rounded_price

        day_low = chart_meta.get("regularMarketDayLow")
        day_high = chart_meta.get("regularMarketDayHigh")
        if rounded_series:
            day_low = min([value for value in [day_low, rounded_price, *rounded_series] if value is not None])
            day_high = max([value for value in [day_high, rounded_price, *rounded_series] if value is not None])

        price_payload = {
            "asset": asset,
            "label": config["label"],
            "proxyLabel": config["proxyLabel"],
            "symbol": config["symbol"],
            "price": rounded_price,
            "changePct": round(float(change_pct), 4) if change_pct is not None else None,
            "currency": chart_meta.get("currency") or "USD",
            "priceDigits": config.get("priceDigits", 2),
            "series": rounded_series,
            "dayLow": day_low,
            "dayHigh": day_high,
            "updatedAt": updated_at,
        }

        status["healthy"] = True
        if config.get("spotUrl"):
            status["note"] = f"{config.get('spotLabel', config['proxyLabel'])} spot quote online"
            if chart_error:
                status["note"] += " | chart fallback limited"
        else:
            if str(config.get("symbol", "")).endswith("=F"):
                status["note"] = f"{config['symbol']} front-month quote online"
            else:
                status["note"] = f"{config['symbol']} quote online"

        return asset, price_payload, status

    def _parse_rss_items(
        self,
        xml_text: str,
        feed_name: str,
        forced_source: Optional[str] = None,
        google_mode: bool = False,
        prices: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_text)
        nodes = root.findall(".//item")
        if not nodes:
            nodes = root.findall(".//{*}entry")

        stories: List[Dict[str, Any]] = []
        for node in nodes:
            title = _clean_text(_first_child_text(node, ["title"]))
            link = _first_link(node)
            summary = _clean_text(
                _first_child_text(node, ["description", "summary", "content", "encoded"])
            )
            published = _first_child_text(node, ["pubDate", "updated", "published", "date"])
            raw_source = forced_source or _first_child_text(node, ["source"]) or feed_name
            source = _canonical_source(raw_source)

            if google_mode and source not in TRUSTED_PUBLISHERS:
                continue

            story = self._analyze_story(
                title=title,
                summary=summary,
                link=link,
                source=source,
                feed_name=feed_name,
                published=published,
                prices=prices or {},
            )
            if story:
                stories.append(story)

        return stories

    def _analyze_story(
        self,
        title: str,
        summary: str,
        link: str,
        source: str,
        feed_name: str,
        published: str,
        prices: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not title:
            return None

        text = f"{title} {summary}".strip()
        lowered = text.lower()
        if not _is_relevant_text(lowered):
            return None

        gold_score = 0
        oil_score = 0
        eurusd_score = 0
        reasons: List[str] = []
        themes: List[str] = []

        if GOLD_RELEVANCE.search(text):
            gold_score += 6
        if OIL_RELEVANCE.search(text):
            oil_score += 6
        if FX_RELEVANCE.search(text):
            eurusd_score += 6

        for signal in SIGNALS:
            if signal["pattern"].search(text):
                gold_score += signal["effects"].get("gold", 0)
                oil_score += signal["effects"].get("oil", 0)
                eurusd_score += signal["effects"].get("eurusd", 0)
                themes.append(signal["label"])
                reasons.append(signal["reason"])

        if "breaking" in lowered or "urgent" in lowered:
            if gold_score:
                gold_score = int(gold_score * 1.1)
            if oil_score:
                oil_score = int(oil_score * 1.1)
            if eurusd_score:
                eurusd_score = int(eurusd_score * 1.1)

        if not gold_score and not oil_score and not eurusd_score:
            gold_score = 4 if GOLD_RELEVANCE.search(text) else 0
            oil_score = 4 if OIL_RELEVANCE.search(text) else 0
            eurusd_score = 4 if FX_RELEVANCE.search(text) else 0

        if not gold_score and not oil_score and not eurusd_score:
            return None

        primary_reason = reasons[0] if reasons else "Trusted source headline matched the market watchlist."
        gold_price = (prices.get("gold") or {}).get("price")
        oil_price = (prices.get("oil") or {}).get("price")
        eurusd_price = (prices.get("eurusd") or {}).get("price")
        gold_impact = _impact_payload(
            "gold",
            gold_score,
            source,
            primary_reason,
            gold_price,
            digits=PRICE_FEEDS["gold"]["priceDigits"],
            relevance_boost=6 if GOLD_RELEVANCE.search(text) else 0,
        )
        oil_impact = _impact_payload(
            "oil",
            oil_score,
            source,
            primary_reason,
            oil_price,
            digits=PRICE_FEEDS["oil"]["priceDigits"],
            relevance_boost=6 if OIL_RELEVANCE.search(text) else 0,
        )
        eurusd_impact = _impact_payload(
            "eurusd",
            eurusd_score,
            source,
            primary_reason,
            eurusd_price,
            digits=PRICE_FEEDS["eurusd"]["priceDigits"],
            relevance_boost=6 if FX_RELEVANCE.search(text) else 0,
        )

        peak_confidence = max(
            gold_impact["confidence"],
            oil_impact["confidence"],
            eurusd_impact["confidence"],
        )
        published_dt = _parse_datetime(published)
        recency_bonus = 0
        age_minutes: Optional[int] = None
        if published_dt:
            age_seconds = max((datetime.now(timezone.utc) - published_dt).total_seconds(), 0)
            recency_bonus = max(0, int(18 - (age_seconds / 3600)))
            age_minutes = int(age_seconds // 60)

        is_breaking = "breaking" in lowered or "urgent" in lowered or (
            age_minutes is not None and age_minutes <= 240 and peak_confidence >= 68
        )

        story_id = _stable_id(title, link, published, source)
        return {
            "id": story_id,
            "kind": "news",
            "alertMode": "breaking" if is_breaking else "watch",
            "title": title,
            "summary": summary or primary_reason,
            "link": link,
            "source": source,
            "channel": feed_name,
            "publishedAt": published_dt.isoformat() if published_dt else published,
            "publishedLabel": _format_time_label(published),
            "ageMinutes": age_minutes,
            "breaking": is_breaking,
            "themes": themes[:4],
            "severity": _severity_from_confidence(peak_confidence),
            "assetImpacts": {
                "gold": gold_impact,
                "oil": oil_impact,
                "eurusd": eurusd_impact,
            },
            "sortScore": peak_confidence + recency_bonus,
        }

    def _fetch_news_items(
        self, prices: Dict[str, Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        stories: List[Dict[str, Any]] = []
        status_rows: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=min(6, len(NEWS_FEEDS))) as executor:
            futures = [
                executor.submit(self._fetch_single_news_feed, feed, prices)
                for feed in NEWS_FEEDS
            ]
            for future in as_completed(futures):
                parsed, status = future.result()
                stories.extend(parsed)
                status_rows.append(status)

        deduped: Dict[str, Dict[str, Any]] = {}
        for story in stories:
            key = _normalize_title(story["title"])
            existing = deduped.get(key)
            if existing is None or story["sortScore"] > existing["sortScore"]:
                deduped[key] = story

        ordered = sorted(deduped.values(), key=lambda item: item["sortScore"], reverse=True)
        status_rows.sort(key=lambda row: row["id"])
        return ordered, status_rows

    def _fetch_single_news_feed(
        self, feed: Dict[str, Any], prices: Dict[str, Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        status = {
            "id": feed["id"],
            "type": "news",
            "label": feed["name"],
            "healthy": False,
        }

        try:
            xml_text = self._fetch_text(
                feed["url"],
                accept="application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
            )
            parsed = self._parse_rss_items(
                xml_text=xml_text,
                feed_name=feed["name"],
                forced_source=feed.get("forcedSource"),
                google_mode=feed["kind"] == "google_rss",
                prices=prices,
            )
            status.update({"healthy": True, "count": len(parsed)})
            return parsed, status
        except (HTTPError, URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            status["error"] = str(exc)
            return [], status

    def _fetch_calendar_items(
        self, prices: Dict[str, Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        status_rows: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=min(2, len(CALENDAR_FEEDS))) as executor:
            futures = [
                executor.submit(self._fetch_single_calendar_feed, feed, prices)
                for feed in CALENDAR_FEEDS
            ]
            for future in as_completed(futures):
                parsed, status = future.result()
                status_rows.append(status)
                if parsed and not events:
                    events = parsed

        status_rows.sort(key=lambda row: row["id"])
        return sorted(events, key=lambda item: item["sortScore"], reverse=True), status_rows

    def _fetch_single_calendar_feed(
        self, feed: Dict[str, Any], prices: Dict[str, Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        status = {
            "id": feed["id"],
            "type": "calendar",
            "label": feed["name"],
            "healthy": False,
        }

        try:
            xml_text = self._fetch_text(
                feed["url"],
                accept="application/xml, text/xml;q=0.9, */*;q=0.5",
            )
            events = self._parse_forex_factory_events(xml_text, prices)
            status.update({"healthy": True, "count": len(events)})
            return events, status
        except (HTTPError, URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            status["error"] = str(exc)
            return [], status

    def _parse_forex_factory_events(
        self, xml_text: str, prices: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        root = ET.fromstring(xml_text)
        events: List[Dict[str, Any]] = []

        for node in root.findall(".//event"):
            payload: Dict[str, str] = {}
            for child in node:
                payload[_local_name(child.tag)] = _clean_text("".join(child.itertext()))

            title = payload.get("title") or payload.get("event") or payload.get("name")
            if not title:
                continue

            currency = (payload.get("country") or payload.get("currency") or "").upper()
            impact_text = payload.get("impact") or payload.get("importance") or "Low"
            impact_rank = _impact_rank(impact_text)
            title_lower = title.lower()

            is_relevant = impact_rank >= 1 and (
                currency in {"USD", "EUR"}
                or any(
                    keyword in title_lower
                    for keyword in (
                        "crude",
                        "oil",
                        "inventory",
                        "inventories",
                        "eia",
                        "opec",
                        "ecb",
                        "lagarde",
                    )
                )
            )
            if not is_relevant:
                continue

            actual = payload.get("actual", "")
            forecast = payload.get("forecast", "")
            previous = payload.get("previous", "")
            date_text = payload.get("date", "")
            time_text = payload.get("time", "")
            event = self._analyze_calendar_event(
                title=title,
                currency=currency,
                impact_text=impact_text,
                actual=actual,
                forecast=forecast,
                previous=previous,
                date_text=date_text,
                time_text=time_text,
                link=payload.get("url") or "https://www.forexfactory.com/calendar",
                prices=prices,
            )
            if event:
                events.append(event)

        return events

    def _analyze_calendar_event(
        self,
        title: str,
        currency: str,
        impact_text: str,
        actual: str,
        forecast: str,
        previous: str,
        date_text: str,
        time_text: str,
        link: str,
        prices: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        title_lower = title.lower()
        status = "released" if actual else "scheduled"
        scheduled_at = _parse_calendar_scheduled_at(date_text, time_text) if status == "scheduled" else None
        timing_note = _calendar_timing_note(time_text)
        compare_value = _parse_numeric_value(forecast) if forecast else _parse_numeric_value(previous)
        actual_value = _parse_numeric_value(actual)

        gold_score = 0
        oil_score = 0
        eurusd_score = 0
        explanation = "High-impact macro risk can shift USD, yields, and commodity pricing."
        alert_mode = "watch"

        if "crude" in title_lower or "inventory" in title_lower or "eia" in title_lower:
            alert_mode = "breaking" if status == "released" else "watch"
            explanation = "Crude inventory surprises usually move near-term oil pricing fast."
            if actual_value is not None and compare_value is not None:
                delta = actual_value - compare_value
                if delta < 0:
                    oil_score = 20
                    explanation = "A deeper-than-expected draw usually tightens the oil balance and supports WTI."
                elif delta > 0:
                    oil_score = -20
                    explanation = "A bigger-than-expected build usually weighs on WTI."
            else:
                oil_score = 4
        elif "ecb" in title_lower or "lagarde" in title_lower or (
            currency == "EUR" and "interest rate" in title_lower
        ):
            alert_mode = "breaking" if status == "released" else "watch"
            explanation = "ECB surprises can reprice EUR/USD quickly."
            if actual_value is not None and compare_value is not None:
                if actual_value > compare_value:
                    eurusd_score = 18
                elif actual_value < compare_value:
                    eurusd_score = -18
            else:
                eurusd_score = 3
        elif any(token in title_lower for token in ("cpi", "pce", "inflation")):
            alert_mode = "breaking" if status == "released" else "watch"
            explanation = "Inflation surprises usually move yields and the dollar, which can hit gold fast."
            if actual_value is not None and compare_value is not None:
                if currency == "EUR":
                    if actual_value > compare_value:
                        eurusd_score = 14
                        explanation = "Hot eurozone inflation can keep ECB pricing firmer and support EUR/USD."
                    elif actual_value < compare_value:
                        eurusd_score = -14
                        explanation = "Cooler eurozone inflation can soften ECB expectations and weigh on EUR/USD."
                else:
                    if actual_value > compare_value:
                        gold_score = -18
                        oil_score = 4
                        eurusd_score = -16
                        explanation = "Hotter US inflation usually boosts yields and the dollar first."
                    elif actual_value < compare_value:
                        gold_score = 18
                        eurusd_score = 16
                        explanation = "Cooler US inflation can reduce rate pressure and support gold and EUR/USD."
            else:
                gold_score = 2 if currency == "USD" else 0
                eurusd_score = 2 if currency in {"USD", "EUR"} else 0
        elif any(token in title_lower for token in ("fomc", "interest rate", "fed", "powell")):
            alert_mode = "breaking" if status == "released" else "watch"
            explanation = "Fed events often reset gold direction and EUR/USD through dollar expectations."
            if actual_value is not None and compare_value is not None:
                if actual_value < compare_value:
                    gold_score = 18
                    oil_score = 6
                    eurusd_score = 18
                elif actual_value > compare_value:
                    gold_score = -18
                    oil_score = -6
                    eurusd_score = -18
            else:
                gold_score = 3
                eurusd_score = 3
        elif any(token in title_lower for token in ("payroll", "nfp", "jobless", "unemployment", "jobs")):
            alert_mode = "breaking" if status == "released" else "watch"
            explanation = "Labor surprises often move the dollar, gold, and EUR/USD immediately."
            if actual_value is not None and compare_value is not None:
                if "jobless" in title_lower or "unemployment" in title_lower:
                    if actual_value > compare_value:
                        gold_score = 12
                        oil_score = -8
                        eurusd_score = 10
                    elif actual_value < compare_value:
                        gold_score = -12
                        oil_score = 8
                        eurusd_score = -10
                else:
                    if actual_value > compare_value:
                        gold_score = -12
                        oil_score = 8
                        eurusd_score = -12
                    elif actual_value < compare_value:
                        gold_score = 12
                        oil_score = -8
                        eurusd_score = 12
            else:
                gold_score = 2
                eurusd_score = 2
        elif any(token in title_lower for token in ("gdp", "pmi", "retail sales")):
            alert_mode = "breaking" if status == "released" else "watch"
            explanation = "Growth data matters for oil demand and can also move EUR/USD through rate expectations."
            if actual_value is not None and compare_value is not None:
                if currency == "EUR":
                    if actual_value > compare_value:
                        eurusd_score = 12
                    elif actual_value < compare_value:
                        eurusd_score = -12
                else:
                    if actual_value > compare_value:
                        oil_score = 12
                        gold_score = -8
                        eurusd_score = -10
                    elif actual_value < compare_value:
                        oil_score = -12
                        gold_score = 8
                        eurusd_score = 10
            else:
                oil_score = 3 if currency == "USD" else 0
                eurusd_score = 3 if currency in {"USD", "EUR"} else 0
        else:
            if currency == "USD":
                gold_score = 2
                eurusd_score = 2
            elif currency == "EUR":
                eurusd_score = 2

        gold_price = (prices.get("gold") or {}).get("price")
        oil_price = (prices.get("oil") or {}).get("price")
        eurusd_price = (prices.get("eurusd") or {}).get("price")

        if status == "scheduled" and not gold_score and not oil_score and not eurusd_score:
            gold_mixed = 56 if currency == "USD" else 0
            oil_mixed = 56 if any(token in title_lower for token in ("crude", "oil", "inventory", "gdp", "pmi")) else 0
            eurusd_mixed = 56 if currency in {"USD", "EUR"} else 0
            gold_impact = _impact_payload(
                "gold",
                0,
                "Forex Factory",
                explanation,
                gold_price,
                digits=PRICE_FEEDS["gold"]["priceDigits"],
                mixed_confidence=gold_mixed,
            )
            oil_impact = _impact_payload(
                "oil",
                0,
                "Forex Factory",
                explanation,
                oil_price,
                digits=PRICE_FEEDS["oil"]["priceDigits"],
                mixed_confidence=oil_mixed,
            )
            eurusd_impact = _impact_payload(
                "eurusd",
                0,
                "Forex Factory",
                explanation,
                eurusd_price,
                digits=PRICE_FEEDS["eurusd"]["priceDigits"],
                mixed_confidence=eurusd_mixed,
            )
        else:
            gold_impact = _impact_payload(
                "gold",
                gold_score,
                "Forex Factory",
                explanation,
                gold_price,
                digits=PRICE_FEEDS["gold"]["priceDigits"],
                mixed_confidence=56 if status == "scheduled" and currency == "USD" else 0,
            )
            oil_impact = _impact_payload(
                "oil",
                oil_score,
                "Forex Factory",
                explanation,
                oil_price,
                digits=PRICE_FEEDS["oil"]["priceDigits"],
                mixed_confidence=56 if status == "scheduled" and ("crude" in title_lower or "oil" in title_lower) else 0,
            )
            eurusd_impact = _impact_payload(
                "eurusd",
                eurusd_score,
                "Forex Factory",
                explanation,
                eurusd_price,
                digits=PRICE_FEEDS["eurusd"]["priceDigits"],
                mixed_confidence=56 if status == "scheduled" and currency in {"USD", "EUR"} else 0,
            )

        peak_confidence = max(
            gold_impact["confidence"],
            oil_impact["confidence"],
            eurusd_impact["confidence"],
        )
        sort_score = peak_confidence + (8 if status == "released" else 2)
        summary = (
            f"{currency or 'Macro'} | {impact_text} impact | "
            f"actual {actual or '--'} | forecast {forecast or '--'} | previous {previous or '--'}"
        )

        return {
            "id": _stable_id(title, date_text, time_text, actual, forecast, previous),
            "kind": "calendar",
            "alertMode": alert_mode,
            "title": title,
            "summary": summary,
            "link": link,
            "source": "Forex Factory",
            "channel": "Macro calendar",
            "publishedAt": date_text,
            "publishedLabel": f"{date_text} {time_text}".strip() or "Calendar event",
            "scheduledAt": scheduled_at.isoformat() if scheduled_at else None,
            "timingNote": timing_note,
            "themes": [impact_text.title(), status.title()],
            "impactLevel": impact_text.lower(),
            "impactRank": _impact_rank(impact_text),
            "severity": _severity_from_confidence(peak_confidence),
            "eventStatus": status,
            "assetImpacts": {
                "gold": gold_impact,
                "oil": oil_impact,
                "eurusd": eurusd_impact,
            },
            "sortScore": sort_score,
        }

    def _build_asset_summary(
        self, asset: str, price_info: Optional[Dict[str, Any]], items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        impacts = []
        for item in items:
            impact = item["assetImpacts"][asset]
            if impact["confidence"] <= 0:
                continue
            impacts.append((item, impact))

        label = PRICE_FEEDS[asset]["label"]
        proxy_label = PRICE_FEEDS[asset]["proxyLabel"]
        price = (price_info or {}).get("price")
        change_pct = (price_info or {}).get("changePct")
        price_digits = PRICE_FEEDS[asset].get("priceDigits", 2)

        if not impacts:
            return {
                "asset": asset,
                "label": label,
                "proxyLabel": proxy_label,
                "price": price,
                "currency": (price_info or {}).get("currency") or "USD",
                "changePct": change_pct,
                "priceDigits": price_digits,
                "bias": "mixed",
                "confidence": 0,
                "summary": "No live catalyst scored yet. The radar is waiting for fresh trusted headlines.",
                "zone": None,
                "moveText": "Waiting for catalysts",
                "catalysts": [],
                "series": (price_info or {}).get("series") or [],
                "dayLow": (price_info or {}).get("dayLow"),
                "dayHigh": (price_info or {}).get("dayHigh"),
            }

        signed = 0
        total = 0
        mixed_weight = 0
        move_low_weighted = 0.0
        move_high_weighted = 0.0

        for _, impact in impacts:
            confidence = impact["confidence"]
            total += confidence
            move_low_weighted += impact["moveLowPct"] * max(confidence, 1)
            move_high_weighted += impact["moveHighPct"] * max(confidence, 1)
            if impact["bias"] == "up":
                signed += confidence
            elif impact["bias"] == "down":
                signed -= confidence
            elif impact["bias"] == "mixed":
                mixed_weight += confidence

        ratio = signed / max(total, 1)
        if abs(ratio) < 0.15 and mixed_weight >= total * 0.35:
            bias = "mixed"
        elif ratio > 0:
            bias = "up"
        elif ratio < 0:
            bias = "down"
        else:
            bias = "mixed"

        confidence = int(min(97, abs(ratio) * 100 + min(16, total / 12)))
        if bias == "mixed":
            confidence = int(min(82, max(52, mixed_weight / max(len(impacts), 1))))

        avg_low = round(move_low_weighted / max(total, 1), 2)
        avg_high = round(move_high_weighted / max(total, 1), 2)
        zone = _price_zone(price, bias, avg_low, avg_high, digits=price_digits)
        catalysts = [
            {
                "title": item["title"],
                "source": item["source"],
                "reason": impact["reason"],
            }
            for item, impact in impacts[:3]
        ]

        if bias == "up":
            if asset == "eurusd":
                summary = "EUR/USD has an upside reaction bias, which usually means euro strength or a softer dollar."
            else:
                summary = f"{label} has an upside reaction bias from the strongest current catalysts."
            move_text = (
                f"Estimated reaction area: +{avg_low:.2f}% to +{avg_high:.2f}%"
                if not zone
                else f"Estimated upside zone: {zone['label']}"
            )
        elif bias == "down":
            if asset == "eurusd":
                summary = "EUR/USD has a downside reaction bias, which usually means dollar strength or softer euro pricing."
            else:
                summary = f"{label} has a downside reaction bias from the strongest current catalysts."
            move_text = (
                f"Estimated reaction area: -{avg_high:.2f}% to -{avg_low:.2f}%"
                if not zone
                else f"Estimated downside zone: {zone['label']}"
            )
        else:
            summary = (
                f"{label} is in high-volatility mode. Catalyst pressure is mixed, so expect chop until a cleaner signal lands."
            )
            move_text = f"Estimated swing size: +/-{avg_low:.2f}% to +/-{avg_high:.2f}%"

        return {
            "asset": asset,
            "label": label,
            "proxyLabel": proxy_label,
            "price": price,
            "currency": (price_info or {}).get("currency") or "USD",
            "changePct": change_pct,
            "priceDigits": price_digits,
            "bias": bias,
            "confidence": confidence,
            "summary": summary,
            "zone": zone,
            "moveText": move_text,
            "catalysts": catalysts,
            "series": (price_info or {}).get("series") or [],
            "dayLow": (price_info or {}).get("dayLow"),
            "dayHigh": (price_info or {}).get("dayHigh"),
        }

    def _build_alerts(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        alerts = []
        for item in items:
            peak_conf = max(impact["confidence"] for impact in item["assetImpacts"].values())
            if item["severity"] in {"critical", "major"} or (
                item["kind"] == "calendar" and item.get("eventStatus") == "scheduled"
            ):
                alert = dict(item)
                alert["peakConfidence"] = peak_conf
                alerts.append(alert)
        return sorted(alerts, key=lambda item: item.get("sortScore", 0), reverse=True)

    def _build_warnings(
        self,
        news_items: List[Dict[str, Any]],
        calendar_items: List[Dict[str, Any]],
        news_status: List[Dict[str, Any]],
        calendar_status: List[Dict[str, Any]],
        price_status: List[Dict[str, Any]],
    ) -> List[str]:
        warnings: List[str] = []

        if not any(row.get("healthy") for row in price_status):
            warnings.append(
                "Live quote proxies are unavailable, so target zones fall back to percentage ranges only."
            )
        if not any(row.get("healthy") for row in news_status):
            warnings.append(
                "Trusted news feeds are unreachable right now. Check your network if the feed stays empty."
            )
        if not any(row.get("healthy") for row in calendar_status):
            warnings.append(
                "Forex Factory calendar data is unavailable right now. The news radar still works without it."
            )
        if not news_items and not calendar_items:
            warnings.append(
                "No live catalysts were parsed yet. That usually means upstream feeds are blocked or temporarily empty."
            )

        return warnings
