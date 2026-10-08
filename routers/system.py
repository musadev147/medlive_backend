from fastapi import APIRouter, Request

import models
from security import get_current_admin
from utils import FILE, form_body, json_body, read_payload, register_crud

router = APIRouter()

PAYMENT_FIELDS = {"payment_method": "string", "payment_number": "string", "image": FILE}

register_crud(router, models.DeliveryCharge, label="Delivery charge", list_path="/deliverycharges",
              create_path="/deliverycharges", update_paths=["/deliverychargesupdate/{id}"],
              delete_paths=["/deliverychargedelete/{id}"],
              create_body=json_body({"district_name": "Dhaka", "delivery_charge": 60, "delivery_note": "",
                                     "estimated_days": "1-2"}))
register_crud(router, models.PaymentMethod, label="Payment method", list_path="/paymentmethod",
              create_path="/paymentmethod", update_paths=["/paymentmethodupdate/{id}"],
              delete_paths=["/paymentmethoddelete/{id}"],
              create_body=form_body(PAYMENT_FIELDS, required=["payment_method"]),
              update_body=form_body(PAYMENT_FIELDS))
# Steadfast configs hold courier API credentials, so even reading them is admin-only.
register_crud(router, models.Steadfast, label="Steadfast config", list_path="/steadfasts",
              create_path="/steadfasts", update_paths=["/steadfastsupdate/{id}"],
              delete_paths=["/steadfastdelete/{id}"], read_dep=get_current_admin,
              create_body=json_body({"apiKey": "...", "secretKey": "..."}))
register_crud(router, models.Pixel, label="Pixel", list_key="pixels", list_path="/pixels", create_path="/pixels",
              update_paths=["/pixelsupdate/{id}"], delete_paths=["/pixeldelete/{id}"],
              create_body=json_body({"pixel_id": "1234567890"}))
register_crud(router, models.SocialLink, label="Social link", list_path="/sociallinks",
              create_path="/sociallinks", update_paths=["/sociallinksupdate/{id}"],
              delete_paths=["/sociallinkdelete/{id}"],
              create_body=json_body({"name": "Facebook", "url": "https://facebook.com/...",
                                     "icon_class": "FaFacebook", "status": 1}))
# Anyone can submit the contact form; only admins can read or manage submissions.
register_crud(router, models.Contact, label="Contact", list_path="/contacts", create_path="/contacts",
              update_paths=["/contactsupdate/{id}"], delete_paths=["/contactdelete/{id}"],
              read_dep=get_current_admin, create_dep=None,
              create_body=json_body({"name": "...", "email": "...", "phone": "...", "subject": "...",
                                     "message": "..."}))
register_crud(router, models.ContactInfo, label="Contact info", list_path="/contactinfos",
              create_path="/contactinfos", update_paths=["/contactinfosupdate/{id}"],
              delete_paths=["/contactinfodelete/{id}"],
              create_body=json_body({"phone": "...", "email": "...", "address": "...", "tnx_number": "..."}))


# --- Facebook Conversions API relay ---
@router.post("/fb-track", openapi_extra=json_body({"event": "Purchase", "event_id": "..."}))
async def fb_track(request: Request):
    """The storefront also fires the browser pixel; this server-side relay is accepted but not forwarded
    until a Conversions API access token is configured."""
    payload = await read_payload(request)
    return {"success": True, "forwarded": False, "event": payload.get("event")}
