from database import Base, engine

print("در حال ساخت جداول دیتابیس...")

# این دستور تمام جدول‌هایی که تعریف کردیم را در دیتابیس می‌سازد
Base.metadata.create_all(bind=engine)

print("تبریک! دیتابیس و جداول با موفقیت و بدون خطا ساخته شدند.")