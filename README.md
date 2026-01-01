# pullback analyzer

a simple tool for visualizing and analyzing price pullbacks on financial assets.

## what it does

- fetches historical data for any ticker (default: gold futures GC=F)
- detects pullbacks using a two-window approach:
  - finds N-day highs
  - tracks lows until confirmed (no new low for N days) or a new high is made
- shows pullbacks visually on an interactive chart
- gives you stats: avg/median/max pullback, duration, recovery time, etc.

## setup

```bash
pip install -r requirements.txt
```

## run

```bash
streamlit run app.py
```

## settings

- **window size:** how many days to use for high/low detection (default 25)
- **minimum pullback %:** ignore anything smaller than this (default 7.5%)
- **ticker:** any valid yahoo finance symbol

## how the detection works

1. find a 25-day high → start tracking
2. if price drops, track the low
3. if a new lower low is made, reset and keep tracking
4. pullback ends when:
   - no new low for 25 days (confirmed bottom), or
   - a new high is made (use the lowest low so far)
5. only keep pullbacks ≥ your threshold

