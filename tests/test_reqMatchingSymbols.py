import asyncio
from ib_insync import IB, Contract

async def main():
    ib = IB()
    try:
        await ib.connectAsync('127.0.0.1', 4002, clientId=9999)
        test_symbols = ["XDEF", "AAPL", "RYA"] # XDEF is EU, AAPL is US, RYA is EU (Ryanair or Royal Yacht)
        
        for sym in test_symbols:
            print(f"\n--- Searching for {sym} ---")
            contracts = await ib.reqMatchingSymbolsAsync(sym)
            if not contracts:
                print("No matches found.")
            else:
                for cntd in contracts:
                    c = cntd.contract
                    devs = cntd.derivativeSecTypes
                    print(f"Match: {c.symbol} | PriExch: {c.primaryExchange} | Curr: {c.currency} | Type: {c.secType} | Desc: {c.description} | Deriv: {devs}")
                    
    except Exception as e:
        print(f"Error: {e}")
    finally:
        ib.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
