from fastapi import FastAPI, Depends, HTTPException, Request, File, UploadFile
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from fps_scheduler_placeholder import BackgroundScheduler # Placeholder if needed, standard apscheduler used
from apscheduler.schedulers.background import BackgroundScheduler
from contextlib import asynccontextmanager
from sqlalchemy import text
import database as db
import openpyxl
import io
import os
import shutil
import jdatetime
import pytz
from datetime import datetime
import uvicorn

# --- سیستم بک‌آپ‌گیری خودکار سازگار با فضای ابری لیارا ---
BACKUP_DIR = "data/backups"
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

def backup_database():
    try:
        now_str = jdatetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        shutil.copy2("data/football.db", f"{BACKUP_DIR}/football_backup_{now_str}.db")
        print(f"Backup created: {BACKUP_DIR}/football_backup_{now_str}.db")
    except Exception as e:
        print(f"Backup failed: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(backup_database, 'cron', hour=3, minute=0)
scheduler.start()

# --- ساخت خودکار جداول و اصلاح دیتابیس بدون از دست رفتن داده‌ها ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.Base.metadata.create_all(bind=db.engine)
    # تزریق امن ستون جدید به دیتابیس موجود روی سرور
    with db.engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN previous_rank INTEGER DEFAULT 1;"))
        except Exception:
            pass # اگر ستون از قبل وجود داشته باشد خطایی نمی‌دهد
    yield
# -----------------------------------------------

app = FastAPI(title="سیستم پیش‌بینی فوتبال", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

def get_db():
    db_session = db.SessionLocal()
    try: yield db_session
    finally: db_session.close()

def get_tehran_timestamp(j_date_str, time_str):
    try:
        clean_date = str(j_date_str).split(' ')[0].replace('-', '/')
        y, m, d = map(int, clean_date.split('/'))
        time_parts = str(time_str).split(':')
        dt_jalali = jdatetime.datetime(y, m, d, int(time_parts[0]), int(time_parts[1]), 0)
        tehran_tz = pytz.timezone("Asia/Tehran")
        return tehran_tz.localize(dt_jalali.togregorian()).timestamp()
    except Exception: return None

def log_action(db_session: Session, request: Request, user_name: str, action: str, details: str):
    ip = request.client.host if request.client else "Unknown"
    ua = request.headers.get("user-agent", "Unknown")
    now_str = jdatetime.datetime.now().strftime("%Y/%m/%d - %H:%M:%S")
    new_log = db.AuditLog(user_name=user_name, action=action, details=details, ip_address=ip, user_agent=ua, timestamp=now_str)
    db_session.add(new_log)
    db_session.commit()

class BulkDeleteRequest(BaseModel): match_ids: List[int]
class MatchResultItem(BaseModel): match_id: int; actual_home: int; actual_away: int
class BulkFinishRequest(BaseModel): results: List[MatchResultItem]

@app.get("/")
def home(request: Request): return templates.TemplateResponse(request=request, name="index.html", context={"request": request})

@app.get("/admin")
def admin_page(request: Request): return templates.TemplateResponse(request=request, name="admin.html", context={"request": request})

@app.get("/matches/list")
def get_matches(db_session: Session = Depends(get_db)):
    return db_session.query(db.Match).order_by(db.Match.timestamp).all()

@app.get("/users/list")
def get_users(db_session: Session = Depends(get_db)):
    return db_session.query(db.User).order_by(db.User.id.desc()).all()

@app.get("/admin/logs")
def get_audit_logs(db_session: Session = Depends(get_db)):
    return db_session.query(db.AuditLog).order_by(db.AuditLog.id.desc()).limit(300).all()

# دریافت میزان جایزه کل تنظیم شده
@app.get("/admin/prize")
def get_prize(db_session: Session = Depends(get_db)):
    setting = db_session.query(db.SystemSetting).filter(db.SystemSetting.key == "total_prize").first()
    return {"total_prize": float(setting.value) if setting else 0.0}

# ثبت میزان جایزه جدید توسط ادمین
@app.post("/admin/prize")
def set_prize(total_prize: float, db_session: Session = Depends(get_db)):
    setting = db_session.query(db.SystemSetting).filter(db.SystemSetting.key == "total_prize").first()
    if setting:
        setting.value = str(total_prize)
    else:
        db_session.add(db.SystemSetting(key="total_prize", value=str(total_prize)))
    db_session.commit()
    return {"status": "success"}

@app.get("/leaderboard/")
def get_leaderboard(db_session: Session = Depends(get_db)):
    users = db_session.query(db.User).all()
    finished_matches = db_session.query(db.Match).filter(db.Match.status == "finished").all()
    
    prize_setting = db_session.query(db.SystemSetting).filter(db.SystemSetting.key == "total_prize").first()
    total_prize = float(prize_setting.value) if prize_setting else 0.0

    leaderboard_data = []
   
    for u in users:
        stats = {"exact": 0, "diff": 0, "winner": 0, "wrong": 0, "missed": 0}
        preds = {p.match_id: p for p in db_session.query(db.Prediction).filter(db.Prediction.user_id == u.id).all()}
        for fm in finished_matches:
            if fm.id not in preds: stats["missed"] += 1
            else:
                p = preds[fm.id]
                a_diff = fm.actual_home_goals - fm.actual_away_goals
                p_diff = p.predicted_home_goals - p.predicted_away_goals
                if p.predicted_home_goals == fm.actual_home_goals and p.predicted_away_goals == fm.actual_away_goals: stats["exact"] += 1
                elif p_diff == a_diff: stats["diff"] += 1
                elif (a_diff > 0 and p_diff > 0) or (a_diff < 0 and p_diff < 0): stats["winner"] += 1
                else: stats["wrong"] += 1
        
        prev_rank = getattr(u, 'previous_rank', 1)
        if prev_rank is None: prev_rank = 1

        leaderboard_data.append({
            "id": u.id, "name": u.name, "score": u.score, 
            "previous_rank": prev_rank, "prize": 0.0, "trend": "-", **stats
        })
       
    leaderboard_data.sort(key=lambda x: x["score"], reverse=True)
    
    # تعیین رتبه جدید و مشخص کردن نوع تغییر رتبه (فلش گرافیکی)
    current_rank = 1
    for i, row in enumerate(leaderboard_data):
        if i > 0 and leaderboard_data[i]["score"] < leaderboard_data[i-1]["score"]:
            current_rank = i + 1
        row["rank"] = current_rank
        
        if row["rank"] < row["previous_rank"]: row["trend"] = "up"
        elif row["rank"] > row["previous_rank"]: row["trend"] = "down"
        else: row["trend"] = "stable"

    # پیاده‌سازی منطق و فرمول‌های دقیق تقسیم جوایز کاربران
    if total_prize > 0 and leaderboard_data:
        from collections import defaultdict
        rank_groups = defaultdict(list)
        for row in leaderboard_data:
            rank_groups[row["rank"]].append(row)
        
        first_place_users = rank_groups[1]
        distinct_ranks = sorted(rank_groups.keys())
        second_place_users = rank_groups[distinct_ranks[1]] if len(distinct_ranks) > 1 else []

        # قانون ۲: بیش از یک نفر اول داریم -> تقسیم مساوی بین تمام نفرات اول
        if len(first_place_users) >= 2:
            share = total_prize / len(first_place_users)
            for u in first_place_users:
                u["prize"] = round(share, 2)
        
        elif len(first_place_users) == 1:
            u1 = first_place_users[0]
            
            # قانون ۱: دقیقاً یک نفر اول و یک نفر دوم داریم -> تقسیم به نسبت امتیازات کل آن‌ها
            if len(second_place_users) == 1:
                u2 = second_place_users[0]
                total_pts = u1["score"] + u2["score"]
                if total_pts > 0:
                    u1["prize"] = round((u1["score"] / total_pts) * total_prize, 2)
                    u2["prize"] = round((u2["score"] / total_pts) * total_prize, 2)
                else:
                    u1["prize"] = u2["prize"] = round(total_prize / 2, 2)
            
            # قانون ۳: یک نفر اول و چند نفر دوم هم‌امتیاز داریم -> ۵۵٪ نفر اول و ۴۵٪ تقسیم مساوی بین نفرات دوم
            elif len(second_place_users) >= 2:
                u1["prize"] = round(0.55 * total_prize, 2)
                share_45 = (0.45 * total_prize) / len(second_place_users)
                for u in second_place_users:
                    u["prize"] = round(share_45, 2)
            else:
                u1["prize"] = total_prize

    return leaderboard_data

@app.get("/predictions/all")
def get_all_predictions(db_session: Session = Depends(get_db)):
    preds = db_session.query(db.Prediction).all()
    users = {u.id: u.name for u in db_session.query(db.User).all()}
    result = {}
    for p in preds:
        if p.match_id not in result: result[p.match_id] = []
        if p.user_id in users: result[p.match_id].append({"user_name": users[p.user_id], "home": p.predicted_home_goals, "away": p.predicted_away_goals})
    return result

@app.get("/predictions/user/{user_id}")
def get_user_predictions(user_id: int, db_session: Session = Depends(get_db)):
    return db_session.query(db.Prediction).filter(db.Prediction.user_id == user_id).all()

@app.post("/users/")
def create_user(request: Request, name: str, password: str, db_session: Session = Depends(get_db)):
    if db_session.query(db.User).filter(db.User.name == name).first(): raise HTTPException(status_code=400, detail="این نام کاربری قبلاً ثبت شده است")
    now_str = jdatetime.datetime.now().strftime("%Y/%m/%d - %H:%M")
    new_user = db.User(name=name, password=password, last_login=now_str)
    db_session.add(new_user)
    db_session.commit()
    log_action(db_session, request, name, "ثبت‌نام", "کاربر جدید ثبت‌نام کرد")
    return {"status": "success", "user_id": new_user.id, "name": new_user.name}

@app.post("/login/")
def login_user(request: Request, name: str, password: str, db_session: Session = Depends(get_db)):
    if name == "admin" and password == "manhastam":
        log_action(db_session, request, "مدیریت", "ورود ادمین", "ورود به پنل مدیریت")
        return {"status": "success", "user_id": 0, "name": "مدیریت", "is_admin": True}
       
    user = db_session.query(db.User).filter(db.User.name == name).first()
    if not user or user.password != password: raise HTTPException(status_code=400, detail="نام کاربری یا رمز عبور اشتباه است")
   
    user.last_login = jdatetime.datetime.now().strftime("%Y/%m/%d - %H:%M")
    db_session.commit()
    log_action(db_session, request, user.name, "ورود کاربر", "ورود موفق به سیستم")
   
    return {"status": "success", "user_id": user.id, "name": user.name, "is_admin": False}

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
                d, t = str(row[2]).strip() if len(row)>2 and row[2] else "نامشخص", str(row[3]).strip() if len(row)>3 and row[3] else "نامشخص"
                s = str(row[4]).strip() if len(row)>4 and row[4] else "نامشخص"
                g = str(row[5]).strip() if len(row)>5 and row[5] else "نامشخص"
                ts = get_tehran_timestamp(d, t)
                new_match = db.Match(home_team=h, away_team=a, match_date=d, match_time=t, stadium=s, group_name=g, timestamp=ts)
                db_session.add(new_match)
                added += 1
        db_session.commit()
        return {"status": "success", "message": f"{added} مسابقه اضافه شد"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

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

@app.post("/predictions/")
def create_prediction(request: Request, user_id: int, match_id: int, home_goals: int, away_goals: int, db_session: Session = Depends(get_db)):
    match = db_session.query(db.Match).filter(db.Match.id == match_id).first()
    if not match or match.status != "upcoming": raise HTTPException(status_code=400, detail="مسابقه یافت نشد")
    if not match.timestamp: raise HTTPException(status_code=400, detail="تاریخ نامعتبر است.")
    if datetime.now(pytz.timezone("Asia/Tehran")).timestamp() >= (match.timestamp - 900): raise HTTPException(status_code=400, detail="مهلت ثبت پیش‌بینی برای این مسابقه تمام شده است")

    user = db_session.query(db.User).filter(db.User.id == user_id).first()
    user_name = user.name if user else "Unknown"

    pred = db_session.query(db.Prediction).filter(db.Prediction.user_id == user_id, db.Prediction.match_id == match_id).first()
    if pred:
        pred.predicted_home_goals = home_goals
        pred.predicted_away_goals = away_goals
        action = "ویرایش پیش‌بینی"
    else:
        db_session.add(db.Prediction(user_id=user_id, match_id=match_id, predicted_home_goals=home_goals, predicted_away_goals=away_goals))
        action = "ثبت پیش‌بینی جدید"
        
    db_session.commit()
    
    details = f"بازی: {match.home_team} و {match.away_team} | نتیجه ثبت شده: {home_goals} - {away_goals}"
    log_action(db_session, request, user_name, action, details)
    
    return {"status": "success"}

@app.post("/matches/bulk-finish")
def bulk_finish_matches(req: BulkFinishRequest, db_session: Session = Depends(get_db)):
    # ثبت رتبه‌های فعلی کاربران به عنوان رتبه قبلی قبل از بروزرسانی نهایی نتایج بازی جدید
    all_users = db_session.query(db.User).all()
    sorted_by_current = sorted(all_users, key=lambda x: x.score, reverse=True)
    
    current_rank = 1
    for i, u in enumerate(sorted_by_current):
        if i > 0 and sorted_by_current[i].score < sorted_by_current[i-1].score:
            current_rank = i + 1
        u.previous_rank = current_rank
    db_session.commit()

    for item in req.results:
        match = db_session.query(db.Match).filter(db.Match.id == item.match_id).first()
        if match:
            match.actual_home_goals = item.actual_home; match.actual_away_goals = item.actual_away; match.status = "finished"
    db_session.commit()
    
    users = db_session.query(db.User).all()
    for u in users: u.score = 0
    db_session.commit()

    finished_matches = db_session.query(db.Match).filter(db.Match.status == "finished").all()
    for fm in finished_matches:
        actual_diff = fm.actual_home_goals - fm.actual_away_goals
        predictions = db_session.query(db.Prediction).filter(db.Prediction.match_id == fm.id).all()
        for pred in predictions:
            user = db_session.query(db.User).filter(db.User.id == pred.user_id).first()
            if not user: continue
            pred_diff = pred.predicted_home_goals - pred.predicted_away_goals
            if pred.predicted_home_goals == fm.actual_home_goals and pred.predicted_away_goals == fm.actual_away_goals: user.score += 3
            elif pred_diff == actual_diff: user.score += 2
            elif (actual_diff > 0 and pred_diff > 0) or (actual_diff < 0 and pred_diff < 0): user.score += 1
    db_session.commit()
    return {"status": "success"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
