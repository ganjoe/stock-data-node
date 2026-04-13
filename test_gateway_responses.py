"""
test_gateway_responses.py
Erweiterte Diagnostik: Testet mehrere Strategien pro Ticker wenn
"No security definition" (Error 200) auftritt:
  1. SMART/USD (Standard)
  2. NASDAQ/USD (explizit)
  3. NYSE/USD (explizit)
  4. reqMatchingSymbols Auto-Discovery

Ziel: Herausfinden welche Kombination für jeden Ticker funktioniert,
      um den Downloader und ticker_map.json zu verbessern.

Aufruf:
    cd /home/daniel/stock-data-node
    .venv/bin/python test_gateway_responses.py
"""
from __future__ import annotations

import asyncio
import time
import sys
import random
from dataclasses import dataclass, field

sys.path.insert(0, "src")
from ib_insync import IB, Contract

# ─── Config ──────────────────────────────────────────────────────────────────

GATEWAY_HOST = "172.18.0.2"
GATEWAY_PORT = 4002
CONNECT_TIMEOUT = 20

TEST_TICKERS = [
    "SNOW", "PLTR", "CRM", "NOW", "DDOG",
    "MDB", "CFLT", "INFA", "ESTC",
    "MSFT", "AMZN", "GOOGL",
]

BAR_SIZE     = "1 day"
DURATION     = "1 Y"
WHAT_TO_SHOW = "TRADES"
USE_RTH      = True

PER_REQUEST_TIMEOUT = 60.0
MAX_CONCURRENT      = 5
PACING_DELAY        = 0.1

# Fallback exchanges tried in order after SMART fails
FALLBACK_EXCHANGES = ["NASDAQ", "NYSE", "ARCA", "BATS"]

# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class AttemptResult:
    strategy: str           # z.B. "SMART/USD", "NASDAQ/USD", "auto-discovery"
    success: bool
    bars: int = 0
    elapsed_s: float = 0.0
    error_code: int | None = None
    error_msg: str | None = None
    resolved_exchange: str | None = None
    resolved_currency: str | None = None
    conid: int | None = None
    raw_errors: list[tuple[int, str]] = field(default_factory=list)

@dataclass
class TickerReport:
    ticker: str
    attempts: list[AttemptResult] = field(default_factory=list)

    @property
    def best(self) -> AttemptResult | None:
        ok = [a for a in self.attempts if a.success]
        return ok[0] if ok else None

    @property
    def succeeded(self) -> bool:
        return self.best is not None

# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _request_bars(
    ib: IB,
    contract: Contract,
    semaphore: asyncio.Semaphore,
    pacing_lock: asyncio.Lock,
    last_t: list[float],
    strategy_label: str,
) -> AttemptResult:
    """Qualifies contract, then fetches historical bars. Returns AttemptResult."""
    captured: list[tuple[int, str]] = []

    def on_error(req_id: int, code: int, msg: str, contract_: object) -> None:
        captured.append((code, msg))

    ib.errorEvent += on_error

    async with semaphore:
        # Pacing
        async with pacing_lock:
            wait = PACING_DELAY - (time.monotonic() - last_t[0])
            if wait > 0:
                await asyncio.sleep(wait)
            last_t[0] = time.monotonic()

        t0 = time.monotonic()
        try:
            # Qualify
            await ib.qualifyContractsAsync(contract)
            if not contract.conId:
                ib.errorEvent -= on_error
                return AttemptResult(
                    strategy=strategy_label, success=False,
                    elapsed_s=time.monotonic() - t0,
                    error_msg="qualify → no conId",
                    raw_errors=captured,
                )

            # Fetch bars
            bars = await asyncio.wait_for(
                ib.reqHistoricalDataAsync(
                    contract=contract,
                    endDateTime="",
                    durationStr=DURATION,
                    barSizeSetting=BAR_SIZE,
                    whatToShow=WHAT_TO_SHOW,
                    useRTH=USE_RTH,
                    formatDate=1,
                ),
                timeout=PER_REQUEST_TIMEOUT,
            )
            elapsed = time.monotonic() - t0
            ib.errorEvent -= on_error

            fatal = [e for e in captured if e[0] in (162, 200, 321, 10090)]
            if fatal:
                code, msg = fatal[0]
                return AttemptResult(
                    strategy=strategy_label, success=False,
                    elapsed_s=elapsed, error_code=code, error_msg=msg,
                    conid=contract.conId, raw_errors=captured,
                )

            return AttemptResult(
                strategy=strategy_label, success=True,
                bars=len(bars), elapsed_s=elapsed,
                resolved_exchange=contract.primaryExchange or contract.exchange,
                resolved_currency=contract.currency,
                conid=contract.conId,
                raw_errors=captured,
            )

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            ib.errorEvent -= on_error
            return AttemptResult(
                strategy=strategy_label, success=False,
                elapsed_s=elapsed,
                error_msg=f"TIMEOUT after {elapsed:.1f}s",
                raw_errors=captured,
            )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            ib.errorEvent -= on_error
            return AttemptResult(
                strategy=strategy_label, success=False,
                elapsed_s=elapsed, error_msg=str(exc),
                raw_errors=captured,
            )


async def _auto_discover(
    ib: IB,
    ticker: str,
    semaphore: asyncio.Semaphore,
    pacing_lock: asyncio.Lock,
    last_t: list[float],
) -> AttemptResult:
    """
    Uses reqMatchingSymbols to find the best STK contract,
    then fetches historical bars with it.
    """
    try:
        descriptions = await ib.reqMatchingSymbolsAsync(ticker)
    except Exception as exc:
        return AttemptResult(strategy="auto-discovery", success=False, error_msg=str(exc))

    stk = [d for d in descriptions if d.contract.secType == "STK"]
    if not stk:
        return AttemptResult(strategy="auto-discovery", success=False,
                             error_msg="reqMatchingSymbols: no STK results")

    # Priority: USD first, then primary exchange order
    PRIO_EXCHANGE = ["NASDAQ", "NYSE", "ARCA", "SMART"]
    PRIO_CURRENCY = ["USD", "EUR"]

    def score(d) -> tuple[int, int]:
        c = d.contract
        cx = c.currency
        ex = c.primaryExchange or c.exchange
        ci = PRIO_CURRENCY.index(cx) if cx in PRIO_CURRENCY else 99
        ei = PRIO_EXCHANGE.index(ex) if ex in PRIO_EXCHANGE else 99
        return (ci, ei)

    stk.sort(key=score)
    best_desc = stk[0]
    c = best_desc.contract
    contract = Contract(
        symbol=c.symbol, secType="STK",
        exchange=c.primaryExchange or c.exchange,
        currency=c.currency,
    )
    label = f"auto-discovery → {contract.exchange}/{contract.currency}"
    return await _request_bars(ib, contract, semaphore, pacing_lock, last_t, label)


async def test_ticker(
    ib: IB,
    ticker: str,
    semaphore: asyncio.Semaphore,
    pacing_lock: asyncio.Lock,
    last_t: list[float],
) -> TickerReport:
    report = TickerReport(ticker=ticker)

    # Strategy 1: SMART/USD (standard)
    c1 = Contract(symbol=ticker, secType="STK", exchange="SMART", currency="USD")
    r1 = await _request_bars(ib, c1, semaphore, pacing_lock, last_t, "SMART/USD")
    report.attempts.append(r1)
    if r1.success:
        return report

    # Strategy 2–N: explicit exchanges
    for exch in FALLBACK_EXCHANGES:
        c = Contract(symbol=ticker, secType="STK", exchange=exch, currency="USD")
        r = await _request_bars(ib, c, semaphore, pacing_lock, last_t, f"{exch}/USD")
        report.attempts.append(r)
        if r.success:
            return report

    # Strategy last: reqMatchingSymbols auto-discovery
    r_auto = await _auto_discover(ib, ticker, semaphore, pacing_lock, last_t)
    report.attempts.append(r_auto)

    return report


# ─── Report ──────────────────────────────────────────────────────────────────

def print_report(reports: list[TickerReport]) -> None:
    SEP = "─" * 80
    print()
    print("═" * 80)
    print("  IB Gateway Diagnostics — Erweiterter Strategie-Test")
    print("═" * 80)

    ok_reports  = [r for r in reports if r.succeeded]
    fail_reports = [r for r in reports if not r.succeeded]

    # ── Summary table ──
    print(f"\n  {'Ticker':<8}  {'Ergebnis':<10}  {'Strategie':<30}  {'Bars':>5}  {'Zeit':>7}  {'Exchange/Currency'}")
    print(f"  {SEP}")
    for rep in reports:
        b = rep.best
        if b:
            exch_cur = f"{b.resolved_exchange}/{b.resolved_currency}" if b.resolved_exchange else "—"
            print(f"  {rep.ticker:<8}  {'✅ OK':<10}  {b.strategy:<30}  {b.bars:>5}  {b.elapsed_s:>6.2f}s  {exch_cur}")
        else:
            last = rep.attempts[-1]
            err = (last.error_msg or "")[:35]
            print(f"  {rep.ticker:<8}  {'❌ FAIL':<10}  {'alle Strategien fehlgeschlagen':<30}  {'—':>5}  {'—':>7}  {err}")

    print()

    # ── Timing stats ──
    if ok_reports:
        times = [rep.best.elapsed_s for rep in ok_reports]
        print("  Antwortzeiten (erfolgreiche Requests):")
        print(f"    min : {min(times):.2f}s")
        print(f"    max : {max(times):.2f}s")
        print(f"    avg : {sum(times)/len(times):.2f}s")
        if len(times) > 1:
            p95_idx = max(0, int(len(times) * 0.95) - 1)
            print(f"    p95 : {sorted(times)[p95_idx]:.2f}s")
        recommended = max(times) * 3
        recommended = max(recommended, 5.0)
        print(f"\n  → Empfohlener historical_data_timeout: {recommended:.0f}s  (3× max beobachtet, min 5s)")

    print()

    # ── ticker_map.json Empfehlung ──
    print("  Empfohlene ticker_map.json Einträge:")
    print(f"  {SEP}")
    for rep in ok_reports:
        b = rep.best
        exch = b.resolved_exchange or "SMART"
        cur  = b.resolved_currency or "USD"
        print(f'  "{rep.ticker}": {{"symbol": "{rep.ticker}", "exchange": "{exch}", "currency": "{cur}", "sec_type": "STK"}},')
    for rep in fail_reports:
        print(f'  "{rep.ticker}": null,  // alle Strategien fehlgeschlagen')

    print()

    # ── Detail pro Fehlschlag ──
    if fail_reports:
        print("  Fehlgeschlagene Ticker — alle versuchten Strategien:")
        print(f"  {SEP}")
        for rep in fail_reports:
            print(f"  {rep.ticker}:")
            for att in rep.attempts:
                status = "✅" if att.success else "❌"
                err = att.error_msg or ""
                print(f"    {status} [{att.strategy}] {err[:65]}")
        print()

    # ── Alle gesehenen IB-Fehlercodes (über alle Ticker/Strategien hinweg) ──
    all_codes: dict[int, list[str]] = {}
    for rep in reports:
        for att in rep.attempts:
            for code, msg in att.raw_errors:
                all_codes.setdefault(code, []).append(f"{rep.ticker}/{att.strategy}: {msg[:55]}")

    if all_codes:
        print("  Alle beobachteten IB-Fehlercodes:")
        for code in sorted(all_codes):
            label = "INFO" if code < 2000 else "WARN" if code < 10000 else "ERR"
            examples = all_codes[code][:2]
            print(f"    [{label:4}] {code}: {examples[0]}")
            for ex in examples[1:]:
                print(f"               {ex}")
        print()

    print(f"  Ergebnis: {len(ok_reports)}/{len(reports)} Ticker erfolgreich geladen")
    print("═" * 80)


# ─── Entrypoint ──────────────────────────────────────────────────────────────

async def main() -> None:
    ib = IB()
    client_id = random.randint(100, 999)

    print(f"Verbinde mit {GATEWAY_HOST}:{GATEWAY_PORT} (clientId={client_id})...")
    try:
        await ib.connectAsync(
            host=GATEWAY_HOST, port=GATEWAY_PORT,
            clientId=client_id, timeout=CONNECT_TIMEOUT,
        )
    except Exception as exc:
        print(f"❌ Verbindung fehlgeschlagen: {exc}")
        sys.exit(1)
    print("✅ Verbunden.\n")

    semaphore  = asyncio.Semaphore(MAX_CONCURRENT)
    pacing_lock = asyncio.Lock()
    last_t: list[float] = [0.0]

    tickers = list(dict.fromkeys(TEST_TICKERS))  # deduplicate (NOW kommt 2x vor)
    print(f"Teste {len(tickers)} Ticker mit bis zu {MAX_CONCURRENT} parallelen Requests...\n")
    print("Strategien pro Ticker: SMART → NASDAQ → NYSE → ARCA → BATS → auto-discovery\n")

    tasks = [test_ticker(ib, t, semaphore, pacing_lock, last_t) for t in tickers]
    reports = await asyncio.gather(*tasks)

    print_report(list(reports))
    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
