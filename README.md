# Gold & Oil Geopolitics Radar

A lightweight Python web app that watches trusted headlines plus Forex Factory macro events for `XAUUSD` and `WTI crude`, then scores each catalyst for likely market direction and a short-term reaction area.

## What it does

- Scans trusted publishers only for gold, crude oil, and geopolitics-related headlines.
- Pulls a Forex Factory weekly calendar feed for high-impact macro events.
- Scores each event for likely `gold` and `oil` bias: `up`, `down`, or `mixed`.
- Estimates a reaction range in percent, and in price terms when live quote proxies are available.
- Raises browser-side alerts with sound, speech, and notifications for fresh major headlines.

## Trusted inputs

- Reuters
- CNBC
- Bloomberg
- Associated Press / AP News
- The Wall Street Journal
- Financial Times
- Federal Reserve
- U.S. Energy Information Administration
- Forex Factory

## Run it

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:8000
```

## One-click local launch on Mac

Double-click:

```text
Launch Macro Reaction Radar.command
```

That helper will:

- start the Python backend if it is not already running
- use the local `.venv` Python when available
- open the full browser version at `http://127.0.0.1:8000`
- keep the live feed connected through the local backend automatically
- keep the launcher Terminal window open while the dashboard is running

To stop the background server later, double-click:

```text
Stop Macro Reaction Radar.command
```

You can still open `public/index.html` directly if you want file mode.

## Notes

- The app is dependency-free and uses Python's standard library only.
- Live prices use `GC=F` and `CL=F` quote proxies to anchor target zones for `XAUUSD` and `WTI`.
- The scoring model is heuristic, not a guarantee. Treat it as a fast reaction assistant, not financial advice.
