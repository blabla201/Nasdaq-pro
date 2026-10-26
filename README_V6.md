# Nasdaq Radar v6.0 Smart Trader — Paper prototype

Run: `pip install -r requirements.txt` then `streamlit run app.py`.

New: risk-budgeted paper-trade plans, paper trade entry, stop-first simulated exits, and CSV export. No broker credentials, no real orders, no push notifications.

Important: The 1m and 5m feeds are Yahoo Finance research data, may be delayed or incomplete. Paper exits update only when the app scans and are NOT reliable execution/backtesting results. Session-state paper positions disappear when the Streamlit session resets. There is no historical backtest, reliable multi-bar fill simulation, commissions, market hours guard, broker connectivity, or live alert service yet. The 1m/5m/10m experimental scanner is retained from v5.4 and is not certified for live trading. Signals are not buy recommendations.

Risk limits: max 2% modeled equity risk per position, 20% capital allocation, conservative spread/slippage estimates, 2-minute signal freshness gate. These controls cannot prevent loss beyond the modelled risk.
