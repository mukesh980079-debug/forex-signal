import os, time, requests, statistics, math
from datetime import datetime

# --- Config (better to set as env vars in Railway) ---
BOT_TOKEN = os.getenv("BOT_TOKEN")            # telegram bot token
CHAT_ID   = os.getenv("CHAT_ID")              # telegram chat id (numeric)
SYMBOL    = os.getenv("SYMBOL", "BTCUSDT")
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", "300"))  # 5 minutes

# thresholds (strict mode)
VOL_RATIO_MIN = float(os.getenv("VOL_RATIO_MIN", "1.15"))  # current_vol / avg_vol
OI_PC_MIN     = float(os.getenv("OI_PC_MIN", "5"))        # percent change vs avg

# SL/TP in points (change as you like)
SL_PTS = int(os.getenv("SL_PTS", "100"))
TP1_PTS = int(os.getenv("TP1_PTS", "170"))
TP2_PTS = int(os.getenv("TP2_PTS", "250"))
TP3_PTS = int(os.getenv("TP3_PTS", "300"))

# anti-spam: don't send same type of signal within X minutes
MIN_REPEAT_MINUTES = int(os.getenv("MIN_REPEAT_MINUTES", "30"))

# Binance endpoints (public)
OI_API = "https://fapi.binance.com/futures/data/openInterestHist"
KLINES_API = "https://api.binance.com/api/v3/klines"

# helper: send telegram
def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram credentials not set. Skipping send.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print("Telegram:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram send error:", e)

def fetch_oi(symbol, period="5m", limit=12):
    params = {"symbol": symbol, "period": period, "limit": limit}
    r = requests.get(OI_API, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    oi_list = []
    for item in data:
        # API returns keys like 'sumOpenInterest' or 'openInterest'; be flexible
        oi = item.get("sumOpenInterest") or item.get("openInterest") or item.get("sumOpenInterestValue")
        try:
            oi_list.append(float(oi))
        except:
            oi_list.append(0.0)
    return oi_list

def fetch_klines_vol_price(symbol, interval="5m", limit=50):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(KLINES_API, params=params, timeout=10)
    r.raise_for_status()
    klines = r.json()
    volumes = [float(k[5]) for k in klines]       # index 5 = volume
    closes  = [float(k[4]) for k in klines]       # index 4 = close price
    return volumes, closes

def compute_confidence(oi_pct, vol_ratio):
    # basic heuristic -> scale to 0-100
    score = 30 + min(40, abs(oi_pct)*2) + min(30, max(0, (vol_ratio-1)*100))
    return int(max(0, min(100, score)))

def main_loop():
    last_signal = {"type": None, "time": 0}
    send_telegram("🚀 Bot started. Only STRONG BUY/SELL will be sent ✅")
    while True:
        try:
            volumes, closes = fetch_klines_vol_price(SYMBOL, interval="5m", limit=50)
            oi_list = fetch_oi(SYMBOL, period="5m", limit=12)

            if not oi_list or not volumes or not closes:
                print("No data, sleeping...")
                time.sleep(10)
                continue

            current_vol = volumes[-1]
            avg_vol = statistics.mean(volumes[-21:-1]) if len(volumes) >= 22 else statistics.mean(volumes[:-1] or volumes)
            vol_ratio = current_vol / (avg_vol if avg_vol>0 else 1)

            current_oi = oi_list[-1]
            avg_oi = statistics.mean(oi_list[:-1]) if len(oi_list) >= 2 else statistics.mean(oi_list)
            oi_pct = (current_oi - avg_oi) / (avg_oi if avg_oi>0 else 1) * 100

            last_price = closes[-1]

            print(f"[{datetime.utcnow().isoformat()}] Close={last_price:.2f} | Vol={current_vol} (Avg={avg_vol:.0f}) | OI={current_oi:.0f}")
            print(f"VolRatio={vol_ratio:.2f} | OI%={oi_pct:.2f}")

            signal = None
            # STRONG BUY condition
            if (current_oi > avg_oi*(1 + OI_PC_MIN/100)) and (vol_ratio >= VOL_RATIO_MIN):
                signal = "BUY"
            # STRONG SELL condition
            elif (current_oi < avg_oi*(1 - OI_PC_MIN/100)) and (vol_ratio >= VOL_RATIO_MIN):
                signal = "SELL"

            now_ts = time.time()
            minutes_since = (now_ts - last_signal["time"]) / 60

            if signal:
                # avoid duplicates
                if last_signal["type"] == signal and minutes_since < MIN_REPEAT_MINUTES:
                    print("Duplicate signal within cool-down, skipping.")
                else:
                    # build SL/TP
                    if signal == "BUY":
                        sl = round(last_price - SL_PTS, 2)
                        tp1 = round(last_price + TP1_PTS, 2)
                        tp2 = round(last_price + TP2_PTS, 2)
                        tp3 = round(last_price + TP3_PTS, 2)
                        direction = "📈 *STRONG BUY*"
                    else:
                        sl = round(last_price + SL_PTS, 2)
                        tp1 = round(last_price - TP1_PTS, 2)
                        tp2 = round(last_price - TP2_PTS, 2)
                        tp3 = round(last_price - TP3_PTS, 2)
                        direction = "📉 *STRONG SELL*"

                    conf = compute_confidence(oi_pct, vol_ratio)
                    volpct = (vol_ratio - 1) * 100

                    message = (
                        f"{direction}\n\n"
                        f"*Pair:* {SYMBOL}\n"
                        f"*Price:* {last_price:.2f}\n"
                        f"*OI Δ:* {oi_pct:.2f}%   |  *Vol %:* {volpct:.1f}%\n"
                        f"*Confidence:* {conf}%\n\n"
                        f"*Entry:* {last_price:.2f}\n"
                        f"*SL:* {sl}\n"
                        f"*TP1:* {tp1}  |  *TP2:* {tp2}  |  *TP3:* {tp3}\n\n"
                        f"_Signal generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_"
                    )

                    send_telegram(message)
                    last_signal = {"type": signal, "time": now_ts}
            else:
                print("No strong signal.")

        except Exception as e:
            print("Main loop error:", e)

        # sleep for configured time
        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main_loop()
      
