"""
SET50 RRG — Local Data Server
รัน: python server.py
แล้วเปิด browser ไปที่ http://localhost:8765
"""
import json, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import yfinance as yf

SET50 = [
    "ADVANC.BK","AOT.BK","AWC.BK","BANPU.BK","BBL.BK","BDMS.BK","BEM.BK","BH.BK",
    "BJC.BK","BTS.BK","CBG.BK","CCET.BK","CENTEL.BK","COM7.BK","CPALL.BK","CPF.BK",
    "CPN.BK","CRC.BK","DELTA.BK","EGCO.BK","GPSC.BK","GULF.BK","HMPRO.BK","IVL.BK",
    "KBANK.BK","KKP.BK","KTB.BK","KTC.BK","LH.BK","MINT.BK","MTC.BK","OR.BK",
    "OSP.BK","PTT.BK","PTTEP.BK","PTTGC.BK","RATCH.BK","SAWAD.BK","SCB.BK","SCC.BK",
    "SCGP.BK","TCAP.BK","TIDLOR.BK","TISCO.BK","TLI.BK","TOP.BK","TRUE.BK","TTB.BK",
    "TU.BK","WHA.BK"
]

TF = {
    "5m":      {"period": "10d",  "interval": "5m"},
    "15m":     {"period": "30d",  "interval": "15m"},
    "daily":   {"period": "6mo",  "interval": "1d"},
    "weekly":  {"period": "2y",   "interval": "1wk"},
    "monthly": {"period": "5y",   "interval": "1mo"},
}

BENCHMARK_CANDIDATES = [
    ("^SET50.BK", "SET50 Index"),
    ("^SET.BK", "SET Index (fallback)"),
]

# Cache: (tf) -> {data, ts}
_cache = {}
_lock  = threading.Lock()
CACHE_TTL = 300  # 5 min
SIZE_LOOKBACK = 20

def _normalize_price_frame(df):
    if df is None or df.empty:
        return None
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df:
        return None
    price_frame = df.copy()
    price_frame["Close"] = price_frame["Close"].dropna()
    price_frame = price_frame.dropna(subset=["Close"])
    if price_frame.empty:
        return None
    return price_frame

def _download_close_series(symbol, cfg):
    interval = cfg["interval"]
    # Yahoo monthly bars for SET indices can be sparse or malformed, so
    # derive month-end closes from daily data instead.
    if interval == "1mo":
        interval = "1d"
    raw = yf.download(
        symbol,
        period=cfg["period"],
        interval=interval,
        progress=False,
        auto_adjust=True,
    )
    frame = _normalize_price_frame(raw)
    if frame is None:
        return None
    close = frame["Close"].dropna()
    if cfg["interval"] == "1mo":
        close = close.resample("ME").last().dropna()
    return close

def _build_price_points(frame):
    points = []
    for ts, row in frame.iterrows():
        close = row.get("Close")
        if close is None:
            continue
        volume = row.get("Volume", 0)
        if volume != volume:
            volume = 0
        points.append({
            "t": int(ts.timestamp()),
            "c": float(close),
            "v": float(volume),
        })
    return points

def _estimate_avg_trading_value(points):
    if not points:
        return None
    window = points[-SIZE_LOOKBACK:]
    values = [p["c"] * p.get("v", 0) for p in window if p.get("c") is not None]
    if not values:
        return None
    return float(sum(values) / len(values))

def _load_shares_outstanding(symbol):
    try:
        shares = yf.Ticker(symbol).fast_info.get("shares")
        if shares:
            return float(shares)
    except Exception as e:
        print(f"  shares fast_info miss {symbol}: {e}")

    try:
        shares = yf.Ticker(symbol).info.get("sharesOutstanding")
        if shares:
            return float(shares)
    except Exception as e:
        print(f"  shares info miss {symbol}: {e}")

    return None

def _build_size_metrics(price_map):
    size_map = {}
    total_market_cap = 0.0

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(_load_shares_outstanding, symbol): symbol
            for symbol in price_map
        }
        for future in as_completed(futures):
            symbol = futures[future]
            points = price_map[symbol]
            avg_value = _estimate_avg_trading_value(points)
            last_close = points[-1]["c"] if points else None
            shares = None
            market_cap = None

            try:
                shares = future.result()
            except Exception as e:
                print(f"  size metric skip {symbol}: {e}")

            if shares and last_close:
                market_cap = float(shares * last_close)
                total_market_cap += market_cap

            size_map[symbol] = {
                "market_cap": market_cap,
                "avg_trading_value": avg_value,
                "set50_weight": None,
                "shares_outstanding": shares,
            }

    if total_market_cap > 0:
        for metrics in size_map.values():
            market_cap = metrics["market_cap"]
            if market_cap:
                metrics["set50_weight"] = float((market_cap / total_market_cap) * 100)

    return size_map

def _load_benchmark(cfg):
    errors = []
    for symbol, label in BENCHMARK_CANDIDATES:
        try:
            close = _download_close_series(symbol, cfg)
            if close is None or len(close) < 20:
                errors.append(f"{symbol}: insufficient data")
                continue
            print(f"[server] Benchmark: {symbol} ({len(close)} points)")
            return symbol, label, close
        except Exception as e:
            errors.append(f"{symbol}: {e}")

    raise RuntimeError(
        "ไม่สามารถโหลดข้อมูล benchmark ได้จาก Yahoo Finance "
        + "| ".join(errors)
    )

def fetch_prices(tf="daily"):
    cfg = TF.get(tf, TF["daily"])
    print(f"[server] Downloading {len(SET50)} tickers ({cfg['interval']}/{cfg['period']})...")

    result = {}
    bench_symbol, bench_label, bench_close = _load_benchmark(cfg)
    result["__bench__"] = [
        {"t": int(ts.timestamp()), "c": float(v)}
        for ts, v in bench_close.items()
    ]
    result["__meta__"] = {
        "benchmark_symbol": bench_symbol,
        "benchmark_label": bench_label,
    }

    price_map = {}

    # Download stocks in batches
    batch_size = 10
    for i in range(0, len(SET50), batch_size):
        chunk = SET50[i:i+batch_size]
        print(f"  batch {i//batch_size+1}: {chunk}")
        raw = yf.download(chunk, period=cfg["period"], interval=cfg["interval"],
                          progress=False, auto_adjust=True, group_by="ticker")
        for tk in chunk:
            try:
                if len(chunk) == 1:
                    df = raw
                else:
                    df = raw[tk] if tk in raw.columns.get_level_values(0) else None
                frame = _normalize_price_frame(df)
                if frame is None:
                    result[tk] = []
                    continue
                points = _build_price_points(frame)
                result[tk] = points
                price_map[tk] = points
            except Exception as e:
                print(f"  skip {tk}: {e}")
                result[tk] = []

    result["__sizes__"] = _build_size_metrics(price_map)
    result["__meta__"]["size_weight_note"] = "Estimated from constituent market caps in the loaded SET50 universe."

    print(f"[server] Done. {sum(1 for v in result.values() if v)} tickers with data.")
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default logs

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        # ── /api/data?tf=daily ──────────────────────────────
        if path == "/api/data":
            tf = qs.get("tf", ["daily"])[0]
            if tf not in TF:
                tf = "daily"

            key = tf
            with _lock:
                cached = _cache.get(key)
                if cached and (time.time() - cached["ts"]) < CACHE_TTL:
                    data = cached["data"]
                    print(f"[server] Cache hit ({tf})")
                else:
                    try:
                        data = fetch_prices(tf)
                        _cache[key] = {"data": data, "ts": time.time()}
                    except Exception as e:
                        self._error(500, str(e))
                        return

            body = json.dumps({"ok": True, "data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)

        # ── /api/status ────────────────────────────────────
        elif path == "/api/status":
            body = json.dumps({"ok": True, "msg": "SET50 RRG server running"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors()
            self.end_headers()
            self.wfile.write(body)

        # ── / → serve index.html ──────────────────────────
        elif path in ("/", "/index.html"):
            try:
                with open("index.html", "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self._error(404, "index.html not found")
        else:
            self._error(404, "Not found")

    def _error(self, code, msg):
        body = json.dumps({"ok": False, "error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    PORT = 8765
    print("=" * 50)
    print("  SET50 RRG Local Server")
    print(f"  http://localhost:{PORT}")
    print("  กด Ctrl+C เพื่อหยุด")
    print("=" * 50)
    server = HTTPServer(("localhost", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] หยุดแล้ว")
