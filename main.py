import os
import io
import json
import shutil
from datetime import datetime
import pytz
import jdatetime
import openpyxl
import requests
import uvicorn

from fastapi import FastAPI, Depends, HTTPException, Request, File, UploadFile
from fastapi.templating import Jinja2Templates
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import List
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager

import database as db

# 🎯 دیکشنری تبدیل نام کاربری به نام محترمانه
USER_MAPPING = {
    "Hadi": "آقا هادی لطفی",
    "AmirAKS9": "امیر آقا عباسی",
    "Nima": "آقا نیما",
    "Naser": "آقا ناصر",
    "Gemany": "آقا ساجد",
    "Sana": "آقا سعید",
    "Hamid": "آقا حمید",
    "alisaj": "علی آقا سجادی",
    "alims": "علی آقا متولیان",
    "مسعود": "آقا مسعود",
    "ایران_رویایی": "آقا نادر",
    "hadisajadi": "آقا هادی متولیان",
    "amir_rainboe": "امیر آقا عباسی"
}


def get_persian_name(username):
    if not username: return "کاربر ناشناس"
    return USER_MAPPING.get(username, username)

# --- تنظیمات اتصال به بله ---
BALE_TOKEN = "928514616:u3lR097wIz127f4g4W0GXRyN9KJT5kADmlI"
BALE_CHAT_ID = "@Golchine_Akhbar"
ADMIN_BALE_ID = "189389617"

# --- تنظیمات API-Sports ---
API_SPORTS_KEY = "91a19f0ef86a021c15fb02f28539fe86"


# دیکشنری ترجمه نام تیم‌ها (برای تطبیق دیتابیس فارسی با نتایج انگلیسی)
TEAM_NAME_MAPPING = {
    "ایران": "Iran",
    "ایران": "Iran", 
    "نیوزیلند": "New Zealand", 
    "فرانسه": "France", 
    "سنگال": "Senegal", "عربستان": "Saudi Arabia", "اروگوئه": "Uruguay", "مکزیک": "Mexico", "کره جنوبی": "South Korea", "کانادا": "Canada", "قطر": "Qatar", "آمریکا": "USA", "هائیتی": "Haiti", "برزیل": "Brazil", "استرالیا": "Australia", "آلمان": "Germany", "هلند": "Netherlands", "ساحل عاج": "Ivory Coast", "سوئد": "Sweden", "اسپانیا": "Spain", "اسپانیای": "Spain", "بلژیک": "Belgium", "عراق": "Iraq", "آرژانتین": "Argentina", "اتریش": "Austria", "پرتغال": "Portugal", "انگلیس": "England", "غنا": "Ghana", "ازبکستان": "Uzbekistan", "جمهوری چک": "Czech Republic", "سوئیس": "Switzerland", "اسکاتلند": "Scotland", "ترکیه": "Turkey", "اکوادور": "Ecuador", "تونس": "Tunisia", "نروژ": "Norway", "اردن": "Jordan", "پاناما": "Panama", "کلمبیا": "Colombia", "آفریقای جنوبی": "South Africa", "بوسنی و هرزگوین": "Bosnia and Herzegovina", "مراکش": "Morocco", "پاراگوئه": "Paraguay", "کوراسائو": "Curacao", "ژاپن": "Japan", "مصر": "Egypt", "کیپ ورد": "Cape Verde", "الجزایر": "Algeria", "جمهوری کنگو": "Congo", "کرواسی": "Croatia" } 
def fetch_and_update_from_api(db_session: Session, target_date_str: str):
    """
    دریافت نتایج از API و آپدیت خودکار دیتابیس
    فرمت ورودی: YYYY-MM-DD
    """
    url = f"https://v3.football.api-sports.io/fixtures?date={target_date_str}"
    headers = {'x-apisports-key': API_SPORTS_KEY}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        
        if not data.get('response'):
            return "❌ هیچ دیتایی برای این تاریخ یافت نشد یا محدودیت API پر شده است."
            
        api_fixtures = data['response']
        updated_count = 0
        
        pending_matches = db_session.query(db.Match).filter(db.Match.status == "upcoming").all()
        
        for match in pending_matches:
            home_en = TEAM_NAME_MAPPING.get(match.home_team.strip())
            away_en = TEAM_NAME_MAPPING.get(match.away_team.strip())
            
            if not home_en or not away_en:
                continue 
                
            for api_match in api_fixtures:
                api_home = api_match['teams']['home']['name']
                api_away = api_match['teams']['away']['name']
                
                if api_home == home_en and api_away == away_en:
                    status_short = api_match['fixture']['status']['short']
                    
                    if status_short in ['FT', 'PEN', 'AET']:
                        match.actual_home_goals = api_match['goals']['home']
                        match.actual_away_goals = api_match['goals']['away']
                        match.status = "finished"
                        updated_count += 1
                        
                        db_session.commit()
                        calculate_leaderboard_data(db_session)
                        
                        try:
                            msg_text = generate_bale_summary_message(db_session, match.id)
                            if msg_text: 
                                send_bale_notification(msg_text)
                        except Exception as e:
                            print(f"Bale API error: {e}")
                    break 
                    
        return f"✅ سینک با موفقیت انجام شد. {updated_count} مسابقه ثبت و جدول آپدیت گردید."
        
    except Exception as e:
        return f"❌ خطای ارتباط با سرور خارجی: {e}"

# 🌟 تابع ارسال پیام (همراه با سیستم دیباگ و پردازش دکمه‌ها)
def send_bale_notification(message_text: str, target_chat_id=None, reply_markup=None):
    chat = target_chat_id if target_chat_id else BALE_CHAT_ID
    url = f"https://tapi.bale.ai/bot{BALE_TOKEN}/sendMessage"
    payload = {"chat_id": chat, "text": message_text}
   
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
       
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"❌ Bale API Error: {response.text}")
    except Exception as e:
        print(f"❌ Connection Error: {e}")

# 🌟 تابع ساخت و ارسال منوی اصلی کاربری
def send_user_main_menu(chat_id: str):
    menu_buttons = {
        "inline_keyboard": [
            [
                {"text": "⚽️ مسابقات پیش‌رو", "callback_data": "user_upcoming_matches"},
                {"text": "🏆 جدول رده‌بندی لیگ", "callback_data": "user_leaderboard"}
            ],
            [
                {"text": "📜 قوانین و امتیازدهی", "callback_data": "user_rules"}
            ]
        ]
    }
    welcome_text = "سلام! به ربات دستیار لیگ پیش‌بینی خوش آمدید. 👋\n\n👇 لطفاً یکی از گزینه‌های زیر را انتخاب کنید:"
    send_bale_notification(welcome_text, target_chat_id=chat_id, reply_markup=menu_buttons)

# --- سیستم بک‌آپ و زمان‌بندی ---
BACKUP_DIR = "data/backups"
if not os.path.exists(BACKUP_DIR): 
    os.makedirs(BACKUP_DIR)

def backup_database():
    try:
        now_str = jdatetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        shutil.copy2("data/football.db", f"{BACKUP_DIR}/football_backup_{now_str}.db")
    except Exception: 
        pass

iran_nz_warning_sent = False
def check_iran_nz_match():
    global iran_nz_warning_sent
    if iran_nz_warning_sent: 
        return
        
    db_session = db.SessionLocal()
    try:
        match = db_session.query(db.Match).filter(
            db.Match.home_team.contains('ایران'), 
            db.Match.away_team.contains('نیوزلند')
        ).first()
        
        if match and match.timestamp and match.status == 'upcoming':
            current_ts = datetime.now(pytz.timezone("Asia/Tehran")).timestamp()
            time_left = match.timestamp - current_ts
            if 0 < time_left <= 5 * 3600:
                send_bale_notification("🚨 **یادآوری مهم مسابقات!**\n\nتنها **۵ ساعت** تا شروع مسابقه حساس **ایران ⚡️ نیوزلند** باقی مانده است!\n⏳ فرم ثبت نتایج ۱۵ دقیقه قبل از سوت آغاز قفل خواهد شد.")
                iran_nz_warning_sent = True
    finally:
        db_session.close()

prompted_matches = set()
def check_finished_matches_prompt():
    db_session = db.SessionLocal()
    try:
        current_ts = datetime.now(pytz.timezone("Asia/Tehran")).timestamp()
        pending_matches = db_session.query(db.Match).filter(
            db.Match.status == "upcoming", 
            db.Match.timestamp != None, 
            db.Match.timestamp + 7200 <= current_ts
        ).all()
        
        for m in pending_matches:
            if m.id not in prompted_matches:
                msg = f"🔔 **مدیر عزیز، زمان مسابقه زیر به پایان رسیده است:**\n⚔️ **{m.home_team} - {m.away_team}**\n\n" \
                      f"الگوهای کلیک‌شدنی برای ثبت نتیجه سریع:\n" \
                      f"🔹 مساوی صفر - صفر: `/set_{m.id}_0_0`\n" \
                      f"🔹 برد یک - صفر میزبان: `/set_{m.id}_1_0`\n" \
                      f"🔹 برد دو - یک میزبان: `/set_{m.id}_2_1`\n" \
                      f"🔹 برد صفر - یک میهمان: `/set_{m.id}_0_1`\n\n" \
                      f"الگوی دستی: `/set_{m.id}_[میزبان]_[میهمان]`"
                send_bale_notification(msg, target_chat_id=ADMIN_BALE_ID)
                prompted_matches.add(m.id)
    finally:
        db_session.close()

scheduler = BackgroundScheduler()
scheduler.add_job(backup_database, 'cron', hour=3, minute=0)
scheduler.add_job(check_iran_nz_match, 'interval', minutes=5)
scheduler.add_job(check_finished_matches_prompt, 'interval', minutes=5)
scheduler.start()

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.Base.metadata.create_all(bind=db.engine)
    with db.engine.begin() as conn:
        try: 
            conn.execute(text("ALTER TABLE users ADD COLUMN previous_rank INTEGER DEFAULT 1;"))
        except Exception: 
            pass
            
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN username TEXT;"))
            conn.execute(text("UPDATE users SET username = name WHERE username IS NULL;"))
        except Exception: 
            pass
    yield

app = FastAPI(title="سیستم پیش‌بینی فوتبال", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

def get_db():
    db_session = db.SessionLocal()
    try: 
        yield db_session
    finally: 
        db_session.close()

def get_tehran_timestamp(j_date_str, time_str):
    try:
        clean_date = str(j_date_str).split(' ')[0].replace('-', '/')
        y, m, d = map(int, clean_date.split('/'))
        time_parts = str(time_str).split(':')
        dt_jalali = jdatetime.datetime(y, m, d, int(time_parts[0]), int(time_parts[1]), 0)
        tehran_tz = pytz.timezone("Asia/Tehran")
        return tehran_tz.localize(dt_jalali.togregorian()).timestamp()
    except Exception: 
        return None

def log_action(db_session: Session, request: Request, user_name: str, action: str, details: str):
    ip = request.client.host if request.client else "Unknown"
    ua = request.headers.get("user-agent", "Unknown")
    now_str = jdatetime.datetime.now().strftime("%Y/%m/%d - %H:%M:%S")
    db_session.add(db.AuditLog(user_name=user_name, action=action, details=details, ip_address=ip, user_agent=ua, timestamp=now_str))
    db_session.commit()

class BulkDeleteRequest(BaseModel): 
    match_ids: List[int]

class MatchResultItem(BaseModel): 
    match_id: int
    actual_home: int
    actual_away: int

class BulkFinishRequest(BaseModel): 
    results: List[MatchResultItem]

def calculate_leaderboard_data(db_session):
    users = db_session.query(db.User).all()
    
    # 🌟 تنظیم مبدا جدید محاسبات: ۲۶ خرداد ساعت ۰۴:۰۰ صبح به وقت تهران
    try:
        current_year = jdatetime.datetime.now().year
        # ماه ۳ (خرداد)، روز ۲۶، ساعت ۴، دقیقه ۰، ثانیه ۰
        dt_jalali = jdatetime.datetime(current_year, 3, 26, 4, 0, 0)
        tehran_tz = pytz.timezone("Asia/Tehran")
        threshold_ts = tehran_tz.localize(dt_jalali.togregorian()).timestamp()
    except Exception:
        threshold_ts = 0 # در صورت بروز خطای پیش‌بینی نشده، کل بازی‌ها محاسبه شوند

    # دریافت تمام بازی‌های تمام‌شده‌ای که زمان آن‌ها بعد از ۴ صبح ۲۶ خرداد است
    finished_matches = db_session.query(db.Match).filter(
        db.Match.status == "finished", 
        db.Match.timestamp >= threshold_ts
    ).all()
    
    prize_setting = db_session.query(db.SystemSetting).filter(db.SystemSetting.key == "total_prize").first()
    total_prize = float(prize_setting.value) if prize_setting else 0.0

    leaderboard_data = []
    for u in users:
        stats = {"exact": 0, "diff": 0, "winner": 0, "wrong": 0, "missed": 0, "score": 0}
        preds = {p.match_id: p for p in db_session.query(db.Prediction).filter(db.Prediction.user_id == u.id).all()}
        
        for fm in finished_matches:
            if fm.id not in preds: 
                stats["missed"] += 1
            else:
                p = preds[fm.id]
                a_diff = fm.actual_home_goals - fm.actual_away_goals
                p_diff = p.predicted_home_goals - p.predicted_away_goals
                
                if p.predicted_home_goals == fm.actual_home_goals and p.predicted_away_goals == fm.actual_away_goals:
                    stats["exact"] += 1
                    stats["score"] += 3
                elif p_diff == a_diff:
                    stats["diff"] += 1
                    stats["score"] += 2
                elif (a_diff > 0 and p_diff > 0) or (a_diff < 0 and p_diff < 0):
                    stats["winner"] += 1
                    stats["score"] += 1
                else: 
                    stats["wrong"] += 1
                    
        u.score = stats["score"]
        prev_rank = getattr(u, 'previous_rank', 1) or 1
        leaderboard_data.append({"id": u.id, "name": u.name, "username": u.username, "score": stats["score"], "previous_rank": prev_rank, "prize": 0.0, "trend": "-", **stats})
       
    db_session.commit()
    leaderboard_data.sort(key=lambda x: x["score"], reverse=True)
   
    current_rank = 1
    for i, row in enumerate(leaderboard_data):
        if i > 0 and leaderboard_data[i]["score"] < leaderboard_data[i-1]["score"]: 
            current_rank = i + 1
        row["rank"] = current_rank
        
        if row["rank"] < row["previous_rank"]: 
            row["trend"] = "up"
        elif row["rank"] > row["previous_rank"]: 
            row["trend"] = "down"
        else: 
            row["trend"] = "stable"

    if total_prize > 0 and leaderboard_data:
        from collections import defaultdict
        rank_groups = defaultdict(list)
        for row in leaderboard_data: 
            rank_groups[row["rank"]].append(row)
            
        first_place_users = rank_groups[1]
        distinct_ranks = sorted(rank_groups.keys())
        second_place_users = rank_groups[distinct_ranks[1]] if len(distinct_ranks) > 1 else []

        if len(first_place_users) >= 2:
            share = total_prize / len(first_place_users)
            for u in first_place_users: 
                u["prize"] = round(share, 2)
        elif len(first_place_users) == 1:
            u1 = first_place_users[0]
            if len(second_place_users) == 1:
                u2 = second_place_users[0]
                total_pts = u1["score"] + u2["score"]
                if total_pts > 0:
                    u1["prize"] = round((u1["score"] / total_pts) * total_prize, 2)
                    u2["prize"] = round((u2["score"] / total_pts) * total_prize, 2)
                else: 
                    u1["prize"] = u2["prize"] = round(total_prize / 2, 2)
            elif len(second_place_users) >= 2:
                u1["prize"] = round(0.55 * total_prize, 2)
                share_45 = (0.45 * total_prize) / len(second_place_users)
                for u in second_place_users: 
                    u["prize"] = round(share_45, 2)
            else: 
                u1["prize"] = total_prize

    return leaderboard_data

def generate_bale_summary_message(db_session: Session, finished_match_id: int) -> str:
    match = db_session.query(db.Match).filter(db.Match.id == finished_match_id).first()
    if not match:
        return ""
       
    # دریافت اطلاعات لحظه‌ای جدول برای استخراج ترند و جایزه
    lb_data = calculate_leaderboard_data(db_session)
    user_stats = {item['id']: item for item in lb_data}


    rlm = "\u200F" # کاراکتر نامرئی برای راست‌چین کردن قطعی پیام‌ها
    msg_parts = [
        f"{rlm}🏁 **سوت پایان! نتیجه نهایی در سیستم ثبت شد** 🏁\n",
        f"{rlm}⚽️ **{match.home_team} {match.actual_home_goals} - {match.actual_away_goals} {match.away_team}**",
        f"{rlm}*(پایان بازی)*\n\n{rlm}🏆 **وضعیت جدول و عملکرد کاربران در این بازی:**\n"
    ]
    
    predictions = db_session.query(db.Prediction, db.User).join(db.User, db.Prediction.user_id == db.User.id).filter(db.Prediction.match_id == finished_match_id).all()
    # مرتب‌سازی کاربران بر اساس امتیاز کل آن‌ها در جدول
    predictions.sort(key=lambda x: user_stats.get(x[1].id, {}).get('score', 0), reverse=True)
    
    medals = ["🥇", "🥈", "🥉"]
    for idx, (pred, user) in enumerate(predictions):
        medal = medals[idx] if idx < 3 else "👤"
        stats = user_stats.get(user.id, {})
        
        # ۱. رفع باگ فرمول امتیازدهی
        ph = pred.predicted_home_goals
        pa = pred.predicted_away_goals
        ah = match.actual_home_goals
        aa = match.actual_away_goals
        
        if ph == ah and pa == aa:
            point_text = "👈 *3 امتیاز کامل*"
        elif (ph - pa) == (ah - aa):
            point_text = "👈 *2 امتیاز (تفاضل)*"
        elif (ph > pa and ah > aa) or (ph < pa and ah < aa):
            point_text = "👈 *1 امتیاز (برنده)*"
        else:
            point_text = "👈 *بدون امتیاز*"


        # ۲. استخراج نام تیمی که کاربر برنده دانسته
        if ph > pa:
            pred_winner = match.home_team
        elif pa > ph:
            pred_winner = match.away_team
        else:
            pred_winner = "مساوی"


        # ۳. ترند و جایزه
        trend_val = stats.get('trend', '-')
        prize_val = stats.get('prize', 0)
        total_score = stats.get('score', 0)
        
        trend_icon = "🟢 صعود" if trend_val == 'up' else "🔴 سقوط" if trend_val == 'down' else "⚪️ ثابت"
        prize_text = f" | 💰 {prize_val:,.0f} تومان" if prize_val > 0 else ""


        # ۴. تغییر نام کاربری
        raw_name = user.username if user.username else user.name
        display_name = get_persian_name(raw_name)


        # ۵. سرهم کردن پیام راست‌چین
        msg_parts.append(f"{rlm}{medal} **{display_name}** | ⭐️ {total_score} امتیاز | 📊 {trend_icon}{prize_text}\n{rlm}🎯 حدس: ({ph} - {pa}) {pred_winner} {point_text}\n")


    msg_parts.append(f"{rlm}➖➖➖➖➖➖➖➖➖➖\n")
    next_match = db_session.query(db.Match).filter(db.Match.status == "upcoming").order_by(db.Match.timestamp.asc()).first()
   
    if next_match:
        time_left_str = "نامشخص"
        if next_match.timestamp:
            time_diff = datetime.fromtimestamp(next_match.timestamp) - datetime.now()
            ts_secs = int(time_diff.total_seconds())
            if ts_secs > 0:
                h, r = divmod(ts_secs, 3600)
                m = r // 60
                time_left_str = f"{h} ساعت و {m} دقیقه"
            else:
                time_left_str = "زمان ثبت پیش‌بینی تمام شده!"


        msg_parts.append(f"{rlm}🔜 **نبرد بعدی فرا رسید!**\n{rlm}⚔️ **{next_match.home_team} 🆚 {next_match.away_team}**\n\n{rlm}⏳ **زمان تا قفل فرم:** {time_left_str}\n\n{rlm}👀 **پیش‌بینی‌های ثبت‌شده:**")
        next_preds = db_session.query(db.Prediction, db.User).join(db.User, db.Prediction.user_id == db.User.id).filter(db.Prediction.match_id == next_match.id).all()
        predicted_user_ids = []
       
        for pred, user in next_preds:
            raw_name = user.username if user.username else user.name
            dn = get_persian_name(raw_name)
            msg_parts.append(f"{rlm}👤 {dn}: {next_match.home_team} {pred.predicted_home_goals} - {pred.predicted_away_goals} {next_match.away_team}")
            predicted_user_ids.append(user.id)


        all_users = db_session.query(db.User).all()
        missing_users = [u for u in all_users if u.id not in predicted_user_ids]
        if missing_users:
            msg_parts.append(f"\n{rlm}⚠️ **هشدار به غایبین!**\n{rlm}تا دیر نشده فرم رو پر کنید:")
            missing_names = [f"@{get_persian_name(u.username if u.username else u.name)}" for u in missing_users]
            msg_parts.append(f"{rlm}" + "، ".join(missing_names))


    msg_parts.append(f"\n{rlm}👇 **همین الان به سایت مراجعه و پیش‌بینی‌ات رو ثبت کن!**")
    return "\n".join(msg_parts)


# --- مسیرهای مربوط به FastAPI ---

@app.get("/")
def home(request: Request): 
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request})

@app.get("/admin")
def admin_page(request: Request): 
    return templates.TemplateResponse(request=request, name="admin.html", context={"request": request})

@app.get("/matches/list")
def get_matches(db_session: Session = Depends(get_db)): 
    return db_session.query(db.Match).order_by(db.Match.timestamp).all()

@app.get("/users/list")
def get_users(db_session: Session = Depends(get_db)): 
    return db_session.query(db.User).order_by(db.User.id.desc()).all()

@app.get("/admin/logs")
def get_audit_logs(db_session: Session = Depends(get_db)): 
    return db_session.query(db.AuditLog).order_by(db.AuditLog.id.desc()).limit(300).all()

@app.get("/admin/prize")
def get_prize(db_session: Session = Depends(get_db)):
    setting = db_session.query(db.SystemSetting).filter(db.SystemSetting.key == "total_prize").first()
    return {"total_prize": float(setting.value) if setting else 0.0}

@app.post("/admin/prize")
def set_prize(total_prize: float, db_session: Session = Depends(get_db)):
    setting = db_session.query(db.SystemSetting).filter(db.SystemSetting.key == "total_prize").first()
    if setting: 
        setting.value = str(total_prize)
    else: 
        db_session.add(db.SystemSetting(key="total_prize", value=str(total_prize)))
    db_session.commit()
    return {"status": "success"}

# 🌟 روت جدید برای دانلود امن دیتابیس
@app.get("/download-backup/{secret_password}")
def get_database_backup(secret_password: str):
    # این رمز عبور اختصاصی شماست
    if secret_password != "admin_my_secret_pass":
        return {"error": "⛔️ دسترسی غیرمجاز"}
        
    db_path = "data/football.db"
    
    if os.path.exists(db_path):
        return FileResponse(
            path=db_path, 
            filename="football_live_backup.db", 
            media_type="application/octet-stream"
        )
    else:
        return {"error": "❌ فایل دیتابیس یافت نشد!"}

@app.get("/leaderboard/")
def get_leaderboard(db_session: Session = Depends(get_db)): 
    return calculate_leaderboard_data(db_session)

@app.get("/predictions/all")
def get_all_predictions(db_session: Session = Depends(get_db)):
    preds = db_session.query(db.Prediction).all()
    users = {u.id: u.username for u in db_session.query(db.User).all()}
    result = {}
    for p in preds:
        if p.match_id not in result: 
            result[p.match_id] = []
        if p.user_id in users: 
            result[p.match_id].append({"user_name": users[p.user_id], "home": p.predicted_home_goals, "away": p.predicted_away_goals})
    return result

@app.get("/predictions/user/{user_id}")
def get_user_predictions(user_id: int, db_session: Session = Depends(get_db)):
    return db_session.query(db.Prediction).filter(db.Prediction.user_id == user_id).all()

@app.post("/users/")
def create_user(request: Request, name: str, username: str, password: str, db_session: Session = Depends(get_db)):
    if db_session.query(db.User).filter(db.User.username == username).first(): 
        raise HTTPException(status_code=400, detail="یوزرنیم تکراری است")
    
    now_str = jdatetime.datetime.now().strftime("%Y/%m/%d - %H:%M")
    new_user = db.User(name=name, username=username, password=password, last_login=now_str)
    db_session.add(new_user)
    db_session.commit()
    
    log_action(db_session, request, username, "ثبت‌نام", "ثبت‌نام جدید")
    return {"status": "success", "user_id": new_user.id, "name": new_user.name, "username": new_user.username}

@app.post("/login/")
def login_user(request: Request, username: str, password: str, db_session: Session = Depends(get_db)):
    if username == "admin" and password == "manhastam":
        log_action(db_session, request, "مدیریت", "ورود ادمین", "ورود به سیستم")
        return {"status": "success", "user_id": 0, "name": "مدیریت", "username": "admin", "is_admin": True}
       
    user = db_session.query(db.User).filter(db.User.username == username).first()
    if not user or user.password != password:
        raise HTTPException(status_code=400, detail="یوزرنیم یا رمز اشتباه است")
       
    user.last_login = jdatetime.datetime.now().strftime("%Y/%m/%d - %H:%M")
    db_session.commit()
    log_action(db_session, request, user.username, "ورود", "موفق")
   
    # --- ارسال هشدار ورود به پی‌وی ادمین ---
    try:
        admin_msg = f"🔔 ورود به سایت\n👤 کاربر: {user.username}\n⏰ زمان: {user.last_login}"
        send_bale_notification(admin_msg, target_chat_id=ADMIN_BALE_ID)
    except Exception as e:
        print(f"Login notification error: {e}")
    # ---------------------------------------
   
    return {"status": "success", "user_id": user.id, "name": user.name, "username": user.username, "is_admin": False}

@app.post("/users/edit/{target_user_id}")
def edit_user_username(target_user_id: int, new_username: str, db_session: Session = Depends(get_db)):
    existing = db_session.query(db.User).filter(db.User.username == new_username).first()
    if existing and existing.id != target_user_id: 
        return {"status": "error", "detail": "یوزرنیم تکراری است"}
        
    user = db_session.query(db.User).filter(db.User.id == target_user_id).first()
    if user:
        user.username = new_username
        db_session.commit()
        return {"status": "success"}
        
    return {"status": "error"}

@app.post("/matches/edit/{match_id}")
def edit_match_names(match_id: int, home: str, away: str, db_session: Session = Depends(get_db)):
    match = db_session.query(db.Match).filter(db.Match.id == match_id).first()
    if match: 
        match.home_team = home
        match.away_team = away
        db_session.commit()
        return {"status": "success"}
    return {"status": "error"}

@app.post("/matches/")
def create_match(home_team: str, away_team: str, match_date: str, match_time: str, stadium: str="نامشخص", group_name: str="نامشخص", db_session: Session = Depends(get_db)):
    ts = get_tehran_timestamp(match_date, match_time)
    new_match = db.Match(home_team=home_team, away_team=away_team, match_date=match_date, match_time=match_time, stadium=stadium, group_name=group_name, timestamp=ts)
    db_session.add(new_match)
    db_session.commit()
    return new_match

@app.post("/matches/upload/")
def upload_matches(file: UploadFile = File(...), db_session: Session = Depends(get_db)):
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file.file.read()), data_only=True)
        sheet = workbook.active
        added = 0
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if row[0] and row[1]:
                h, a = str(row[0]).strip(), str(row[1]).strip()
                d = str(row[2]).strip() if len(row)>2 and row[2] else "نامشخص"
                t = str(row[3]).strip() if len(row)>3 and row[3] else "نامشخص"
                s = str(row[4]).strip() if len(row)>4 and row[4] else "نامشخص"
                g = str(row[5]).strip() if len(row)>5 and row[5] else "نامشخص"
                ts = get_tehran_timestamp(d, t)
                
                db_session.add(db.Match(home_team=h, away_team=a, match_date=d, match_time=t, stadium=s, group_name=g, timestamp=ts))
                added += 1
                
        db_session.commit()
        return {"status": "success", "message": f"{added} مسابقه اضافه شد"}
    except Exception as e: 
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/matches/{match_id}")
def delete_match(match_id: int, db_session: Session = Depends(get_db)):
    match = db_session.query(db.Match).filter(db.Match.id == match_id).first()
    if match: 
        db_session.query(db.Prediction).filter(db.Prediction.match_id == match_id).delete()
        db_session.delete(match)
        db_session.commit()
    return {"status": "success"}

@app.post("/matches/bulk-delete")
def bulk_delete_matches(req: BulkDeleteRequest, db_session: Session = Depends(get_db)):
    db_session.query(db.Prediction).filter(db.Prediction.match_id.in_(req.match_ids)).delete(synchronize_session=False)
    db_session.query(db.Match).filter(db.Match.id.in_(req.match_ids)).delete(synchronize_session=False)
    db_session.commit()
    return {"status": "success"}

@app.post("/matches/bulk-revert")
def bulk_revert_matches(req: BulkDeleteRequest, db_session: Session = Depends(get_db)):
    for mid in req.match_ids:
        match = db_session.query(db.Match).filter(db.Match.id == mid).first()
        if match: 
            match.status = "upcoming"
            match.actual_home_goals = None
            match.actual_away_goals = None
            
    db_session.commit()
    calculate_leaderboard_data(db_session)
    return {"status": "success"}

@app.post("/predictions/")
def create_prediction(request: Request, user_id: int, match_id: int, home_goals: int, away_goals: int, db_session: Session = Depends(get_db)):
    match = db_session.query(db.Match).filter(db.Match.id == match_id).first()
    if not match or match.status != "upcoming": 
        raise HTTPException(status_code=400, detail="مسابقه یافت نشد")
    if not match.timestamp: 
        raise HTTPException(status_code=400, detail="تاریخ نامعتبر است.")
    if datetime.now(pytz.timezone("Asia/Tehran")).timestamp() >= (match.timestamp - 900): 
        raise HTTPException(status_code=400, detail="مهلت ثبت پیش‌بینی تمام شده")

    user = db_session.query(db.User).filter(db.User.id == user_id).first()
    pred = db_session.query(db.Prediction).filter(db.Prediction.user_id == user_id, db.Prediction.match_id == match_id).first()
   
    if pred:
        pred.predicted_home_goals = home_goals
        pred.predicted_away_goals = away_goals
        action = "ویرایش پیش‌بینی"
    else:
        db_session.add(db.Prediction(user_id=user_id, match_id=match_id, predicted_home_goals=home_goals, predicted_away_goals=away_goals))
        action = "ثبت پیش‌بینی جدید"
       
    db_session.commit()
    log_action(db_session, request, user.username if user else "Unknown", action, f"بازی: {match.home_team}-{match.away_team} | {home_goals}-{away_goals}")
    return {"status": "success"}

@app.post("/matches/bulk-finish")
def bulk_finish_matches(req: BulkFinishRequest, db_session: Session = Depends(get_db)):
    all_users = db_session.query(db.User).all()
    sorted_by_current = sorted(all_users, key=lambda x: x.score, reverse=True)
    current_rank = 1
    
    for i, u in enumerate(sorted_by_current):
        if i > 0 and sorted_by_current[i].score < sorted_by_current[i-1].score: 
            current_rank = i + 1
        u.previous_rank = current_rank
    db_session.commit()

    trigger_excel_backup = False
    finished_match_ids = []

    for item in req.results:
        match = db_session.query(db.Match).filter(db.Match.id == item.match_id).first()
        if match:
            is_newly_finished = (match.status != "finished")
            match.actual_home_goals = item.actual_home
            match.actual_away_goals = item.actual_away
            match.status = "finished"
            
            if is_newly_finished:
                finished_match_ids.append(match.id)
                if "عربستان" in match.home_team and "اروگوئه" in match.away_team: 
                    trigger_excel_backup = True

    db_session.commit()
    calculate_leaderboard_data(db_session)

    if trigger_excel_backup:
        try:
            lb_data = calculate_leaderboard_data(db_session)
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Phase 1 Final"
            ws.append(["رتبه", "یوزرنیم", "نام واقعی", "امتیاز", "دقیق", "تفاضل", "برنده", "غلط"])
            for r in lb_data: 
                ws.append([r['rank'], r['username'], r['name'], r['score'], r['exact'], r['diff'], r['winner'], r['wrong']])
            wb.save("data/backups/leaderboard_phase1_final.xlsx")
        except Exception: 
            pass
           
    for match_id in finished_match_ids:
        try:
            msg_text = generate_bale_summary_message(db_session, match_id)
            if msg_text: 
                send_bale_notification(msg_text)
        except Exception as e: 
            print(f"Bale error: {e}")

    return {"status": "success"}

# 🌟 تابع مرکزی دریافت اطلاعات (وب‌هوک) شامل پردازش کلیک روی دکمه‌های شیشه‌ای
@app.post("/bale-webhook")
async def bale_webhook(request: Request, db_session: Session = Depends(get_db)):
    try:
        data = await request.json()
       
        # ۱. پردازش کلیک روی دکمه‌های شیشه‌ای (Callback Queries)
        if "callback_query" in data:
            callback_data = data["callback_query"]["data"]
            chat_id = str(data["callback_query"]["message"]["chat"]["id"])
            cb_id = data["callback_query"]["id"]
           
            # ارسال پاسخ کوتاه به سرور بله تا لودینگ روی دکمه برداشته شود
            try: 
                requests.post(f"https://tapi.bale.ai/bot{BALE_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=5)
            except Exception: 
                pass

            if callback_data == "user_leaderboard":
                lb_data = calculate_leaderboard_data(db_session)
                msg = "🏆 **جدول رده‌بندی لحظه‌ای لیگ:**\n\n"
                for row in lb_data: 
                    msg += f"🏅 {row['rank']} - {row['username']} | {row['score']} امتیاز\n"
                send_bale_notification(msg, target_chat_id=chat_id)
           
            elif callback_data == "user_upcoming_matches":
                matches = db_session.query(db.Match).filter(db.Match.status == "upcoming").all()
                if not matches:
                    send_bale_notification("در حال حاضر هیچ مسابقه‌ای برای پیش‌بینی تعریف نشده است.", target_chat_id=chat_id)
                else:
                    msg = "⚽️ **لیست مسابقات پیش‌رو:**\n*(برای ثبت پیش‌بینی باید وارد سایت شوید)*\n\n"
                    for m in matches: 
                        msg += f"⚔️ {m.home_team} - {m.away_team}\n"
                    send_bale_notification(msg, target_chat_id=chat_id)
           
            elif callback_data == "user_rules":
                rules_msg = "📜 **قوانین و نحوه امتیازدهی:**\n\n" \
                            "✅ **۳ امتیاز:** اگر تعداد گل‌های هر دو تیم را کاملاً درست حدس بزنید.\n" \
                            "✅ **۲ امتیاز:** اگر فقط اختلاف گل‌ها (تفاضل) یا مساوی بودن را درست حدس بزنید.\n" \
                            "✅ **۱ امتیاز:** اگر فقط تشخیص دهید کدام تیم برنده می‌شود.\n" \
                            "❌ **۰ امتیاز:** پیش‌بینی کاملاً اشتباه."
                send_bale_notification(rules_msg, target_chat_id=chat_id)
           
            return {"status": "ok"}

        # ۲. پردازش پیام‌های متنی عادی
        if "message" in data:
            chat_id = str(data["message"]["chat"]["id"])
            text = data["message"].get("text", "").strip()

            # نمایش منوی اصلی به هر کاربری که وارد ربات می‌شود (بدون نیاز به دسترسی ادمین)
            if text in ["/start", "شروع", "منو", "/menu"]:
                send_user_main_menu(chat_id)
                return {"status": "ok"}

            # ---- محدودیت دسترسی به دستورات مدیریتی ----
            if chat_id != ADMIN_BALE_ID:
                send_bale_notification(f"⛔️ دسترسی غیرمجاز!\nآیدی‌ای که سرور از شما دریافت کرد: {chat_id}\nآیدی‌ای که در کد تنظیم شده است: {ADMIN_BALE_ID}", target_chat_id=chat_id)
                return {"status": "unauth"}

            # دستورات ادمین
            if text == "/users":
                users = db_session.query(db.User).all()
                msg = "👥 **لیست تمام کاربران سیستم:**\n\n"
                for u in users: 
                    msg += f"👤 {u.name} ({u.username}) | ⭐️ {u.score} امتیاز\n📥 پیش‌بینی‌ها: /userpreds_{u.username}\n-------------------\n"
                send_bale_notification(msg, target_chat_id=chat_id)

            elif text.startswith("/userpreds_"):
                target_username = text.replace("/userpreds_", "").strip()
                user = db_session.query(db.User).filter(db.User.username == target_username).first()
                if not user:
                    send_bale_notification("❌ کاربر یافت نشد.", target_chat_id=chat_id)
                    return {"status": "ok"}
                preds = db_session.query(db.Prediction).filter(db.Prediction.user_id == user.id).all()
                msg = f"📊 **پیش‌بینی‌های {user.username}:**\n\n"
                for p in preds:
                    match = db_session.query(db.Match).filter(db.Match.id == p.match_id).first()
                    if match: 
                        msg += f"⚽️ {match.home_team} - {match.away_team} ⟵ ({p.predicted_home_goals} - {p.predicted_away_goals})\n"
                send_bale_notification(msg if preds else "هنوز پیش‌بینی ثبت نکرده است.", target_chat_id=chat_id)

            elif text.startswith("/ap "):
                # الگو: /ap 20260616
                parts = text.split(" ")
                if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
                    raw_date = parts[1]
                    # تبدیل خودکار 20260616 به 2026-06-16
                    target_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                    
                    send_bale_notification(f"⏳ در حال ارتباط با سرور جهانی برای نتایج {target_date}...", target_chat_id=chat_id)
                    result_msg = fetch_and_update_from_api(db_session, target_date)
                    send_bale_notification(result_msg, target_chat_id=chat_id)
                else:
                    send_bale_notification("❌ فرمت اشتباه است. لطفاً تاریخ را ۸ رقمی و بدون خط تیره وارد کنید:\nمثال: `/ap 20260616`", target_chat_id=chat_id)
            
            elif text == "/live":
                today_str = datetime.now().strftime("%Y-%m-%d")
                send_bale_notification("⚡ در حال آپدیت لحظه‌ای نتایج بازی‌های امروز...", target_chat_id=chat_id)
                result_msg = fetch_and_update_from_api(db_session, today_str)
                send_bale_notification(result_msg, target_chat_id=chat_id)

            elif text == "/table" or text == "/leaderboard":
                lb_data = calculate_leaderboard_data(db_session)
                msg = "🏆 **جدول رده‌بندی کل مسابقات:**\n\n"
                for row in lb_data: 
                    msg += f"🏅 رتبه {row['rank']} | **{row['username']}** | ⭐️ {row['score']} امتیاز\n"
                send_bale_notification(msg, target_chat_id=chat_id)

            elif text == "/matches":
                matches = db_session.query(db.Match).all()
                if not matches:
                    send_bale_notification("❌ هیچ مسابقه‌ای ثبت نشده است.", target_chat_id=chat_id)
                else:
                    from collections import defaultdict
                    grouped_matches = defaultdict(list)
                    
                    # دسته‌بندی بازی‌ها بر اساس نام گروه
                    for m in matches:
                        group_n = m.group_name if m.group_name else "نامشخص"
                        grouped_matches[group_n].append(m)
                    
                    # ارسال هر گروه در یک پیام مجزا
                    for group_name, m_list in grouped_matches.items():
                        msg = f"🏆 **بازی‌های {group_name}:**\n\n"
                        for m in m_list:
                            status_text = "🏁 تمام‌شده" if m.status == "finished" else "⏳ آینده"
                            msg += f"⚔️ **{m.home_team} - {m.away_team}**\n🆔 ID: {m.id} | {status_text}\n📊 /mp_{m.id} | ⚠️ /absent_{m.id}\n-------------------\n"
                        send_bale_notification(msg, target_chat_id=chat_id)


            elif text.startswith("/mp_"):
                try:
                    m_id = int(text.replace("/mp_", "").strip())
                    match = db_session.query(db.Match).filter(db.Match.id == m_id).first()
                    if not match: 
                        return {"status": "ok"}
                    preds = db_session.query(db.Prediction, db.User).join(db.User, db.Prediction.user_id == db.User.id).filter(db.Prediction.match_id == m_id).all()
                    msg = f"📊 **پیش‌بینی‌های بازی [{match.home_team} - {match.away_team}]:**\n\n"
                    for p, u in preds: 
                        msg += f"👤 {u.username}: ({p.predicted_home_goals} - {p.predicted_away_goals})\n"
                    send_bale_notification(msg if preds else "پیش‌بینی ثبت نشده است.", target_chat_id=chat_id)
                except ValueError: 
                    pass

            elif text.startswith("/absent_"):
                try:
                    m_id = int(text.replace("/absent_", "").strip())
                    match = db_session.query(db.Match).filter(db.Match.id == m_id).first()
                    if not match: 
                        return {"status": "ok"}
                    preds = db_session.query(db.Prediction).filter(db.Prediction.match_id == m_id).all()
                    predicted_user_ids = [p.user_id for p in preds]
                    all_users = db_session.query(db.User).all()
                    missing_users = [u for u in all_users if u.id not in predicted_user_ids]
                    msg = f"⚠️ **غایبین فرم بازی [{match.home_team} - {match.away_team}]:**\n\n"
                    for u in missing_users: 
                        msg += f"👤 @{u.username}\n"
                    send_bale_notification(msg if missing_users else "✅ همه پیش‌بینی کرده‌اند.", target_chat_id=chat_id)
                except ValueError: 
                    pass

            elif text.startswith("/set_"):
                parts = text.split("_")
                if len(parts) == 4:
                    try:
                        m_id, h_goals, a_goals = int(parts[1]), int(parts[2]), int(parts[3])
                        match = db_session.query(db.Match).filter(db.Match.id == m_id).first()
                        if not match:
                            send_bale_notification("❌ بازی یافت نشد.", target_chat_id=chat_id)
                            return {"status": "ok"}
                       
                        match.actual_home_goals = h_goals
                        match.actual_away_goals = a_goals
                        match.status = "finished"
                        db_session.commit()
                        calculate_leaderboard_data(db_session)
                       
                        ch_msg = generate_bale_summary_message(db_session, match.id)
                        if ch_msg: 
                            send_bale_notification(ch_msg)
                       
                        send_bale_notification(f"✅ نتیجه با موفقیت ثبت و گزارش ارسال شد.", target_chat_id=chat_id)
                    except ValueError:
                        send_bale_notification("❌ قالب ورودی گل‌ها معتبر نیست.", target_chat_id=chat_id)
            else:
                help_msg = "🤖 **سلام مدیر عزیز! پنل فرمان سریع:**\n\n🔹 /users ⟶ لیست کاربران\n🔹 /matches ⟶ لیست بازی‌ها\n🔹 /table ⟶ جدول رده‌بندی\n\n✍️ فرمت ثبت فوری تک بازی:\n`/set_[match_id]_[home]_[away]`\nمثال: `/set_5_2_1`"
                send_bale_notification(help_msg, target_chat_id=chat_id)

        return {"status": "ok"}
    except Exception: 
        return {"status": "error"}

def smart_auto_update():
    """تابعی که توسط زمان‌بند در پس‌زمینه اجرا می‌شود"""
    db_session = next(db.get_db())
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        fetch_and_update_from_api(db_session, today_str)
    except Exception as e:
        print(f"Auto-sync failed: {e}")
    finally:
        db_session.close()

# 🌟 راه‌اندازی زمان‌بند هوشمند 🌟
scheduler = BackgroundScheduler()
# بخش اول: اجرا در ساعت ۲۲ (فقط از دقیقه ۳۰ تا ۵۹، هر ۸ دقیقه یک‌بار)
scheduler.add_job(smart_auto_update, 'cron', hour='22', minute='30-59/8')
# بخش دوم: اجرا از ساعت ۲۳:۰۰ تا ۰۶:۵۹ صبح (هر ۸ دقیقه یک‌بار در کل ساعت)
scheduler.add_job(smart_auto_update, 'cron', hour='23,0-6', minute='*/8')
scheduler.start()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)