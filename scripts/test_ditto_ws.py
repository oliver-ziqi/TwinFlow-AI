import argparse
import asyncio
import base64
import inspect
import json
import time
from typing import Dict, List
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import websockets


def parse_headers(raw_headers: List[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for item in raw_headers:
        if ":" not in item:
            raise ValueError(f"Invalid header format: {item}. Use 'Key: Value'")
        k, v = item.split(":", 1)
        headers[k.strip()] = v.strip()
    return headers


def build_auth_header(args: argparse.Namespace) -> Dict[str, str]:
    if args.username and args.password:
        token = f"{args.username}:{args.password}"
        encoded = base64.b64encode(token.encode("utf-8")).decode("utf-8")
        return {"Authorization": f"Basic {encoded}"}
    if args.basic_auth:
        token = base64.b64encode(args.basic_auth.encode("utf-8")).decode("utf-8")
        return {"Authorization": f"Basic {token}"}
    if args.bearer_token:
        return {"Authorization": f"Bearer {args.bearer_token}"}
    return {}


def with_access_token_query(url: str, access_token: str) -> str:
    if not access_token:
        return url
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    qs["access_token"] = access_token
    new_query = urlencode(qs)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def build_subscribe_message(args: argparse.Namespace) -> str:
    mapping = {
        "events": "START-SEND-EVENTS",
        "messages": "START-SEND-MESSAGES",
        "live-commands": "START-SEND-LIVE-COMMANDS",
        "live-events": "START-SEND-LIVE-EVENTS",
        "policy-announcements": "START-SEND-POLICY-ANNOUNCEMENTS",
    }
    base = mapping[args.subscribe]

    query = {}
    if args.extra_fields:
        query["extraFields"] = args.extra_fields
    if args.namespaces:
        query["namespaces"] = args.namespaces
    if args.filter:
        query["filter"] = args.filter

    if not query:
        return base
    return f"{base}?{urlencode(query)}"


def build_ditto_protocol_message(args: argparse.Namespace) -> str:
    payload: Dict[str, object] = {
        "topic": args.topic,
        "headers": {"correlation-id": args.correlation_id},
        "path": args.path,
    }
    if args.value_json:
        payload["value"] = json.loads(args.value_json)
    elif args.value_string:
        payload["value"] = args.value_string
    return json.dumps(payload, ensure_ascii=False)


async def run_test(args: argparse.Namespace) -> None:
    headers = parse_headers(args.header or [])
    headers.update(build_auth_header(args))
    ws_url = with_access_token_query(args.url, args.access_token_query)

    print(f"[ws-test] connecting: {ws_url}")
    if headers:
        print(f"[ws-test] headers: {headers}")

    start = time.time()
    connect_kwargs = {}
    sig = inspect.signature(websockets.connect)
    if "extra_headers" in sig.parameters:
        connect_kwargs["extra_headers"] = headers
    elif "additional_headers" in sig.parameters:
        connect_kwargs["additional_headers"] = headers
    else:
        raise RuntimeError("Unsupported websockets.connect() signature: cannot set headers.")

    if args.subprotocol:
        connect_kwargs["subprotocols"] = [args.subprotocol]

    try:
        ws_cm = websockets.connect(ws_url, **connect_kwargs)
        async with ws_cm as ws:
            print("[ws-test] connected")

            if args.subscribe:
                subscribe_message = build_subscribe_message(args)
                print(f"[ws-test] send subscribe: {subscribe_message}")
                await ws.send(subscribe_message)

            if args.jwt_refresh:
                refresh_message = f"JWT-TOKEN?jwtToken={args.jwt_refresh}"
                print("[ws-test] send jwt refresh")
                await ws.send(refresh_message)

            if args.topic:
                protocol_message = build_ditto_protocol_message(args)
                print(f"[ws-test] send ditto protocol: {protocol_message}")
                await ws.send(protocol_message)

            if args.send:
                print(f"[ws-test] send raw: {args.send}")
                await ws.send(args.send)

            while True:
                if args.duration > 0 and time.time() - start >= args.duration:
                    print("[ws-test] timeout reached, closing")
                    break

                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                print("[ws-test] recv:")
                try:
                    parsed = json.loads(msg)
                    print(json.dumps(parsed, ensure_ascii=False, indent=2))
                except Exception:
                    print(msg)
    except Exception as e:
        print(f"[ws-test] connect failed: {type(e).__name__}: {e}")
        resp = getattr(e, "response", None)
        if resp is not None:
            code = getattr(resp, "status_code", None) or getattr(resp, "status", None)
            headers_obj = getattr(resp, "headers", None)
            print(f"[ws-test] handshake status: {code}")
            if headers_obj:
                print(f"[ws-test] handshake headers: {headers_obj}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Ditto websocket endpoint")
    parser.add_argument(
        "--url",
        default="ws://192.168.17.128:8080/ws/2",
        help="WebSocket URL",
    )
    parser.add_argument(
        "--basic-auth",
        default="",
        help="Basic auth in 'username:password' format",
    )
    parser.add_argument("--username", default="", help="Username for Basic auth")
    parser.add_argument("--password", default="", help="Password for Basic auth")
    parser.add_argument(
        "--bearer-token",
        default="",
        help="Bearer JWT token for Authorization header",
    )
    parser.add_argument(
        "--access-token-query",
        default="",
        help="JWT token as access_token query parameter (fallback mode)",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="Custom header, e.g. --header 'Authorization: Bearer xxx'",
    )
    parser.add_argument(
        "--subprotocol",
        default="",
        help="Optional websocket subprotocol, e.g. ditto-protocol",
    )
    parser.add_argument(
        "--subscribe",
        choices=[
            "events",
            "messages",
            "live-commands",
            "live-events",
            "policy-announcements",
        ],
        help="Send Ditto websocket binding subscription command",
    )
    parser.add_argument(
        "--extra-fields",
        default="",
        help="For subscribe command, e.g. attributes,cfeatures/status",
    )
    parser.add_argument(
        "--namespaces",
        default="",
        help="For subscribe command, e.g. my.factory,my.queue",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="For subscribe command, e.g. gt(attributes/counter,42)",
    )
    parser.add_argument(
        "--jwt-refresh",
        default="",
        help="Send JWT-TOKEN?jwtToken=<token> after connect",
    )
    parser.add_argument(
        "--topic",
        default="",
        help="Ditto protocol topic, e.g. my.factory/factoryD/things/twin/commands/retrieve",
    )
    parser.add_argument(
        "--path",
        default="/",
        help="Ditto protocol path, e.g. /features/status/properties",
    )
    parser.add_argument(
        "--correlation-id",
        default="ws-test-1",
        help="Ditto protocol correlation-id header value",
    )
    parser.add_argument(
        "--value-json",
        default="",
        help="JSON string for Ditto protocol value field",
    )
    parser.add_argument(
        "--value-string",
        default="",
        help="Plain string for Ditto protocol value field",
    )
    parser.add_argument(
        "--send",
        default="",
        help="Optional text payload to send after connect",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=30,
        help="How many seconds to listen (0 means forever)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_test(args))
    except KeyboardInterrupt:
        print("\n[ws-test] stopped")


if __name__ == "__main__":
    main()
