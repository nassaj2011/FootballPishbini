from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Float
from sqlalchemy.orm import declarative_base, sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./football.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    password = Column(String)
    score = Column(Integer, default=0)
    last_login = Column(String, default="هرگز")

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

# جدول جدید برای ثبت فعالیت‌های سیستم (Audit Log)
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_name = Column(String)
    action = Column(String)
    details = Column(String)
    ip_address = Column(String)
    user_agent = Column(String)
    timestamp = Column(String)