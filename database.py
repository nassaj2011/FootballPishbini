import os
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Float
from sqlalchemy.orm import declarative_base, sessionmaker

# 🎯 قدم اول: دیکشنری تبدیل نام کاربری به نام محترمانه و تابع مبدل
USER_MAPPING = {
    USER_MAPPING = {
    "Hadi": "آقا هادی لطفی",
    "AmirAKS9": "امیر آقا",
    "Nima": "آقا نیما",
    "naser": "آقا ناصر",
    "gemany": "آقا ساجد",
    "Sana": "آقا سعید",
    "Hamid": "آقا حمید",
    "alisaj": "علی آقا سجادی",
    "Alims": "علی آقا متولیان",
    "مسعود": "مسعود آقا",
    
    # 🌟 تمام حالت‌های احتمالی برای اکانت آقا نادر اضافه شد
    "ایران_رویایی": "آقا نادر",
    "ایران رویایی": "آقا نادر",
    "ایران رويایی": "آقا نادر",  # با یِ عربی
    "ایران رويايي": "آقا نادر",  # با یِ عربی دوبل
    
    "Hadisajadi": "هادی آقا متولیان",
    "Amir_Rainbow": "امیر آقا عباسی"
}
}

def get_persian_name(username):
    return USER_MAPPING.get(username, username)

# ==========================================
# تنظیمات اصلی دیتابیس
# ==========================================

if not os.path.exists("data"):
    os.makedirs("data")

SQLALCHEMY_DATABASE_URL = "sqlite:///./data/football.db"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    username = Column(String, unique=True, index=True) # ستون جدید یوزرنیم نمایش
    password = Column(String)
    score = Column(Integer, default=0)
    last_login = Column(String, default="هرگز")
    previous_rank = Column(Integer, default=1)

class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True, index=True)
    home_team = Column(String)
    away_team = Column(String)
    match_date = Column(String)
    match_time = Column(String)
    stadium = Column(String, default="نامشخص")
    group_name = Column(String, default="نامشخص")
    timestamp = Column(Float, nullable=True)
    status = Column(String, default="upcoming")
    actual_home_goals = Column(Integer, nullable=True)
    actual_away_goals = Column(Integer, nullable=True)

class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    match_id = Column(Integer, ForeignKey("matches.id"))
    predicted_home_goals = Column(Integer)
    predicted_away_goals = Column(Integer)
    submit_time = Column(String, default="نامشخص") # 🌟 این خط جا مانده بود!

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String)
    action = Column(String)
    details = Column(String)
    ip_address = Column(String)
    user_agent = Column(String)
    timestamp = Column(String)

class SystemSetting(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)
    value = Column(String)

