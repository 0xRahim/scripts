#!/usr/bin/env python3
"""
Google API Key Checker
Validates an API key starting with 'AIza' and probes its enabled permissions
across common Google APIs.
"""

import sys
import re
import json
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass

# ──────────────────────────────────────────────
# API endpoints to probe and what they test
# ──────────────────────────────────────────────
PROBES = [
    {
        "name": "Maps Geocoding API",
        "url": "https://maps.googleapis.com/maps/api/geocode/json?address=New+York&key={key}",
        "ok_field": "status",
        "ok_values": ["OK", "ZERO_RESULTS"],
    },
    {
        "name": "Maps JavaScript / Places API",
        "url": "https://maps.googleapis.com/maps/api/place/nearbysearch/json?location=40.7128,-74.0060&radius=100&key={key}",
        "ok_field": "status",
        "ok_values": ["OK", "ZERO_RESULTS"],
    },
    {
        "name": "Maps Distance Matrix API",
        "url": "https://maps.googleapis.com/maps/api/distancematrix/json?origins=New+York&destinations=Boston&key={key}",
        "ok_field": "status",
        "ok_values": ["OK", "ZERO_RESULTS"],
    },
    {
        "name": "Maps Directions API",
        "url": "https://maps.googleapis.com/maps/api/directions/json?origin=New+York&destination=Boston&key={key}",
        "ok_field": "status",
        "ok_values": ["OK", "ZERO_RESULTS"],
    },
    {
        "name": "Maps Elevation API",
        "url": "https://maps.googleapis.com/maps/api/elevation/json?locations=39.7391536,-104.9847034&key={key}",
        "ok_field": "status",
        "ok_values": ["OK"],
    },
    {
        "name": "Maps Time Zone API",
        "url": "https://maps.googleapis.com/maps/api/timezone/json?location=39.6034810,-119.6822510&timestamp=1331766000&key={key}",
        "ok_field": "status",
        "ok_values": ["OK"],
    },
    {
        "name": "YouTube Data API v3",
        "url": "https://www.googleapis.com/youtube/v3/videos?part=id&chart=mostPopular&maxResults=1&key={key}",
        "ok_field": "kind",
        "ok_values": ["youtube#videoListResponse"],
    },
    {
        "name": "Custom Search API",
        "url": "https://www.googleapis.com/customsearch/v1?q=test&key={key}",
        "ok_field": "kind",
        "ok_values": ["customsearch#search"],
    },
    {
        "name": "Books API",
        "url": "https://www.googleapis.com/books/v1/volumes?q=python&key={key}",
        "ok_field": "kind",
        "ok_values": ["books#volumes"],
    },
    {
        "name": "Natural Language API",
        "url": "https://language.googleapis.com/v1/documents:analyzeEntities?key={key}",
        "method": "POST",
        "body": json.dumps({
            "document": {"type": "PLAIN_TEXT", "content": "Hello world"},
            "encodingType": "UTF8"
        }),
        "ok_field": "entities",
        "ok_values": [None],   # presence of key is enough
    },
    {
        "name": "Vision API",
        "url": "https://vision.googleapis.com/v1/images:annotate?key={key}",
        "method": "POST",
        "body": json.dumps({
            "requests": [{
                "image": {"source": {"imageUri": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png"}},
                "features": [{"type": "LABEL_DETECTION", "maxResults": 1}]
            }]
        }),
        "ok_field": "responses",
        "ok_values": [None],
    },
    {
        "name": "Translation API",
        "url": "https://translation.googleapis.com/language/translate/v2?q=Hello&target=es&key={key}",
        "ok_field": "data",
        "ok_values": [None],
    },
    # ── Gemini (two probes: model list + text generation) ──
    {
        "name": "Gemini API — List Models",
        "url": "https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        "ok_field": "models",
        "ok_values": [None],
    },
    {
        "name": "Gemini API — Generate Content",
        "url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
        "method": "POST",
        "body": json.dumps({
            "contents": [{"parts": [{"text": "Say hello in one word."}]}]
        }),
        "ok_field": "candidates",
        "ok_values": [None],
    },
    # ── Google Drive (requires OAuth for real access; we detect if the API
    #    is enabled by checking the error type the key returns) ──
    {
        "name": "Google Drive API — About",
        "url": "https://www.googleapis.com/drive/v3/about?fields=kind&key={key}",
        "ok_field": "kind",
        "ok_values": ["drive#about"],
        "oauth_probe": True,          # Drive needs OAuth; flag for special handling
    },
    {
        "name": "Google Drive API — Files List",
        "url": "https://www.googleapis.com/drive/v3/files?pageSize=1&key={key}",
        "ok_field": "kind",
        "ok_values": ["drive#fileList"],
        "oauth_probe": True,
    },
]

# Error messages that mean "key valid but API not enabled"
NOT_ENABLED_MESSAGES = [
    "API_NOT_ACTIVATED",
    "SERVICE_DISABLED",
    "accessNotConfigured",
    "API has not been used",
    "it is disabled",
    "Enable it by visiting",
    "REQUEST_DENIED",
    "disabled",
]

# Error messages that mean the key itself is bad
INVALID_KEY_MESSAGES = [
    "API_KEY_INVALID",
    "keyInvalid",
    "API key not valid",
    "Invalid API key",
]

# Signals that the API is enabled but OAuth is required (key alone isn't enough)
OAUTH_REQUIRED_MESSAGES = [
    "LOGIN_REQUIRED",
    "authError",
    "insufficientPermissions",
    "requires OAuth",
    "Request had invalid authentication credentials",
    "UNAUTHENTICATED",
]


@dataclass
class ProbeResult:
    name: str
    status: str          # "ENABLED" | "DISABLED" | "INVALID_KEY" | "ERROR"
    detail: str


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def validate_key_format(key: str) -> bool:
    return bool(re.match(r"^AIza[0-9A-Za-z\-_]{35}$", key))


def http_request(url: str, method: str = "GET", body: str = None) -> dict:
    """Tiny HTTP helper — no third-party dependencies needed."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    data = body.encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"_raw_error": raw, "_http_status": e.code}
    except Exception as exc:
        return {"_exception": str(exc)}


def classify_response(resp: dict, probe: dict) -> tuple[str, str]:
    """Return (status, detail) for a probe response."""
    raw_str = json.dumps(resp)

    # Network / parse error
    if "_exception" in resp:
        return "ERROR", resp["_exception"]

    # Check for invalid key signals anywhere in the response
    for msg in INVALID_KEY_MESSAGES:
        if msg.lower() in raw_str.lower():
            return "INVALID_KEY", "API key is invalid or revoked"

    # For OAuth-gated APIs (e.g. Drive): check OAuth signals BEFORE "not enabled"
    if probe.get("oauth_probe"):
        for msg in OAUTH_REQUIRED_MESSAGES:
            if msg.lower() in raw_str.lower():
                return "OAUTH_REQUIRED", "API enabled — OAuth/service-account needed for full access"

    # Check for "API not enabled" signals
    for msg in NOT_ENABLED_MESSAGES:
        if msg.lower() in raw_str.lower():
            return "DISABLED", "API not enabled for this key"

    # Check expected success field
    ok_field = probe.get("ok_field")
    ok_values = probe.get("ok_values", [])

    if ok_field and ok_field in resp:
        val = resp[ok_field]
        if None in ok_values:          # presence alone is enough
            return "ENABLED", f"{ok_field} present in response"
        if val in ok_values:
            return "ENABLED", f"{ok_field} = {val}"

    # HTTP-level error with no matching signal
    if "_http_status" in resp:
        return "ERROR", f"HTTP {resp['_http_status']}: {resp.get('_raw_error', '')[:120]}"

    return "ERROR", f"Unexpected response: {raw_str[:120]}"


def probe_api(key: str, probe: dict) -> ProbeResult:
    url = probe["url"].format(key=key)
    method = probe.get("method", "GET")
    body = probe.get("body")
    resp = http_request(url, method=method, body=body)
    status, detail = classify_response(resp, probe)
    return ProbeResult(name=probe["name"], status=status, detail=detail)


# ──────────────────────────────────────────────
# Pretty printing
# ──────────────────────────────────────────────

COLORS = {
    "ENABLED":        "\033[92m",   # green
    "DISABLED":       "\033[93m",   # yellow
    "OAUTH_REQUIRED": "\033[94m",   # blue
    "INVALID_KEY":    "\033[91m",   # red
    "ERROR":          "\033[90m",   # grey
    "RESET":          "\033[0m",
    "BOLD":           "\033[1m",
    "CYAN":           "\033[96m",
}

ICONS = {
    "ENABLED":        "✅",
    "DISABLED":       "🚫",
    "OAUTH_REQUIRED": "🔑",
    "INVALID_KEY":    "❌",
    "ERROR":          "⚠️ ",
}


def colored(text: str, color: str) -> str:
    return f"{COLORS.get(color, '')}{text}{COLORS['RESET']}"


def print_banner():
    print(colored("\n╔══════════════════════════════════════════╗", "CYAN"))
    print(colored("║       Google API Key Permission Checker  ║", "CYAN"))
    print(colored("╚══════════════════════════════════════════╝\n", "CYAN"))


def print_summary(key: str, results: list[ProbeResult]):
    enabled  = [r for r in results if r.status == "ENABLED"]
    oauth    = [r for r in results if r.status == "OAUTH_REQUIRED"]
    disabled = [r for r in results if r.status == "DISABLED"]
    errors   = [r for r in results if r.status == "ERROR"]
    invalid  = [r for r in results if r.status == "INVALID_KEY"]

    masked = key[:8] + "..." + key[-4:]
    print(colored(f"\n{'─'*60}", "CYAN"))
    print(colored(f"  Key : {masked}", "BOLD"))
    print(colored(f"{'─'*60}", "CYAN"))

    col_w = 38
    for r in results:
        icon  = ICONS[r.status]
        color = r.status
        label = colored(r.name.ljust(col_w), color)
        tag   = colored(f"[{r.status}]".ljust(16), color)
        print(f"  {icon}  {label} {tag}  {r.detail}")

    print(colored(f"\n{'─'*60}", "CYAN"))
    print(f"  Total probed   : {len(results)}")
    print(colored(f"  Enabled        : {len(enabled)}", "ENABLED"))
    print(colored(f"  OAuth required : {len(oauth)}", "OAUTH_REQUIRED"))
    print(colored(f"  Disabled       : {len(disabled)}", "DISABLED"))
    print(colored(f"  Errors         : {len(errors)}", "ERROR"))
    if invalid:
        print(colored(f"  Invalid key    : {len(invalid)}", "INVALID_KEY"))
    print(colored(f"{'─'*60}\n", "CYAN"))

    if invalid:
        print(colored("  ⛔  Key appears to be INVALID or REVOKED.\n", "INVALID_KEY"))
    elif not enabled and not oauth:
        print(colored("  ⚠️   No APIs appear to be enabled for this key.\n", "DISABLED"))
    else:
        active = len(enabled) + len(oauth)
        print(colored(f"  🎉  Key is valid — {len(enabled)} API(s) fully enabled, "
                      f"{len(oauth)} require OAuth.\n", "ENABLED"))
    if oauth:
        print(colored("  🔑  Google Drive APIs are enabled but need OAuth 2.0 or a\n"
                      "      service account for actual data access.\n", "OAUTH_REQUIRED"))


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main():
    print_banner()

    # Accept key from CLI or prompt
    if len(sys.argv) > 1:
        api_key = sys.argv[1].strip()
    else:
        api_key = input("  Enter your Google API key (AIza...): ").strip()

    # Basic format validation
    if not api_key.startswith("AIza"):
        print(colored("  ❌  Key must start with 'AIza'. Aborting.\n", "INVALID_KEY"))
        sys.exit(1)

    if not validate_key_format(api_key):
        print(colored("  ⚠️   Key format looks unusual — continuing anyway…\n", "DISABLED"))

    print(f"\n  Probing {len(PROBES)} Google APIs — please wait…\n")

    results = []
    for probe in PROBES:
        print(f"  → Checking {probe['name']}…", end="\r", flush=True)
        result = probe_api(api_key, probe)
        results.append(result)

        # If any probe conclusively says the key is invalid, stop early
        if result.status == "INVALID_KEY":
            print(" " * 60, end="\r")   # clear the line
            print(colored(f"  ❌  {probe['name']} : key is INVALID — stopping early.", "INVALID_KEY"))
            break

    print(" " * 60, end="\r")  # clear last "Checking…" line
    print_summary(api_key, results)


if __name__ == "__main__":
    main()
