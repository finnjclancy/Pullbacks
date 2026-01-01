import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import requests
from io import StringIO
import json

st.set_page_config(
    page_title="Pullback Analyzer",
    page_icon="📉",
    layout="wide"
)

# tradingview-inspired dark theme
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');
    
    .stApp {
        background: #131722;
    }
    
    h1, h2, h3 {
        font-family: 'JetBrains Mono', monospace !important;
        color: #d1d4dc !important;
    }
    
    .metric-card {
        background: #1e222d;
        border: 1px solid #2a2e39;
        border-radius: 8px;
        padding: 20px;
        text-align: center;
    }
    
    .metric-value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 2.2rem;
        font-weight: 700;
        color: #f0c040;
        margin: 0;
    }
    
    .metric-label {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.85rem;
        color: #787b86;
        margin-top: 8px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    
    .stDataFrame {
        font-family: 'JetBrains Mono', monospace !important;
    }
    
    section[data-testid="stSidebar"] {
        background: #1e222d;
    }
    
    section[data-testid="stSidebar"] .stTextInput label,
    section[data-testid="stSidebar"] .stSlider label,
    section[data-testid="stSidebar"] .stNumberInput label {
        color: #d1d4dc !important;
    }
</style>
""", unsafe_allow_html=True)


def detect_pullbacks(df: pd.DataFrame, window: int, min_pullback_pct: float) -> list:
    """
    Detect pullbacks by finding swing highs and their subsequent lows.
    - A swing high is confirmed when price fails to make a new high for N days
    - The low is the lowest point before the next swing high
    """
    prices = df['Close'].values
    dates = df.index.tolist()
    n = len(prices)
    
    pullbacks = []
    
    # Track the running high and when it was set
    running_high = prices[0]
    running_high_idx = 0
    running_low = prices[0]
    running_low_idx = 0
    days_since_high = 0
    
    # State: are we looking for a low after a confirmed high?
    high_confirmed = False
    confirmed_high = None
    confirmed_high_idx = None
    
    for i in range(1, n):
        price = prices[i]
        
        if not high_confirmed:
            # Looking to confirm a high
            if price > running_high:
                # New high - reset counter
                running_high = price
                running_high_idx = i
                days_since_high = 0
            else:
                days_since_high += 1
                
                # High confirmed after N days without new high
                if days_since_high >= window:
                    high_confirmed = True
                    confirmed_high = running_high
                    confirmed_high_idx = running_high_idx
                    running_low = price
                    running_low_idx = i
        else:
            # High is confirmed, now tracking the low
            if price < running_low:
                # New low
                running_low = price
                running_low_idx = i
            
            # Check if price made a new high (recovery)
            if price > confirmed_high:
                # Price exceeded the confirmed high - record pullback and reset
                pullback_pct = ((confirmed_high - running_low) / confirmed_high) * 100
                
                if pullback_pct >= min_pullback_pct:
                    pullbacks.append({
                        'high_date': dates[confirmed_high_idx],
                        'high_price': confirmed_high,
                        'low_date': dates[running_low_idx],
                        'low_price': running_low,
                        'pullback_pct': pullback_pct,
                        'duration_days': (dates[running_low_idx] - dates[confirmed_high_idx]).days,
                        'recovery_days': (dates[i] - dates[running_low_idx]).days
                    })
                
                # Reset - this new high becomes the running high
                high_confirmed = False
                running_high = price
                running_high_idx = i
                days_since_high = 0
            
            # Also check for a lower high that gets confirmed
            elif price > running_low:
                # Price bounced but didn't exceed confirmed high
                # Check if we should start tracking a new potential high
                window_start = max(0, i - window)
                local_max = max(prices[window_start:i])
                
                if price >= local_max:
                    # This could be a new swing high forming
                    # Record the current pullback
                    pullback_pct = ((confirmed_high - running_low) / confirmed_high) * 100
                    
                    if pullback_pct >= min_pullback_pct:
                        pullbacks.append({
                            'high_date': dates[confirmed_high_idx],
                            'high_price': confirmed_high,
                            'low_date': dates[running_low_idx],
                            'low_price': running_low,
                            'pullback_pct': pullback_pct,
                            'duration_days': (dates[running_low_idx] - dates[confirmed_high_idx]).days,
                            'recovery_days': (dates[i] - dates[running_low_idx]).days
                        })
                    
                    # Start tracking new high
                    high_confirmed = False
                    running_high = price
                    running_high_idx = i
                    days_since_high = 0
    
    # Handle any remaining unconfirmed pullback at the end
    if high_confirmed and running_low < confirmed_high:
        pullback_pct = ((confirmed_high - running_low) / confirmed_high) * 100
        if pullback_pct >= min_pullback_pct:
            pullbacks.append({
                'high_date': dates[confirmed_high_idx],
                'high_price': confirmed_high,
                'low_date': dates[running_low_idx],
                'low_price': running_low,
                'pullback_pct': pullback_pct,
                'duration_days': (dates[running_low_idx] - dates[confirmed_high_idx]).days,
                'recovery_days': 0  # Not recovered yet
            })
    
    return pullbacks


def resample_ohlc(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample daily OHLC data to weekly or monthly"""
    if timeframe == "Daily":
        return df
    
    rule = 'W' if timeframe == "Weekly" else 'ME'
    
    resampled = df.resample(rule).agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last'
    }).dropna()
    
    return resampled


def create_tv_chart(df: pd.DataFrame, pullbacks: list, ticker: str, timeframe: str = "Daily") -> str:
    """create tradingview lightweight-charts html component"""
    
    # Resample data for chart display
    chart_df = resample_ohlc(df, timeframe)
    
    # prepare candlestick data as JSON
    candle_data = []
    for idx, row in chart_df.iterrows():
        candle_data.append({
            'time': idx.strftime('%Y-%m-%d'),
            'open': float(row['Open']),
            'high': float(row['High']),
            'low': float(row['Low']),
            'close': float(row['Close'])
        })
    
    # Get the dates from resampled chart data for marker alignment
    chart_dates = set(chart_df.index.strftime('%Y-%m-%d'))
    
    # prepare markers for pullbacks
    markers = []
    for pb in pullbacks:
        high_date = pb['high_date'].strftime('%Y-%m-%d')
        low_date = pb['low_date'].strftime('%Y-%m-%d')
        
        # Find nearest date in chart data for markers
        # For weekly/monthly, find the period that contains this date
        if timeframe != "Daily":
            # Find the candle that contains this date
            high_idx = chart_df.index.get_indexer([pb['high_date']], method='ffill')[0]
            low_idx = chart_df.index.get_indexer([pb['low_date']], method='ffill')[0]
            
            if high_idx >= 0 and high_idx < len(chart_df):
                high_date = chart_df.index[high_idx].strftime('%Y-%m-%d')
            if low_idx >= 0 and low_idx < len(chart_df):
                low_date = chart_df.index[low_idx].strftime('%Y-%m-%d')
        
        # high marker
        markers.append({
            'time': high_date,
            'position': 'aboveBar',
            'color': '#26a69a',
            'shape': 'arrowDown',
            'text': 'H'
        })
        # low marker
        markers.append({
            'time': low_date,
            'position': 'belowBar',
            'color': '#ef5350',
            'shape': 'arrowUp',
            'text': f'-{pb["pullback_pct"]:.0f}%'
        })
    
    candle_json = json.dumps(candle_data)
    markers_json = json.dumps(markers)
    
    html = f"""
    <div id="tv-chart" style="width: 100%; height: 600px;"></div>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
    <script>
        const container = document.getElementById('tv-chart');
        const chart = LightweightCharts.createChart(container, {{
            layout: {{
                background: {{ type: 'solid', color: '#131722' }},
                textColor: '#d1d4dc',
                fontFamily: 'JetBrains Mono, monospace'
            }},
            grid: {{
                vertLines: {{ color: '#1e222d' }},
                horzLines: {{ color: '#1e222d' }}
            }},
            rightPriceScale: {{
                borderColor: '#2a2e39',
                scaleMargins: {{ top: 0.1, bottom: 0.1 }}
            }},
            timeScale: {{
                borderColor: '#2a2e39',
                timeVisible: true
            }},
            crosshair: {{
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: {{ color: '#787b86', width: 1, style: 2 }},
                horzLine: {{ color: '#787b86', width: 1, style: 2 }}
            }}
        }});
        
        // logarithmic scale
        chart.priceScale('right').applyOptions({{
            mode: LightweightCharts.PriceScaleMode.Logarithmic
        }});
        
        const candleSeries = chart.addCandlestickSeries({{
            upColor: '#26a69a',
            downColor: '#ef5350',
            borderUpColor: '#26a69a',
            borderDownColor: '#ef5350',
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350'
        }});
        
        const data = {candle_json};
        candleSeries.setData(data);
        
        // add markers
        const markers = {markers_json};
        candleSeries.setMarkers(markers);
        
        // Show most recent data by default, user can scroll left to see history
        chart.timeScale().scrollToRealTime();
        
        // handle resize
        new ResizeObserver(entries => {{
            if (entries.length === 0 || entries[0].target !== container) return;
            const {{ width, height }} = entries[0].contentRect;
            chart.applyOptions({{ width, height }});
        }}).observe(container);
    </script>
    """
    
    return html


def create_histogram(pullbacks: list) -> go.Figure:
    """create histogram of pullback depths"""
    
    if not pullbacks:
        return go.Figure()
    
    pcts = [pb['pullback_pct'] for pb in pullbacks]
    
    fig = go.Figure()
    
    fig.add_trace(go.Histogram(
        x=pcts,
        nbinsx=15,
        marker=dict(
            color='#f0c040',
            line=dict(color='#131722', width=1)
        ),
        hovertemplate='%{x:.1f}%<br>count: %{y}<extra></extra>'
    ))
    
    fig.update_layout(
        title=dict(
            text='Pullback Distribution',
            font=dict(family='JetBrains Mono', size=16, color='#d1d4dc')
        ),
        xaxis=dict(
            title=dict(text='Pullback %', font=dict(family='JetBrains Mono', color='#787b86')),
            gridcolor='#1e222d',
            tickfont=dict(family='JetBrains Mono', color='#787b86')
        ),
        yaxis=dict(
            title=dict(text='Count', font=dict(family='JetBrains Mono', color='#787b86')),
            gridcolor='#1e222d',
            tickfont=dict(family='JetBrains Mono', color='#787b86')
        ),
        plot_bgcolor='#131722',
        paper_bgcolor='#131722',
        bargap=0.1,
        margin=dict(l=60, r=40, t=60, b=60)
    )
    
    return fig


def main():
    st.title("📉 Pullback Analyzer")
    
    with st.sidebar:
        st.header("Settings")
        
        ticker = st.text_input(
            "Ticker Symbol",
            value="XAUUSD",
            help="Stooq tickers: XAUUSD (Gold), AAPL.US (Apple), SPY.US (S&P 500)"
        ).upper()
        
        start_year = st.number_input(
            "Start Year",
            min_value=1900,
            max_value=2025,
            value=1965,
            help="Filter data to start from this year"
        )
        
        window = st.slider(
            "Window Size (Days)",
            min_value=10,
            max_value=100,
            value=25,
            step=5,
            help="Number of days for high/low detection window"
        )
        
        min_pullback = st.slider(
            "Minimum Pullback %",
            min_value=1.0,
            max_value=25.0,
            value=7.5,
            step=0.5,
            help="Ignore pullbacks smaller than this"
        )
        
        chart_timeframe = st.selectbox(
            "Chart Timeframe",
            options=["Daily", "Weekly", "Monthly"],
            index=1,  # Default to Weekly for long time series
            help="Resample data for chart display (analysis uses daily data)"
        )
        
        analyze_btn = st.button("Analyze", type="primary", use_container_width=True)
    
    if analyze_btn or 'data' in st.session_state:
        with st.spinner(f"Fetching {ticker} data..."):
            try:
                url = f"https://stooq.com/q/d/l/?s={ticker.lower()}&i=d"
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
                response = requests.get(url, headers=headers, timeout=30)
                
                if response.status_code != 200:
                    st.error(f"Failed to fetch data (status {response.status_code})")
                    return
                
                content = response.text.strip()
                if not content or 'Date' not in content:
                    st.error(f"No data found for {ticker}. Try: XAUUSD (Gold), AAPL.US (Apple), SPY.US (S&P)")
                    return
                
                data = pd.read_csv(StringIO(content))
                
                if data.empty:
                    st.error(f"No data found for {ticker}")
                    return
                
                data['Date'] = pd.to_datetime(data['Date'])
                data = data.set_index('Date')
                data = data.sort_index()
                
                # filter by start year
                data = data[data.index.year >= start_year]
                
                if data.empty:
                    st.error(f"No data found for {ticker} after {start_year}")
                    return
                
                st.session_state['data'] = data
                st.session_state['ticker'] = ticker
                
            except Exception as e:
                st.error(f"Error fetching data: {e}")
                return
        
        data = st.session_state.get('data')
        ticker = st.session_state.get('ticker', ticker)
        
        if data is not None and not data.empty:
            # refilter in case start_year changed
            data = data[data.index.year >= start_year]
            
            pullbacks = detect_pullbacks(data, window, min_pullback)
            
            # info bar
            col1, col2, col3 = st.columns(3)
            with col1:
                st.info(f"📊 **{len(data):,}** Days of Data")
            with col2:
                st.info(f"📅 {data.index[0].strftime('%Y-%m-%d')} to {data.index[-1].strftime('%Y-%m-%d')}")
            with col3:
                st.info(f"📉 **{len(pullbacks)}** Pullbacks Detected (≥{min_pullback}%)")
            
            # main chart - tradingview style
            chart_html = create_tv_chart(data, pullbacks, ticker, chart_timeframe)
            components.html(chart_html, height=620)
            
            if pullbacks:
                st.header("📊 Pullback Statistics")
                
                pcts = [pb['pullback_pct'] for pb in pullbacks]
                durations = [pb['duration_days'] for pb in pullbacks]
                recoveries = [pb['recovery_days'] for pb in pullbacks]
                
                col1, col2, col3, col4, col5 = st.columns(5)
                
                with col1:
                    st.markdown(f"""
                    <div class="metric-card">
                        <p class="metric-value">{np.mean(pcts):.1f}%</p>
                        <p class="metric-label">avg pullback</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.markdown(f"""
                    <div class="metric-card">
                        <p class="metric-value">{np.median(pcts):.1f}%</p>
                        <p class="metric-label">median pullback</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"""
                    <div class="metric-card">
                        <p class="metric-value">{max(pcts):.1f}%</p>
                        <p class="metric-label">max pullback</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col4:
                    st.markdown(f"""
                    <div class="metric-card">
                        <p class="metric-value">{int(np.mean(durations))}</p>
                        <p class="metric-label">avg days to low</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col5:
                    st.markdown(f"""
                    <div class="metric-card">
                        <p class="metric-value">{int(np.mean(recoveries))}</p>
                        <p class="metric-label">avg recovery days</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                st.subheader("Severity Breakdown")
                
                small = len([p for p in pcts if p < 10])
                medium = len([p for p in pcts if 10 <= p < 20])
                large = len([p for p in pcts if p >= 20])
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.markdown(f"""
                    <div class="metric-card">
                        <p class="metric-value" style="color: #26a69a;">{small}</p>
                        <p class="metric-label">small (7.5-10%)</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.markdown(f"""
                    <div class="metric-card">
                        <p class="metric-value" style="color: #f0c040;">{medium}</p>
                        <p class="metric-label">medium (10-20%)</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                with col3:
                    st.markdown(f"""
                    <div class="metric-card">
                        <p class="metric-value" style="color: #ef5350;">{large}</p>
                        <p class="metric-label">large (20%+)</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.plotly_chart(create_histogram(pullbacks), use_container_width=True)
                
                with col2:
                    st.subheader("Quick Stats")
                    st.markdown(f"""
                    - **Std Deviation:** {np.std(pcts):.1f}%
                    - **Min Pullback:** {min(pcts):.1f}%
                    - **Longest Decline:** {max(durations)} days
                    - **Shortest Decline:** {min(durations)} days
                    - **Longest Recovery:** {max(recoveries)} days
                    - **Shortest Recovery:** {min(recoveries)} days
                    """)
                
                st.header("📋 Pullback Details")
                
                pb_df = pd.DataFrame(pullbacks)
                # sort by high_date descending (newest first)
                pb_df = pb_df.sort_values('high_date', ascending=False).reset_index(drop=True)
                pb_df['high_date'] = pd.to_datetime(pb_df['high_date']).dt.strftime('%Y-%m-%d')
                pb_df['low_date'] = pd.to_datetime(pb_df['low_date']).dt.strftime('%Y-%m-%d')
                pb_df['high_price'] = pb_df['high_price'].apply(lambda x: f"${x:,.2f}")
                pb_df['low_price'] = pb_df['low_price'].apply(lambda x: f"${x:,.2f}")
                pb_df['pullback_pct'] = pb_df['pullback_pct'].apply(lambda x: f"{x:.1f}%")
                
                pb_df.columns = ['High Date', 'High Price', 'Low Date', 'Low Price', 'Pullback %', 'Days To Low', 'Recovery Days']
                
                st.dataframe(
                    pb_df,
                    use_container_width=True,
                    hide_index=True
                )
                
                # How pullbacks are calculated section
                with st.expander("📖 How Pullbacks Are Calculated"):
                    st.markdown("""
                    #### Step 1: Find a High
                    - Looks for an **N-day high** (price higher than previous N days)
                    - This marks a potential pullback starting point
                    
                    #### Step 2: Track the Low
                    - Once a high is found, tracking begins
                    - If price makes a **new lower low** → reset counter, keep tracking
                    - This captures the *full* extent of the pullback, not just the first dip
                    
                    #### Step 3: Confirm the Pullback
                    A pullback is confirmed when **either**:
                    - **No new low for N days** → decline is over, bottom confirmed
                    - **A new N-day high is made** → price recovered, use lowest point tracked
                    
                    #### Step 4: Filter
                    - Calculate: `pullback % = (high - low) / high × 100`
                    - Only keep pullbacks ≥ your threshold
                    
                    ---
                    
                    #### Visual Example
                    
                    ```
                          HIGH (N-day high found)
                            ↓
                            *
                           / \\
                          /   \\
                         /     \\   ← tracking low...
                        /       \\
                       /         *  ← new low, reset counter
                                  \\
                                   *  ← another new low, reset
                                  /
                                 /   ← N days pass, no new low
                                ↓
                            LOW CONFIRMED
                    ```
                    
                    ---
                    
                    #### Settings Explained
                    
                    | Setting | What It Does |
                    |---------|--------------|
                    | **Window Size** | Days to look back for highs/lows (larger = fewer, more significant pullbacks) |
                    | **Minimum Pullback %** | Ignore anything smaller than this |
                    | **Start Year** | Filter data to begin from this year |
                    """)
            else:
                st.warning(f"No pullbacks ≥{min_pullback}% found with a {window}-day window")
    
    else:
        st.markdown("""
        ### How To Use
        
        1. Enter a ticker symbol in the sidebar
        2. Set the start year (default 1965 for gold)
        3. Adjust the window size (days to confirm highs/lows)
        4. Set minimum pullback threshold
        5. Click **Analyze**
        
        ### Ticker Examples (Stooq Format)
        
        - **XAUUSD** - Gold
        - **AAPL.US** - Apple
        - **SPY.US** - S&P 500 ETF
        - **SI.F** - Silver Futures
        - **CL.F** - Crude Oil Futures
        """)


if __name__ == "__main__":
    main()
