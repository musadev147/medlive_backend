from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import models
from database import get_db
from security import get_current_admin
from utils import FILE, form_body, json_body, public_url, read_payload, register_crud, serialize, unique_slug

router = APIRouter()

BANNER_FIELDS = {
    "title": "string",
    "description": "string",
    "image": FILE,
    "link": "string",
    "is_active": {"type": "integer", "enum": [0, 1]},
    "sort_order": "integer",
}
LOGO_FIELDS = {"image": FILE, "brand_slogan": "string"}
COMMUNITY_FIELDS = {"icon": "string", "name": "string", "description": "string", "image": FILE, "banner": FILE,
                    "button_text": "string", "url": "string"}
OFFER_FIELDS = {"title": "string", "discount": "string", "subtext": "string", "button_text": "string",
                "button_link": "string", "card_color": "string", "image": FILE, "sort_order": "integer"}


# All lists are public (the storefront renders them); writes need an admin token.
register_crud(router, models.Homepage, label="Homepage", list_key="homePages",
              list_path="/homepages", create_path="/homepages",
              update_paths=["/homepagesupdate/{id}"], delete_paths=["/homepagedelete/{id}"],
              create_body=json_body({"headline": "...", "paragraph": "...", "description": "...", "slug": "..."}))
register_crud(router, models.Menu, label="Menu", list_path="/menus", create_path="/menus",
              update_paths=["/menusupdate/{id}"], delete_paths=["/menudelete/{id}"],
              order_by=[models.Menu.order, models.Menu.id],
              create_body=json_body({"name": "Medicine", "category_id": 1, "menu_type": "header", "order": 1}))
register_crud(router, models.Banner, label="Banner", list_path="/banners", create_path="/banners",
              update_paths=["/bannersupdate/{id}"], delete_paths=["/bannerdelete/{id}"],
              order_by=[models.Banner.sort_order, models.Banner.id],
              create_body=form_body(BANNER_FIELDS, required=["title"]), update_body=form_body(BANNER_FIELDS))


def _logo_from_image(obj, payload, request, db):
    # The dashboard uploads the file as `image`; it is stored as the relative `logo` path.
    if payload.get("image"):
        obj.logo = payload["image"]
    extra = dict(obj.extra or {})
    extra.pop("image", None)
    obj.extra = extra or None


def _logo_out(obj, request):
    # Most screens use `logo` as-is in <img src>, so it is returned as a full URL for the current host.
    return {**serialize(obj), "logo": public_url(request, obj.logo)}


register_crud(router, models.WebsiteLogo, label="Logo", list_path="/websitelogos", create_path="/websitelogos",
              update_paths=["/websitelogosupdate/{id}"], delete_paths=["/websitelogodelete/{id}"],
              before_save=_logo_from_image, item=_logo_out,
              create_body=form_body(LOGO_FIELDS), update_body=form_body(LOGO_FIELDS))
register_crud(router, models.ProductPage, label="Product page", list_path="/productpages",
              create_path="/productpages", update_paths=["/productpagesupdate/{id}"],
              delete_paths=["/productpagedelete/{id}"],
              create_body=json_body({"headline": "...", "paragraph": "..."}))


def _legal_slug(obj, payload, request, db):
    if payload.get("slug") or not obj.slug or "title" in payload:
        obj.slug = unique_slug(db, models.LegalPage, payload.get("slug") or obj.title, exclude_id=obj.id)


register_crud(router, models.LegalPage, label="Legal page", list_path="/legalpages", create_path="/legalpages",
              update_paths=["/legalpagesupdate/{id}"], delete_paths=["/legalpagedelete/{id}"],
              before_save=_legal_slug,
              create_body=json_body({"title": "Privacy Policy", "content": "<p>...</p>"}))


@router.get("/legalpages/{slug}")
def get_legal_page(slug: str, db: Session = Depends(get_db)):
    query = db.query(models.LegalPage)
    page = query.filter(models.LegalPage.slug == slug).first()
    if not page and slug.isdigit():
        page = db.get(models.LegalPage, int(slug))
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return serialize(page)


register_crud(router, models.Congratulation, label="Congratulation", list_key="congratulations",
              list_path="/congratulations", create_path="/congratulations",
              update_paths=["/congratulationsupdate/{id}"], delete_paths=["/congratulationdelete/{id}"],
              create_body=json_body({"headline": "...", "paragraph": "..."}))
register_crud(router, models.Community, label="Community", list_path="/communities", create_path="/communities",
              update_paths=["/communitiesupdate/{id}"], delete_paths=["/communitydelete/{id}"],
              create_body=form_body(COMMUNITY_FIELDS, required=["name"]), update_body=form_body(COMMUNITY_FIELDS))
def _offer_out(obj, request):
    # The offer screens prefix the image with the bare host (no /storage), so send a full URL.
    return {**serialize(obj), "image": public_url(request, obj.image)}


register_crud(router, models.Offer, label="Offer", list_path="/offers", create_path="/offers",
              update_paths=["/offers/{id}"], delete_paths=["/offers/{id}"],
              order_by=[models.Offer.sort_order, models.Offer.id], item=_offer_out,
              create_body=form_body(OFFER_FIELDS, required=["title"]), update_body=form_body(OFFER_FIELDS))


# --- Header/footer custom code (a single settings row) ---
def _settings_out(row) -> dict:
    return {"header_script": row.header_script or "", "footer_script": row.footer_script or ""} if row \
        else {"header_script": "", "footer_script": ""}


@router.get("/site-settings")
def get_site_settings(db: Session = Depends(get_db)):
    return _settings_out(db.query(models.SiteSetting).order_by(models.SiteSetting.id).first())


@router.post("/site-settings", dependencies=[Depends(get_current_admin)],
                  openapi_extra=json_body({"header_script": "<script>...</script>", "footer_script": ""}))
@router.put("/site-settings", dependencies=[Depends(get_current_admin)],
                  openapi_extra=json_body({"header_script": "<script>...</script>", "footer_script": ""}))
async def save_site_settings(request: Request, db: Session = Depends(get_db)):
    payload = await read_payload(request)
    row = db.query(models.SiteSetting).order_by(models.SiteSetting.id).first() or models.SiteSetting()
    for field in ("header_script", "footer_script"):
        if field in payload:
            setattr(row, field, payload[field] or "")
    db.add(row)
    db.commit()
    db.refresh(row)
    return _settings_out(row)
