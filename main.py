import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import requests
import pandas as pd
import pandas_ta as ta
from flask import Flask
import threading
import time

# ═══════════════════════════════════════
# الإعدادات - تم التعديل هنا
TOKEN = "8600613901:AAG6KPQ-C0ht9EDapYmqI5mcprxAxaoD7sk"
CHAT_ID = "6532633465"
# ═══════════════════════════════════════

SYMBOL = "XAUUSD"
INTERVAL = "1h"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Flask app للحفاظ على البوت شغال
app = Flask('')

@app.route('/')
def home():
    return "✅ Gold Signal Bot is Running!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.start()

async def get_gold_data():
    """جلب بيانات الذهب"""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/XAUUSD=X"
        params = {"interval": "1h", "range": "5d"}
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, params=params, headers=headers, timeout=10)
        data = response.json()
        
        timestamps = data['chart']['result'][0]['timestamp']
        prices = data['chart']['result'][0]['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'timestamp': pd.to_datetime(timestamps, unit='s'),
            'open': prices['open'],
            'high': prices['high'],
            'low': prices['low'],
            'close': prices['close']
        })
        
        return df.dropna()
    except Exception as e:
        logging.error(f"Error fetching data: {e}")
        return None

def analyze_signal(df):
    """تحليل الإشارة"""
    if df is None or len(df) < 50:
        return None
    
    # المؤشرات الفنية
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['ema20'] = ta.ema(df['close'], length=20)
    df['ema50'] = ta.ema(df['close'], length=50)
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    
    last = df.iloc[-1]
    
    # شروط الشراء
    buy_conditions = []
    if last['rsi'] < 35:
        buy_conditions.append("RSI تشبع بيعي")
    if last['close'] > last['ema20'] and df.iloc[-2]['close'] <= df.iloc[-2]['ema20']:
        buy_conditions.append("اختراق EMA20")
    if last['ema20'] > last['ema50']:
        buy_conditions.append("اتجاه صاعد")
    
    # شروط البيع
    sell_conditions = []
    if last['rsi'] > 65:
        sell_conditions.append("RSI تشبع شرائي")
    if last['close'] < last['ema20'] and df.iloc[-2]['close'] >= df.iloc[-2]['ema20']:
        sell_conditions.append("كسر EMA20")
    if last['ema20'] < last['ema50']:
        sell_conditions.append("اتجاه هابط")
    
    atr = last['atr'] if not pd.isna(last['atr']) else 2.0
    
    if len(buy_conditions) >= 2:
        entry = last['close']
        sl = entry - (atr * 1.5)
        tp = entry + (atr * 3)
        return {
            'signal': "🟢 شراء (BUY)",
            'price': entry,
            'sl': sl,
            'tp': tp,
            'reason': " | ".join(buy_conditions),
            'rsi': last['rsi']
        }
    
    if len(sell_conditions) >= 2:
        entry = last['close']
        sl = entry + (atr * 1.5)
        tp = entry - (atr * 3)
        return {
            'signal': "🔴 بيع (SELL)",
            'price': entry,
            'sl': sl,
            'tp': tp,
            'reason': " | ".join(sell_conditions),
            'rsi': last['rsi']
        }
    
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 بوت إشارات الذهب (XAU/USD)\n\n"
        "📊 الأوامر:\n"
        "/signal - تحليل فوري\n"
        "/status - حالة السوق\n"
        "/help - المساعدة\n\n"
        "⏰ الإشارات التلقائية كل ساعة"
    )

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ جاري تحليل الذهب...")
    
    df = await get_gold_data()
    result = analyze_signal(df)
    
    if result:
        message = f"""
⚡ إشارة الذهب (XAU/USD)

{result['signal']}

💰 سعر الدخول: {result['price']:.2f}
🛑 وقف الخسارة: {result['sl']:.2f}
🎯 الهدف: {result['tp']:.2f}

📊 التحليل:
• RSI: {result['rsi']:.1f}
• الأسباب: {result['reason']}

⚠️ تنويه: إشارة تحليلية - التداول يحتوي على مخاطر
        """
    else:
        message = "⏸️ لا توجد إشارة واضحة حالياً.\nالسوق متذبذب، انتظر فرصة أفضل."
    
    await update.message.reply_text(message)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    df = await get_gold_data()
    if df is not None:
        last = df.iloc[-1]
        rsi = ta.rsi(df['close'], length=14).iloc[-1]
        
        await update.message.reply_text(
            f"📊 حالة الذهب الآن:\n\n"
            f"💰 السعر: {last['close']:.2f}\n"
            f"📈 الأعلى: {last['high']:.2f}\n"
            f"📉 الأدنى: {last['low']:.2f}\n"
            f"📊 RSI: {rsi:.1f}"
        )
    else:
        await update.message.reply_text("❌ تعذر جلب البيانات")

async def auto_signals(context: ContextTypes.DEFAULT_TYPE):
    """إشارات تلقائية"""
    df = await get_gold_data()
    result = analyze_signal(df)
    
    if result:
        message = f"""
🔔 إشارة تلقائية - الذهب

{result['signal']}
السعر: {result['price']:.2f}
SL: {result['sl']:.2f} | TP: {result['tp']:.2f}

التحليل: {result['reason']}
        """
        await context.bot.send_message(chat_id=CHAT_ID, text=message)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 دليل الاستخدام:\n\n"
        "/signal - تحليل فني فوري\n"
        "/status - أسعار الذهب الحالية\n"
        "/auto - تفعيل الإشارات التلقائية\n"
        "/stop - إيقاف الإشارات\n\n"
        "⚠️ تنويه: البوت يستخدم مؤشرات فنية (RSI, EMA) "
        "ولا يضمن الأرباح. استخدم وقف خسارة دائماً."
    )

async def auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.job_queue.run_repeating(auto_signals, interval=3600, first=10)
    await update.message.reply_text("✅ تم تفعيل الإشارات التلقائية (كل ساعة)")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.job_queue.stop()
    await update.message.reply_text("🛑 تم إيقاف الإشارات")

def main():
    keep_alive()
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("signal", signal_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("auto", auto_command))
    application.add_handler(CommandHandler("stop", stop_command))
    
    # إشارة تلقائية عند التشغيل
    application.job_queue.run_once(auto_signals, 5)
    
    print("🤖 البوت يعمل الآن...")
    application.run_polling()

if __name__ == "__main__":
    main()