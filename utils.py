# نام فایل: utils.py

import datetime
from sqlalchemy.orm import Session
# ایمپورت کردن مدل‌ها و تنظیمات دیتابیس شما
from database import Match, Prediction, User 

def generate_bale_summary_message(session: Session, finished_match_id: int) -> str:
    # ... (دقیقاً همان کدهای طولانی که در پیام قبلی برای این تابع نوشتم را اینجا قرار دهید) ...
