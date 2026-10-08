from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import models
from database import SessionLocal, engine, sync_columns
from routers import auth, care, lab_tests, orders, pages, products, settings, system
from utils import UPLOAD_DIR, unique_slug

# Create the database tables, then add any columns new model fields need
models.Base.metadata.create_all(bind=engine)
sync_columns()


def backfill_slugs():
    """Rows created before slugs existed get one, so slug-based URLs work for them too."""
    db = SessionLocal()
    try:
        for model, source in ((models.Category, "name"), (models.Product, "name"), (models.LegalPage, "title")):
            for row in db.query(model).filter(model.slug.is_(None)).all():
                row.slug = unique_slug(db, model, getattr(row, source) or str(row.id), exclude_id=row.id)
                db.flush()
        db.commit()
    finally:
        db.close()


backfill_slugs()

app = FastAPI(
    title="Medivila API",
    description="FastAPI Backend for Medivila eCommerce Platform",
    version="1.0.0",
    docs_url=None,  # replaced by the custom /docs below
)

# Any origin may call the API. Some frontend calls set `withCredentials`, which browsers only accept
# when the exact origin is echoed back, hence the regex instead of "*". Auth is a Bearer header, not
# cookies, so allowing credentials does not expose a session to other sites.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Errors use the Laravel shape the frontend reads: {"message": ..., "errors": {field: [...]}}.
# `detail` is kept so Swagger and other FastAPI clients still work.
@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(status_code=exc.status_code, content={"message": detail, "detail": exc.detail},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors: dict = {}
    for err in exc.errors():
        field = str(err["loc"][-1]) if err.get("loc") else "body"
        errors.setdefault(field, []).append(err.get("msg", "Invalid value"))
    first = next(iter(errors.values()))[0] if errors else "Invalid data"
    return JSONResponse(status_code=422, content={"message": first, "errors": errors,
                                                  "detail": jsonable_encoder(exc.errors())})

# Include all the sub-routers
app.include_router(auth.router, prefix="/api", tags=["Authentication & Users"])
app.include_router(products.router, prefix="/api", tags=["Products & Categories"])
app.include_router(orders.router, prefix="/api", tags=["Orders & Customers"])
app.include_router(settings.router, prefix="/api", tags=["Site Settings & Content"])
app.include_router(system.router, prefix="/api", tags=["System & Integrations"])
app.include_router(lab_tests.router, prefix="/api", tags=["Lab Tests & Home Lab"])
app.include_router(pages.router, prefix="/api", tags=["Page Content & Wholesale"])
app.include_router(care.router, prefix="/api", tags=["Prescriptions & Appointments"])

# Uploaded files. The frontend's `imageUrl` is "<host>/storage", like the old Laravel API.
app.mount("/storage", StaticFiles(directory=UPLOAD_DIR), name="storage")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/")
def read_root():
    return {"message": "Welcome to Medivila API"}


# Swagger UI that picks up the token from a successful /api/login response and
# authorizes every request with it, so it never has to be pasted by hand.
SWAGGER_HTML = r"""<!DOCTYPE html>
<html>
<head>
  <title>Medivila API - Swagger UI</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <link rel="shortcut icon" href="https://fastapi.tiangolo.com/img/favicon.png">
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
  window.ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    layout: "BaseLayout",
    deepLinking: true,
    persistAuthorization: true,
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    responseInterceptor: function (res) {
      try {
        if (res.ok && /\/api\/login$/.test(new URL(res.url).pathname)) {
          var body = res.body || JSON.parse(res.text);
          if (body && body.remember_token) {
            // Same action the Authorize dialog uses; with persistAuthorization it survives a reload.
            window.ui.authActions.authorizeWithPersistOption({
              HTTPBearer: {
                name: "HTTPBearer",
                schema: { type: "http", scheme: "bearer" },
                value: body.remember_token
              }
            });
          }
        }
      } catch (e) { console.warn("Auto-authorize failed", e); }
      return res;
    }
  });
</script>
</body>
</html>"""


@app.get("/docs", include_in_schema=False)
def swagger_ui():
    return HTMLResponse(SWAGGER_HTML)
