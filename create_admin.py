from database import SessionLocal
import models
from security import hash_password

def create_super_admin():
    db = SessionLocal()
    admin_email = "admin@example.com"
    existing = db.query(models.User).filter(models.User.email == admin_email).first()
    if not existing:
        admin = models.User(
            name="Super Admin",
            email=admin_email,
            password=hash_password("password123"),
            type="admin"
        )
        db.add(admin)
        db.commit()
        print("Super admin created successfully.")
    else:
        existing.password = hash_password("password123")
        existing.type = "admin"
        db.commit()
        print("Super admin updated successfully.")
    db.close()

if __name__ == "__main__":
    create_super_admin()

