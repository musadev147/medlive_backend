"""Copy the public catalogue of the live site (api.medivila.com) into this database.

Imports categories, products (images, colours/sizes, discounts, bumps, landing text), banners, offers,
lab tests, menus, legal pages, delivery charges and the site settings. Images are downloaded into
uploads/ so nothing depends on the live server afterwards. Customers, orders and other private data
are never touched.

Safe to run again: rows that already exist (same slug / name) are skipped, images are not downloaded twice.

    python import_live_data.py                  # import everything
    python import_live_data.py --dry-run        # only show what would be imported
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import models
from database import Base, SessionLocal, engine, sync_columns
from utils import UPLOAD_DIR, unique_slug

LIVE_API = os.getenv("LIVE_API", "https://api.medivila.com/api")
LIVE_HOST = LIVE_API.rsplit("/api", 1)[0]
IMAGE_DIR = "images/live"


def fetch(path):
    req = urllib.request.Request(f"{LIVE_API}/{path}", headers={"User-Agent": "medivila-import/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def as_list(data, *keys):
    if isinstance(data, list):
        return data
    for key in keys:
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return []


def num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------- images ----------
def image_path(url):
    """Local relative path for a live image URL (stable, so re-runs reuse the file)."""
    if not url or not isinstance(url, str):
        return None
    if not url.startswith("http"):
        url = f"{LIVE_HOST}/storage/{url.lstrip('/')}"
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if ext not in (".webp", ".png", ".jpg", ".jpeg", ".gif"):
        ext = ".jpg"
    return f"{IMAGE_DIR}/{hashlib.sha1(url.encode()).hexdigest()[:20]}{ext}", url


def download_all(urls):
    os.makedirs(os.path.join(UPLOAD_DIR, IMAGE_DIR), exist_ok=True)
    jobs = {}
    for url in urls:
        mapped = image_path(url)
        if mapped:
            jobs[mapped[0]] = mapped[1]
    todo = {p: u for p, u in jobs.items() if not os.path.exists(os.path.join(UPLOAD_DIR, p))}
    failed = []

    def get(item):
        path, url = item
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "medivila-import/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            with open(os.path.join(UPLOAD_DIR, path), "wb") as out:
                out.write(data)
        except Exception as exc:  # keep going; the row just shows the fallback image
            failed.append((url, str(exc)))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(get, todo.items()))
    print(f"images: {len(jobs)} referenced, {len(todo)} downloaded, {len(failed)} failed")
    for url, err in failed[:10]:
        print("  failed:", url, err)
    return {u: p for p, u in jobs.items()}


def local(url_map, url):
    mapped = image_path(url)
    return mapped[0] if mapped and mapped[1] in url_map else None


def safe_link(link):
    """Keep only normal links. Drops the odd *.edgeone.app config links found on the live banners."""
    link = str(link or "").strip()
    tel = re.search(r'href="(tel:[^"]+)"', link)
    if tel:
        return tel.group(1)
    if "edgeone.app" in link or link.startswith("<"):
        return ""
    return link


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Reading {LIVE_API} ...")
    categories = as_list(fetch("category"), "categories")
    flat_categories = []
    for c in categories:
        flat_categories.append(c)
        flat_categories.extend(c.get("children") or [])
    products = as_list(fetch("products"), "data", "products")
    banners = as_list(fetch("banners"))
    offers = as_list(fetch("offers"))
    lab_tests = as_list(fetch("lab-tests"), "data")
    menus = as_list(fetch("menus"))
    legal_pages = as_list(fetch("legalpages"))
    delivery = as_list(fetch("deliverycharges"))
    socials = as_list(fetch("sociallinks"))
    contact_infos = as_list(fetch("contactinfos"))
    logos = as_list(fetch("websitelogos"))
    bonus = as_list(fetch("bonuscoins"))
    print(f"live: {len(flat_categories)} categories, {len(products)} products, {len(banners)} banners, "
          f"{len(offers)} offers, {len(lab_tests)} lab tests, {len(menus)} menus, {len(legal_pages)} legal pages")
    if args.dry_run:
        return

    urls = [c.get("image") for c in flat_categories]
    for p in products:
        urls += [i.get("image") for i in p.get("images") or []]
        urls += [c.get("image") for c in p.get("colors") or []]
        urls += [b.get("image") for b in p.get("bumps") or []]
    urls += [b.get("image") for b in banners] + [o.get("image") for o in offers] + [l.get("logo") for l in logos]
    for t in lab_tests:
        urls.append(t.get("image"))
        urls += [lab.get("logo") for lab in t.get("labs") or []]
    url_map = download_all([u for u in urls if u])

    Base.metadata.create_all(bind=engine)
    sync_columns()
    db = SessionLocal()
    added = {}

    def count(name):
        added[name] = added.get(name, 0) + 1

    # categories (parents first so children can point at them)
    category_ids = {}
    for c in sorted(flat_categories, key=lambda c: c.get("parent_id") is not None):
        existing = db.query(models.Category).filter(models.Category.slug == c.get("slug")).first()
        if existing:
            category_ids[c["id"]] = existing.id
            continue
        row = models.Category(
            name=c["name"], slug=unique_slug(db, models.Category, c.get("slug") or c["name"]),
            image=local(url_map, c.get("image")), sort_order=int(num(c.get("sort_order"))),
            product_limit=int(num(c.get("product_limit"), 50)),
            parent_id=category_ids.get(c.get("parent_id")), show_on_homepage=int(num(c.get("show_on_homepage"))),
        )
        db.add(row)
        db.flush()
        category_ids[c["id"]] = row.id
        count("categories")

    # products
    for p in products:
        if db.query(models.Product).filter(models.Product.slug == p.get("slug")).first():
            continue
        homepage = p.get("homepage") or None
        if isinstance(homepage, dict):
            homepage = {k: homepage.get(k) for k in ("headline", "paragraph", "description")}
        bumps = []
        for i, b in enumerate(p.get("bumps") or [], start=1):
            bumps.append({"id": i, "title": b.get("title"), "bump_price": b.get("bump_price"),
                          "description": b.get("description"), "image": local(url_map, b.get("image"))})
        row = models.Product(
            name=p["name"], slug=unique_slug(db, models.Product, p.get("slug") or p["name"]),
            price=num(p.get("price")), discount_price=num(p.get("discount_price"), None) or None,
            category_id=category_ids.get(p.get("category_id")), clothing=int(num(p.get("clothing"))),
            images=[{"image": path} for path in (local(url_map, i.get("image")) for i in p.get("images") or []) if path],
            single_product_sizes=[{"id": i, "size": s.get("size")} for i, s in
                                  enumerate(p.get("single_product_sizes") or [], start=1) if s.get("size")],
            bulk_discounts=[{"id": i, "title": d.get("title"), "offer_quantity": d.get("offer_quantity"),
                             "discount_percentage": d.get("discount_percentage")}
                            for i, d in enumerate(p.get("bulk_discounts") or [], start=1)],
            bumps=bumps, homepage=homepage,
        )
        db.add(row)
        db.flush()
        for c in p.get("colors") or []:
            color = models.Color(product_id=row.id, color=c.get("color"), image=local(url_map, c.get("image")))
            db.add(color)
            db.flush()
            for s in c.get("sizes") or []:
                if s.get("size"):
                    db.add(models.Size(product_id=row.id, color_id=color.id, size=s["size"]))
        count("products")

    # banners & offers
    for b in banners:
        if db.query(models.Banner).filter(models.Banner.title == b.get("title")).first():
            continue
        db.add(models.Banner(title=b.get("title"), description=b.get("description"), image=local(url_map, b.get("image")),
                             link=safe_link(b.get("link")), is_active=int(num(b.get("is_active"), 1)),
                             sort_order=int(num(b.get("sort_order")))))
        count("banners")
    for o in offers:
        if db.query(models.Offer).filter(models.Offer.title == o.get("title")).first():
            continue
        row = models.Offer(title=o.get("title"), discount=o.get("discount"), subtext=o.get("subtext"),
                           button_text=o.get("button_text"), button_link=safe_link(o.get("button_link")),
                           card_color=o.get("card_color"), image=local(url_map, o.get("image")),
                           sort_order=int(num(o.get("sort_order"), 9999)))
        row.extra = {k: o.get(k) for k in ("top_text", "main_text", "support_text", "icon", "gradient", "icon_bg")
                     if o.get(k) is not None} or None
        db.add(row)
        count("offers")

    # lab tests
    for t in lab_tests:
        if db.query(models.LabTest).filter(models.LabTest.name == t.get("name")).first():
            continue
        labs = []
        for i, lab in enumerate(t.get("labs") or [], start=1):
            labs.append({"id": i, "name": lab.get("name"), "original_price": num(lab.get("original_price")),
                         "discounted_price": num(lab.get("discounted_price")), "rating": num(lab.get("rating")),
                         "is_recommended": 1 if lab.get("is_recommended") in (1, True, "1") else 0,
                         "logo": local(url_map, lab.get("logo"))})
        db.add(models.LabTest(
            name=t.get("name"), description=t.get("description"), icon=t.get("icon") or "MdBloodtype",
            original_price=num(t.get("original_price")), discounted_price=num(t.get("discounted_price")),
            discount=str(t.get("discount") or ""), report_time=t.get("report_time"),
            booking_count=str(t.get("booking_count") or ""), rating=num(t.get("rating")),
            image=local(url_map, t.get("image")), labs=labs, sections=["trending"], is_active=1))
        count("lab tests")

    # menus, legal pages, delivery charges
    for m in menus:
        if db.query(models.Menu).filter(models.Menu.name == m.get("name"), models.Menu.menu_type == m.get("menu_type")).first():
            continue
        db.add(models.Menu(name=m.get("name"), category_id=category_ids.get(m.get("category_id")),
                           menu_type=m.get("menu_type") or "header", order=int(num(m.get("order"))),
                           url=m.get("url") or m.get("link")))
        count("menus")
    for page in legal_pages:
        if db.query(models.LegalPage).filter(models.LegalPage.title == page.get("title")).first():
            continue
        db.add(models.LegalPage(title=page.get("title"), content=page.get("content"),
                                slug=unique_slug(db, models.LegalPage, page.get("slug") or page.get("title"))))
        count("legal pages")
    for d in delivery:
        if db.query(models.DeliveryCharge).filter(models.DeliveryCharge.district_name == d.get("district_name")).first():
            continue
        db.add(models.DeliveryCharge(district_name=d.get("district_name"), delivery_charge=num(d.get("delivery_charge")),
                                     delivery_note=d.get("delivery_note"), estimated_days=d.get("estimated_days")))
        count("delivery charges")
    for s in socials:
        if db.query(models.SocialLink).filter(models.SocialLink.name == s.get("name")).first():
            continue
        db.add(models.SocialLink(name=s.get("name"), url=s.get("url"), icon_class=s.get("icon_class"),
                                 status=int(num(s.get("status"), 1))))
        count("social links")

    # single-row settings: only when nothing is set locally yet
    if contact_infos and not db.query(models.ContactInfo).count():
        c = contact_infos[0]
        db.add(models.ContactInfo(phone=c.get("phone"), email=c.get("email"), address=c.get("address"),
                                  tnx_number=c.get("tnx_number")))
        count("contact info")
    if logos and not db.query(models.WebsiteLogo).count():
        db.add(models.WebsiteLogo(logo=local(url_map, logos[0].get("logo")), brand_slogan=logos[0].get("brand_slogan")))
        count("logo")
    if bonus and not db.query(models.BonusCoin).count():
        b = bonus[0]
        db.add(models.BonusCoin(first_reg_bonus=int(num(b.get("first_reg_bonus"))),
                                bonus_percentage=num(b.get("bonus_percentage")), coin_name=b.get("coin_name")))
        count("coin bonus")

    db.commit()
    db.close()
    print("added:", ", ".join(f"{v} {k}" for k, v in added.items()) or "nothing new")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
