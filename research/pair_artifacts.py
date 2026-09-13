"""Validated research-only price snapshots for identical-input model comparisons."""

import hashlib
import json
import re

import numpy as np
import pandas as pd


def save_history(history, directory, start, end):
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for symbol, bars in history.items():
        if not re.fullmatch(r"[A-Z]{1,6}", symbol):
            raise ValueError("Invalid price snapshot symbol")
        csv = bars.loc[:, ["Open", "Close"]].to_csv(index_label="Date", date_format="%Y-%m-%d", float_format="%.17g")
        content = csv.encode("utf-8")
        (directory / f"{symbol}.csv").write_bytes(content)
        hashes[symbol] = hashlib.sha256(content).hexdigest()
    manifest = {"start": start, "endExclusive": end, "dataSha256": hashes}
    (directory / "data-manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")


def load_history(directory, symbols, start, end):
    manifest = json.loads((directory / "data-manifest.json").read_text(encoding="utf-8"))
    if manifest["start"] != start or manifest["endExclusive"] != end or set(manifest["dataSha256"]) != set(symbols):
        raise ValueError("Saved prices do not match the requested dates and universe; download a fresh snapshot")
    history = {}
    for symbol in symbols:
        if not re.fullmatch(r"[A-Z]{1,6}", symbol):
            raise ValueError("Invalid price snapshot symbol")
        path = directory / f"{symbol}.csv"
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["dataSha256"][symbol]:
            raise ValueError(f"Saved price hash mismatch: {symbol}")
        bars = pd.read_csv(path, index_col="Date", parse_dates=["Date"], float_precision="round_trip")
        index = bars.index
        if (list(bars.columns) != ["Open", "Close"] or not isinstance(index, pd.DatetimeIndex)
                or index.empty or index.hasnans or index.has_duplicates or index.tz is not None
                or not index.is_monotonic_increasing or not index.equals(index.normalize())
                or index.min() < pd.Timestamp(start) or index.max() >= pd.Timestamp(end)
                or not np.isfinite(bars.to_numpy(dtype=float)).all() or (bars <= 0).any().any()):
            raise ValueError(f"Invalid saved price history: {symbol}")
        history[symbol] = bars
    return history, manifest["dataSha256"]


def load_available_history(directory, symbols):
    """Load requested local CSVs and constrain them to their shared date range.

    Unlike ``load_history``, this path deliberately does not require a matching
    manifest.  It is for offline research recovery when the local bars remain
    valid but the requested CLI dates or universe changed.  The CSV contract and
    file hashes are still validated and returned for the result audit.
    """
    if not symbols:
        raise ValueError("At least one local price symbol is required")
    history, hashes = {}, {}
    for symbol in symbols:
        if not re.fullmatch(r"[A-Z]{1,6}", symbol):
            raise ValueError("Invalid price snapshot symbol")
        path = directory / f"{symbol}.csv"
        if not path.exists():
            raise ValueError(f"No local price history found for {symbol}")
        content = path.read_bytes()
        hashes[symbol] = hashlib.sha256(content).hexdigest()
        bars = pd.read_csv(path, index_col="Date", parse_dates=["Date"], float_precision="round_trip")
        index = bars.index
        if (list(bars.columns) != ["Open", "Close"] or not isinstance(index, pd.DatetimeIndex)
                or index.empty or index.hasnans or index.has_duplicates or index.tz is not None
                or not index.is_monotonic_increasing or not index.equals(index.normalize())
                or not np.isfinite(bars.to_numpy(dtype=float)).all() or (bars <= 0).any().any()):
            raise ValueError(f"Invalid local price history: {symbol}")
        history[symbol] = bars

    first_session = max(bars.index.min() for bars in history.values())
    last_session = min(bars.index.max() for bars in history.values())
    if first_session > last_session:
        raise ValueError("Local price histories have no overlapping sessions")
    history = {
        symbol: bars.loc[(bars.index >= first_session) & (bars.index <= last_session)]
        for symbol, bars in history.items()
    }
    return history, hashes, first_session, last_session + pd.Timedelta(days=1)
