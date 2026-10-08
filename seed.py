import json
from database import SessionLocal, engine, sync_columns
import models
from security import hash_password

def seed():
    db = SessionLocal()
    with open("fake_data.json", "r") as f:
        data = json.load(f)
        
    # Seed Users
    for u in data.get("users", []):
        if not db.query(models.User).filter_by(email=u["email"]).first():
            user = models.User(**{**u, "password": hash_password(u["password"])})
            db.add(user)
            
    # Seed Categories
    for c in data.get("categories", []):
        if not db.query(models.Category).filter_by(name=c["name"]).first():
            cat = models.Category(**c)
            db.add(cat)
    db.commit()
    
    # Seed Products
    for p in data.get("products", []):
        if not db.query(models.Product).filter_by(name=p["name"]).first():
            prod = models.Product(**p)
            db.add(prod)
            
    db.commit()
    print("Fake data successfully injected into the database!")
    db.close()

if __name__ == "__main__":
    models.Base.metadata.create_all(bind=engine)
    sync_columns()
    seed()

