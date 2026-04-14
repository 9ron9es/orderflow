import requests
import zipfile
import io
import pandas as pd
from pathlib import Path
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.adapters.binance.loaders import BinanceOrderBookDeltaDataLoader
from nautilus_trader.persistence.wranglers import TradeTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider

# ── CONFIG ─────────────────────────────────────────────
SYMBOL = "BTCUSDT"          # change to whatever pair you want
YEAR   = "2024"
MONTH  = "01"               # change month as needed
CATALOG_PATH = "./catalog"  # where data gets saved
# ───────────────────────────────────────────────────────

def download_binance_trades(symbol, year, month):
    """Download aggTrades from Binance public data archive."""
    url = (
        f"https://data.binance.vision/data/futures/um/monthly/aggTrades/"
        f"{symbol}/{symbol}-aggTrades-{year}-{month}.zip"
    )
    print(f"Downloading: {url}")
    r = requests.get(url)
    r.raise_for_status()

    z = zipfile.ZipFile(io.BytesIO(r.content))
    csv_name = z.namelist()[0]
    df = pd.read_csv(z.open(csv_name), header=None, names=[
        "agg_id", "price", "qty", "first_trade_id", "last_trade_id",
        "ts", "is_buyer_maker", "best_price_match"
    ])
    print(f"Loaded {len(df):,} ticks")
    return df

def save_to_catalog(df):
    catalog = ParquetDataCatalog(CATALOG_PATH)
    instrument = TestInstrumentProvider.btcusdt_binance_perpetual()

    # convert side
    df["side"] = df["is_buyer_maker"].map({True: "SELL", False: "BUY"})
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)

    wrangler = TradeTickDataWrangler(instrument=instrument)
    ticks = wrangler.process(df.rename(columns={"price": "price", "qty": "quantity", "ts": "timestamp"}))

    catalog.write_data(ticks)
    print(f"Saved {len(ticks):,} ticks to {CATALOG_PATH}")

if __name__ == "__main__":
    Path(CATALOG_PATH).mkdir(exist_ok=True)
    df = download_binance_trades(SYMBOL, YEAR, MONTH)
    save_to_catalog(df)