"""Tables. Field names follow what the React frontend (medivila-web) sends and reads."""
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class Common:
    """Columns shared by every table.

    `extra` keeps any payload fields the frontend sends that have no dedicated
    column, so nothing the client submits is silently dropped.
    """
    id = Column(Integer, primary_key=True, index=True)
    extra = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------- users ----------

class User(Common, Base):
    __tablename__ = "users"
    name = Column(String(255))
    email = Column(String(255), unique=True, index=True, nullable=True)
    phone = Column(String(50), unique=True, index=True, nullable=True)
    password = Column(String(255), nullable=True)
    type = Column(String(50), default="user")
    remember_token = Column(String(100), unique=True, index=True, nullable=True)
    points = Column(Integer, default=0)


# ---------- catalogue ----------

class Category(Common, Base):
    __tablename__ = "categories"
    name = Column(String(255))
    slug = Column(String(255), unique=True, index=True, nullable=True)
    image = Column(String(255), nullable=True)
    sort_order = Column(Integer, default=0)
    product_limit = Column(Integer, default=0)
    parent_id = Column(Integer, nullable=True)
    show_on_homepage = Column(Integer, default=0)
    products = relationship("Product", back_populates="category")


class Product(Common, Base):
    __tablename__ = "products"
    name = Column(String(255), index=True)
    slug = Column(String(255), unique=True, index=True, nullable=True)
    price = Column(Float)
    discount_price = Column(Float, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    clothing = Column(Integer, default=0)
    images = Column(JSON, nullable=True)                # [{"image": "images/x.png"}]
    single_product_sizes = Column(JSON, nullable=True)  # [{"id": 1, "size": "M"}]
    bulk_discounts = Column(JSON, nullable=True)        # [{"id", "title", "offer_quantity", "discount_percentage"}]
    bumps = Column(JSON, nullable=True)                 # [{"id", "title", "bump_price", "description", "image"}]
    homepage = Column(JSON, nullable=True)              # {"headline", "paragraph", "description"}
    is_wholesale = Column(Integer, default=0)           # listed on the Wholesaler page
    wholesale_price = Column(Float, nullable=True)
    wholesale_min_qty = Column(Integer, nullable=True)
    category = relationship("Category", back_populates="products")


class Color(Common, Base):
    """A product's colour variant (the dashboard's "Color" / "live" screens edit these rows)."""
    __tablename__ = "colors"
    product_id = Column(Integer, index=True, nullable=True)
    color = Column(String(100))
    image = Column(String(255), nullable=True)


class Size(Common, Base):
    """A size available for one colour of a product."""
    __tablename__ = "sizes"
    product_id = Column(Integer, index=True, nullable=True)
    color_id = Column(Integer, index=True, nullable=True)
    size = Column(String(50))


class LabTest(Common, Base):
    __tablename__ = "lab_tests"
    name = Column(String(255))
    description = Column(Text)
    icon = Column(String(255))
    original_price = Column(Float)
    discounted_price = Column(Float)
    discount = Column(String(50))
    report_time = Column(String(100))
    booking_count = Column(String(50))
    rating = Column(Float)
    image = Column(String(255))
    labs = Column(JSON, nullable=True)  # [{"id", "name", "original_price", "discounted_price", "rating", "is_recommended", "logo"}]
    sections = Column(JSON, nullable=True)      # Home Lab page rows: ["trending", "packages", "most_booked"]
    category_ids = Column(JSON, nullable=True)  # LabCategory ids (vital organs / health concerns)
    sort_order = Column(Integer, default=0)
    is_active = Column(Integer, default=1)


class LabCategory(Common, Base):
    """A Home Lab page tile: a vital organ or a health concern that groups lab tests."""
    __tablename__ = "lab_categories"
    name = Column(String(255))
    icon = Column(String(50), nullable=True)   # emoji shown in the tile
    image = Column(String(255), nullable=True)  # optional picture instead of the emoji
    type = Column(String(20), default="organ")  # "organ" | "concern"
    sort_order = Column(Integer, default=0)
    is_active = Column(Integer, default=1)


class LabPartner(Common, Base):
    __tablename__ = "lab_partners"
    name = Column(String(255))
    icon = Column(String(50), nullable=True)
    logo = Column(String(255), nullable=True)
    sort_order = Column(Integer, default=0)
    is_active = Column(Integer, default=1)


# ---------- orders ----------

class Order(Common, Base):
    __tablename__ = "orders"
    order_id = Column(String(100), unique=True, index=True)
    order_type = Column(String(50), default="product")
    user_id = Column(Integer, nullable=True, index=True)
    customer_name = Column(String(255))
    phone_number = Column(String(50), index=True)
    customer_address = Column(Text)
    product_name = Column(Text, nullable=True)
    color = Column(String(100), nullable=True)
    size = Column(String(50), nullable=True)
    quantity = Column(Integer, default=1)
    delivery_charge = Column(Float, default=0.0)
    cod_advance = Column(Float, default=0.0)
    product_price = Column(Float, default=0.0)
    subtotal = Column(Float, nullable=True)
    lab_tests_total = Column(Float, nullable=True)
    regular_products_total = Column(Float, nullable=True)
    total = Column(Float)
    transaction_id = Column(String(255), nullable=True)
    coins_used = Column(Integer, default=0)
    coin_discount = Column(Float, default=0.0)
    points = Column(Integer, default=0)
    payment_method = Column(String(50))
    payment_number = Column(String(50), nullable=True)
    delivery_note = Column(Text, nullable=True)
    delivery_status = Column(String(50), default="New Order")
    lab_test_name = Column(Text, nullable=True)
    lab_name = Column(String(255), nullable=True)
    patient_count = Column(Integer, nullable=True)
    report_time = Column(String(100), nullable=True)
    lab_test_image = Column(String(255), nullable=True)
    has_lab_tests = Column(Integer, default=0)
    has_regular_products = Column(Integer, default=1)
    consignment_id = Column(String(100), nullable=True)
    tracking_code = Column(String(100), nullable=True)
    items = Column(JSON, nullable=True)
    bulk_discounts = Column(JSON, nullable=True)
    bumps = Column(JSON, nullable=True)


class Lead(Common, Base):
    __tablename__ = "leads"
    order_id = Column(String(100), index=True)
    customer_name = Column(String(255))
    phone_number = Column(String(50))
    product_price = Column(Float, nullable=True)
    customer_address = Column(Text, nullable=True)
    status = Column(String(50), default="lead")
    quantity = Column(Integer, default=1)
    product_name = Column(Text, nullable=True)
    page_url = Column(Text, nullable=True)


class Transaction(Common, Base):
    """Advance-payment record created when an order carries a transaction id."""
    __tablename__ = "transactions"
    order_id = Column(String(100), nullable=True)
    tnxId = Column(String(255), nullable=True)
    tnxNumber = Column(String(50), nullable=True)
    amount = Column(Float, default=0.0)
    payment_method = Column(String(50), nullable=True)
    status = Column(String(50), nullable=True)


class Prescription(Common, Base):
    """An uploaded prescription. The file is private (see routers/care.py) and only reachable via signed URLs."""
    __tablename__ = "prescriptions"
    name = Column(String(255))
    phone = Column(String(50))
    notes = Column(Text, nullable=True)
    file = Column(String(255))
    file_type = Column(String(10), nullable=True)       # "image" | "pdf"
    original_name = Column(String(255), nullable=True)
    status = Column(String(20), default="pending")       # pending | contacted | completed | cancelled
    admin_note = Column(Text, nullable=True)


class AppointmentType(Common, Base):
    __tablename__ = "appointment_types"
    name = Column(String(255))
    icon = Column(String(50), nullable=True)
    fee = Column(Float, default=0.0)
    duration = Column(String(50), nullable=True)  # e.g. "15 mins"
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    is_active = Column(Integer, default=1)


class Appointment(Common, Base):
    __tablename__ = "appointments"
    appointment_no = Column(String(20), unique=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    appointment_type_id = Column(Integer, nullable=True)
    type_name = Column(String(255))           # copied at booking time so later edits don't rewrite history
    fee = Column(Float, default=0.0)
    date = Column(String(10), index=True)     # YYYY-MM-DD
    time = Column(String(20))                 # e.g. "10:30 AM"
    patient_name = Column(String(255))
    mobile = Column(String(50), index=True)
    age = Column(Integer, nullable=True)
    gender = Column(String(10), nullable=True)
    issue = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    status = Column(String(20), default="pending")  # pending | confirmed | completed | cancelled
    admin_note = Column(Text, nullable=True)


# ---------- coins ----------

class BonusCoin(Common, Base):
    __tablename__ = "bonus_coins"
    first_reg_bonus = Column(Integer, default=0)
    bonus_percentage = Column(Float, default=0.0)
    coin_name = Column(String(100), nullable=True)
    name = Column(String(100), nullable=True)


class CoinPackage(Common, Base):
    __tablename__ = "coin_packages"
    name = Column(String(100))
    coins = Column(Integer, default=0)
    price = Column(Float, default=0.0)
    is_active = Column(Integer, default=0)


class CoinTransaction(Common, Base):
    """A customer's request to buy a coin package; an admin approves it (status 1)."""
    __tablename__ = "coin_transactions"
    user_id = Column(Integer, nullable=True, index=True)
    coin_package_id = Column(Integer, nullable=True)
    name = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True, index=True)
    wallet_number = Column(String(50), nullable=True)
    status = Column(Integer, default=0)


class CodAdvance(Common, Base):
    __tablename__ = "cod_advances"
    title = Column(String(255), nullable=True)
    sub_title = Column(String(255), nullable=True)
    headline = Column(Text, nullable=True)
    pay_amount = Column(Float, default=0.0)
    is_active = Column(Integer, default=1)


# ---------- site content ----------

class Homepage(Common, Base):
    __tablename__ = "homepages"
    headline = Column(Text, nullable=True)
    paragraph = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    slug = Column(String(255), nullable=True)


class Menu(Common, Base):
    __tablename__ = "menus"
    name = Column(String(255))
    category_id = Column(Integer, nullable=True)
    menu_type = Column(String(50), default="header")
    order = Column(Integer, default=0)
    url = Column(String(255), nullable=True)


class Banner(Common, Base):
    __tablename__ = "banners"
    title = Column(String(255))
    description = Column(Text, nullable=True)
    image = Column(String(255))
    link = Column(String(255), nullable=True)
    is_active = Column(Integer, default=1)
    sort_order = Column(Integer, default=0)


class WebsiteLogo(Common, Base):
    __tablename__ = "website_logos"
    logo = Column(String(255), nullable=True)
    brand_slogan = Column(String(255), nullable=True)


class ProductPage(Common, Base):
    __tablename__ = "product_pages"
    headline = Column(Text, nullable=True)
    paragraph = Column(Text, nullable=True)


class LegalPage(Common, Base):
    __tablename__ = "legal_pages"
    title = Column(String(255))
    slug = Column(String(255), unique=True, index=True, nullable=True)
    content = Column(Text)


class Congratulation(Common, Base):
    __tablename__ = "congratulations"
    headline = Column(Text, nullable=True)
    paragraph = Column(Text, nullable=True)


class Community(Common, Base):
    __tablename__ = "communities"
    icon = Column(String(100), nullable=True)
    name = Column(String(255))
    description = Column(Text, nullable=True)
    image = Column(String(255), nullable=True)
    banner = Column(String(255), nullable=True)
    button_text = Column(String(100), nullable=True)
    url = Column(String(255), nullable=True)


class Offer(Common, Base):
    __tablename__ = "offers"
    title = Column(String(255))
    discount = Column(String(100), nullable=True)
    subtext = Column(Text, nullable=True)
    button_text = Column(String(100), nullable=True)
    button_link = Column(String(255), nullable=True)
    card_color = Column(String(50), nullable=True)
    image = Column(String(255), nullable=True)
    sort_order = Column(Integer, default=9999)


class PageContent(Common, Base):
    """Editable text/blocks of a static storefront page (e.g. "home-lab", "wholesaler")."""
    __tablename__ = "page_contents"
    page = Column(String(100), unique=True, index=True)
    content = Column(JSON, nullable=True)


class WholesaleApplication(Common, Base):
    """A wholesale-account or bulk-quote request from the Wholesaler page."""
    __tablename__ = "wholesale_applications"
    request_type = Column(String(20), default="account")  # "account" | "quote"
    business_name = Column(String(255), nullable=True)
    owner_name = Column(String(255))
    phone = Column(String(50))
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    trade_license = Column(String(100), nullable=True)
    message = Column(Text, nullable=True)
    status = Column(String(20), default="pending")  # "pending" | "approved" | "rejected"


class SiteSetting(Common, Base):
    __tablename__ = "site_settings"
    header_script = Column(Text, nullable=True)
    footer_script = Column(Text, nullable=True)


# ---------- integrations / misc ----------

class DeliveryCharge(Common, Base):
    __tablename__ = "delivery_charges"
    district_name = Column(String(100), nullable=True)
    delivery_charge = Column(Float, default=0.0)
    delivery_note = Column(Text, nullable=True)
    estimated_days = Column(String(50), nullable=True)


class PaymentMethod(Common, Base):
    __tablename__ = "payment_methods"
    payment_method = Column(String(100))
    payment_number = Column(String(50), nullable=True)
    image = Column(String(255), nullable=True)


class Steadfast(Common, Base):
    __tablename__ = "steadfasts"
    apiKey = Column(String(255), nullable=True)
    secretKey = Column(String(255), nullable=True)


class Pixel(Common, Base):
    __tablename__ = "pixels"
    pixel_id = Column(String(100))


class SocialLink(Common, Base):
    __tablename__ = "social_links"
    name = Column(String(100), nullable=True)
    url = Column(String(255), nullable=True)
    icon_class = Column(String(100), nullable=True)
    status = Column(Integer, default=1)


class Contact(Common, Base):
    __tablename__ = "contacts"
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    subject = Column(String(255), nullable=True)
    message = Column(Text)
    status = Column(String(50), nullable=True)


class ContactInfo(Common, Base):
    __tablename__ = "contact_infos"
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    tnx_number = Column(String(50), nullable=True)
