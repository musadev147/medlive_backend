# Medivila API Documentation

This document outlines the REST API endpoints consumed by the Medivila frontend application. It is intended for backend developers to implement or maintain the API.

## Base URL

The API endpoints are relative to the `apiUrl` configured in `src/config.js`.

- **Production Base URL:** `https://api.medivila.com/api`

## Authentication

Some endpoints (especially those under `/Dashboard`, `/users`, and protected user routes like booking/checkout) require a Bearer token.

- **Header:** `Authorization: Bearer <token>`

### Access Levels

- **Public (no token):** `POST /login`, `POST /userlogin`, `POST /logout`, `POST /users` (customer sign-up), every storefront `GET` (products, categories, colours, sizes, homepages, menus, banners, logos, product pages, legal pages, delivery charges, payment methods, pixels, social links, contact info, bonus coins, coin packages, COD advances, congratulations, communities, offers, lab tests, site settings), `GET /customers/{order_id}`, `GET /customers/phone/{phone}`, `GET /customers/search/customerdata` (phone match only, no personal data), `POST /customers`, `POST /cartstore`, `POST /leads`, `POST /contacts`, `POST /cointransactions`, `POST /prescriptions`, `POST /fb-track`.
- **Logged-in user:** `GET /user`, `GET /users/{phone}` (own phone only), `GET /cointransactions` (own only). Checkout accepts an optional token; it is required when `coins_used > 0`.
- **Admin only:** every other create/update/delete route, plus `GET /users`, `/customers`, `/leads`, `/leads/{id}`, `/transactions`, `/contacts`, `/steadfasts`, `/prescriptions`.
- Missing/invalid token → `401`; non-admin on an admin route → `403`. `POST /users` with a staff `type` is only allowed for an admin caller. `/userlogin` (phone only) never logs in staff accounts.
- Tokens are reused across logins, so several devices can stay signed in; `POST /logout` revokes the token.

### Response conventions

- **Errors:** `{"message": "...", "detail": ...}`; validation errors are `422 {"message": "...", "errors": {"field": ["..."]}}`.
- **Creates** return `201` with `{"success": true, "message": "...", "id": ..., "data": {...}}`; updates return `{"success": true, "data": {...}}`.
- **Flags** (`is_active`, `status`, `show_on_homepage`, `clothing`, ...) are returned as `1`/`0`.
- **Files** are saved under `/storage` and stored as relative paths (`images/<name>.png`), so the frontend builds `${imageUrl}/${path}`. Exceptions: website logo `logo` and prescription `file_url` are full URLs.
- **Update routes accept `PUT` and `POST`**, including Laravel-style `POST` with `_method=PUT`.

### Endpoints

**POST `/login` (User & Admin Login)**

- **Content-Type:** `application/json`
- **Payload Structure:**
  ```json
  {
    "email": "user_or_admin@example.com",
    "password": "password123"
  }
  ```
- **Response Structure (Success for User):**
  ```json
  {
    "remember_token": "your_generated_token_string",
    "type": "user"
  }
  ```
- **Response Structure (Success for Admin):**
  ```json
  {
    "remember_token": "your_generated_token_string",
    "type": "admin"
  }
  ```
- **Role Based Access:** The API must validate the user's role. It should return `type: "admin"` for administrator accounts and `type: "user"` for regular customer accounts. Admin-specific API endpoints (like creating products, updating order status) must verify on the backend that the Bearer token belongs to an admin.
- **Frontend Handling:** The frontend stores `remember_token` as `'token'` and `type` as `'type'` in `localStorage`. The frontend uses this `type` to determine if the user can access the admin dashboard.

**POST `/users` (User Registration)**

- **Content-Type:** `application/json`
- **Payload Structure:**
  ```json
  {
    "name": "John Doe",
    "email": "user@example.com",
    "password": "password123",
    "password_confirmation": "password123",
    "type": "user"
  }
  ```
- **Frontend Handling:** Redirects to `/login` upon success.

---

## 1. Products & Categories

### Products

- **GET** `/products` - Retrieve a list of products. (Supports pagination and query params `?page=1&limit=10&category_id=x`; response: `{ page, limit, total, last_page, products: [...] }`)
- **GET** `/products/{id}` - Retrieve a single product.
- **POST** `/products` - Create a new product. (Uses `multipart/form-data`)
  - **Payload Structure:**
    - `name` (string)
    - `price` (number)
    - `discount_price` (number, optional)
    - `category_id` (number)
    - `clothing` (integer, 1 or 0)
    - `images[]` (files)
    - `colors[index][color]` (string)
    - `colors[index][image]` (file)
    - `colors[index][sizes][sizeIndex][size]` (string)
    - `singleProductSizes[index][size]` (string)
    - `bulk_discounts[index][title]`, `[offer_quantity]`, `[discount_percentage]`
    - `bumps[index][bump_price]`, `[title]`, `[description]`, `[image]`
    - `homepage[headline]`, `homepage[paragraph]`, `homepage[description]`
- **POST** `/productsupdate/{id}` - Update an existing product. (Same payload as creation).
- **DELETE** `/productdelete/{id}` - Delete a product.

### Categories

- **GET** `/category` - Retrieve all categories.
- **POST** `/category` - Create a new category. (Uses `multipart/form-data`)
  - **Payload Structure:**
    - `name` (string)
    - `image` (file)
    - `sort_order` (integer)
    - `product_limit` (integer)
    - `parent_id` (integer or empty string)
    - `show_on_homepage` (integer, 1 or 0)
- **POST** `/categoryupdate/{id}` - Update a category.
- **DELETE** `/categorydelete/{id}` - Delete a category.

### Product Attributes (Colors & Sizes)

- **GET** `/colors` - Get all colors.
- **POST** `/colors` - Create a color. (Payload: `{ "color": "Red" }`)
- **PUT** `/colorsupdate/{id}` - Update a color.
- **DELETE** `/colordelete/{id}` - Delete a color.
- **GET** `/sizes` - Get all sizes.
- **POST** `/sizes` - Create a size. (Payload: `{ "size": "XL" }`)
- **PUT** `/sizesupdate/{id}` - Update a size.
- **DELETE** `/sizedelete/{id}` - Delete a size.

---

## 2. Orders & Customers

### Orders / Checkouts

**POST `/customers` (Fast Order / Single Item Checkout)**

- **Content-Type:** `application/json`
- **Payload Structure:**
  ```json
  {
    "order_id": "A2C123456",
    "product_name": "Product Name",
    "color": "Red",
    "size": "XL",
    "quantity": 1,
    "delivery_charge": 120,
    "cod_advance": 0,
    "product_price": 500,
    "total": 620,
    "transaction_id": "",
    "customer_name": "John Doe",
    "customer_address": "Village/Road: ..., district: ..., division: ...",
    "phone_number": "017XXXXXXXX",
    "coins_used": 0,
    "payment_method": "cash",
    "payment_number": "",
    "coin_discount": 0,
    "delivery_note": "Handle with care",
    "bulk_discounts": [{ "id": 1 }],
    "bumps": [{ "id": 1, "bump_price": "100" }],
    "items": [
      {
        "product_id": 1,
        "product_name": "Product Name",
        "quantity": 1,
        "price": 500,
        "color": "Red",
        "size": "XL"
      }
    ]
  }
  ```

**POST `/cartstore` (Cart Order / Lab Test Checkout)**

- **Content-Type:** `application/json`
- **Payload Structure:**
  ```json
  {
    "order_id": "A2C123456",
    "order_type": "product | lab_test | mixed",
    "customer_name": "John Doe",
    "phone_number": "017XXXXXXXX",
    "customer_address": "Village/Road: ..., District: ..., Division: ...",
    "product_name": "Item 1, Item 2",
    "quantity": 2,
    "lab_test_name": null,
    "lab_name": null,
    "patient_count": null,
    "report_time": null,
    "lab_test_image": null,
    "delivery_charge": 120,
    "product_price": 1000,
    "subtotal": 1000,
    "lab_tests_total": 0,
    "regular_products_total": 1000,
    "total": 1120,
    "payment_method": "cod",
    "delivery_note": null,
    "delivery_status": "New Order",
    "has_lab_tests": false,
    "has_regular_products": true,
    "items": [
      {
        "id": "1-colorId-size",
        "product_id": 1,
        "product_name": "Item 1",
        "product_image": "url",
        "quantity": 1,
        "price": 500,
        "color": "Red",
        "size": "XL"
      }
    ]
  }
  ```

### Customer / Order Management

- **GET** `/customers` - Retrieve all orders/customers. (Supports `?export_csv=true` and `?status=pending`).
- **GET** `/customers/search/customerdata` - Search for specific customers.
- **PUT** `/customersupdate/{id}` - Update order details.
- **DELETE** `/customerdelete/{id}` - Delete an order.
- **PUT** `/customersstatusupdate/{id}` - Update order status. (Payload: `{ "delivery_status": "Shipped" }`).
- **PUT** `/customersconsignmentupdate/{invoice}` - Update consignment info.
- **POST** `/customers/{id}/add-points` - Add reward points/coins to a customer.

### Leads

- **GET** `/leads` - Retrieve all leads.
- **POST** `/leads` - Auto-save lead data on checkout abandonment.
  - **Payload Structure:**
    ```json
    {
      "order_id": "A2C123456",
      "customer_name": "John Doe",
      "phone_number": "017XXXXXXXX",
      "product_price": 500,
      "customer_address": "...",
      "status": "lead",
      "quantity": 1,
      "product_name": "Product Name",
      "page_url": "https://..."
    }
    ```
- **PUT** `/leadsupdate/{id}` - Update a lead.
- **DELETE** `/leaddelete/{id}` - Delete a lead.
- **POST** `/leads/{id}/convert` - Convert a lead into a customer/order.

### Transactions & Coins

- **GET** `/transactions` - Get all transactions.
- **PUT** `/transactionsupdate/{id}` - Update transaction.
- **DELETE** `/transactiondelete/{id}` - Delete transaction.
- **GET** `/cointransactions` - Get user coin transactions.
- **POST** `/cointransactions` - Submit a coin transaction (e.g., buying a coin package).
- **GET** `/coinpackages` - Get available coin packages.

---

## 3. Site Settings & UI Content

### Home Pages & Menus

- **GET** `/homepages` - Retrieve homepage configurations.
- **POST** `/homepages` - Create a new homepage layout.
- **PUT** `/homepagesupdate/{id}` - Update homepage layout.
- **DELETE** `/homepagedelete/{id}` - Delete homepage layout.
- **GET** `/menus` - Retrieve navigation menus.
- **POST** `/menus` - Create a menu item.
- **PUT** `/menusupdate/{id}` - Update a menu item.
- **DELETE** `/menudelete/{id}` - Delete a menu item.

### Banners & Logos

- **GET** `/banners` - Get all banners.
- **POST** `/banners` - Create a new banner. (Uses `multipart/form-data`)
  - **Payload Structure:**
    - `title` (string)
    - `description` (string)
    - `image` (file)
    - `link` (string)
    - `is_active` (integer, 1 or 0)
    - `sort_order` (integer)
- **POST** `/bannersupdate/{id}` - Update a banner.
- **DELETE** `/bannerdelete/{id}` - Delete a banner.
- **GET** `/websitelogos` - Get website logo configurations.
- **POST** `/websitelogos` - Create a logo entry.
- **PUT** `/websitelogosupdate/{id}` - Update a logo.
- **DELETE** `/websitelogodelete/{id}` - Delete a logo.

### Pages & Legal

- **GET** `/productpages` - Get custom product page layouts.
- **POST** `/productpages` - Create product page.
- **PUT** `/productpagesupdate/{id}` - Update product page.
- **DELETE** `/productpagedelete/{id}` - Delete product page.
- **GET** `/legalpages` - Get legal pages (Privacy Policy, Terms).
- **POST** `/legalpages` - Create a legal page.
- **PUT** `/legalpagesupdate/{id}` - Update a legal page.
- **DELETE** `/legalpagedelete/{id}` - Delete a legal page.

---

## 4. System & Integration

### Delivery & Payment

- **GET** `/deliverycharges` - Get delivery charge rules.
- **POST** `/deliverycharges` - Create delivery charge.
- **PUT** `/deliverychargesupdate/{id}` - Update delivery charge.
- **DELETE** `/deliverychargedelete/{id}` - Delete delivery charge.
- **GET** `/paymentmethod` - Get payment methods.
- **POST** `/paymentmethod` - Create payment method.
- **POST** `/paymentmethodupdate/{id}` - Update payment method.
- **DELETE** `/paymentmethoddelete/{id}` - Delete payment method.

### Steadfast Courier Integration

- **GET** `/steadfasts` - Get Steadfast integration configs.
- **POST** `/steadfasts` - Create Steadfast config.
- **PUT** `/steadfastsupdate/{id}` - Update config.
- **DELETE** `/steadfastdelete/{id}` - Delete config.
  _(Note: App also makes direct external requests to Steadfast API for `status_by_invoice` and `fraud_check`)._

### Pixels & Tracking

- **GET** `/pixels` - Get tracking pixels (Facebook, GTM).
- **POST** `/pixels` - Create a pixel.
- **PUT** `/pixelsupdate/{id}` - Update a pixel.
- **DELETE** `/pixeldelete/{id}` - Delete a pixel.

### Contacts & Social

- **GET** `/sociallinks` - Get social media links.
- **POST** `/sociallinks` - Add a social link.
- **PUT** `/sociallinksupdate/{id}` - Update a social link.
- **DELETE** `/sociallinkdelete/{id}` - Delete a social link.
- **GET** `/contacts` - Get contact form submissions.
- **POST** `/contacts` - Submit contact form.
- **PUT** `/contactsupdate/{id}` - Update contact message status.
- **DELETE** `/contactdelete/{id}` - Delete contact message.
- **GET** `/contactinfos` - Get company contact info.
- **POST** `/contactinfos` - Add contact info.
- **PUT** `/contactinfosupdate/{id}` - Update contact info.
- **DELETE** `/contactinfodelete/{id}` - Delete contact info.

---

## 5. Users

- **GET** `/users` - Retrieve system users / admins.
- **PUT** `/usersupdate/{id}` - Update user.
- **DELETE** `/userdelete/{id}` - Delete user.
- **GET** `/user` - Retrieve authenticated user info.

---

## Note on Conventions

- **Update endpoints** typically use `PUT` or `POST` appended with `update/{id}` instead of standard REST `PUT /{id}` (e.g., `/productsupdate/1`).
- **Delete endpoints** use `DELETE` appended with `delete/{id}` instead of standard `DELETE /{id}` (e.g., `/productdelete/1`).
- **File Uploads** (Products, Categories, Banners) require headers: `{"Content-Type": "multipart/form-data"}`.
- **Booleans** are often sent as integers (`"1"` or `"0"`) for MySQL compatibility in form data.

---

## 6. Frontend (medivila-web) response shapes & extra endpoints

These follow what the React frontend reads.

### Lists that are wrapped in an object

| Endpoint | Response |
|---|---|
| `GET /category` | `{ "categories": [...] }` (each has `slug`) |
| `GET /pixels` | `{ "pixels": [...] }` |
| `GET /homepages` | `{ "homePages": [...] }` |
| `GET /transactions` | `{ "transactions": [...] }` (`tnxId`, `tnxNumber`; created automatically when an order has a `transaction_id`) |
| `GET /congratulations` | `{ "congratulations": [...] }` |
| `GET /users` | `{ "users": [...] }` (`phone`, `points`) |
| `GET /leads?page=1` | Laravel paginator `{ data, current_page, last_page, per_page, total }` plus `leads` (same rows) |

Every other list (banners, menus, colours, sizes, logos, legal pages, coin packages, bonus coins, COD advances, communities, offers, lab tests, ...) is a plain array.

### Products

- `GET /products` → plain array of all products. With `page`/`per_page` → paginator (`data`, `last_page`, `total`, ...). With `limit`/`offset` → `{ data, total }`. Filters: `search`, `category_id`, `category_ids=1,2`.
- `GET /products/{id or slug}` → one product.
- Product shape: `slug`, `images: [{ "image": "images/x.png" }]`, `colors: [{ id, product_id, color, image, sizes: [{ id, color_id, size }] }]`, `single_product_sizes: [{ id, size }]`, `bulk_discounts: [{ id, ... }]`, `bumps: [{ id, image, ... }]`, `homepage: { headline, paragraph, description }`.
- Create/update accept the dashboard's form keys: `images[i]`, `existing_images[i]`, `colors[i][id|color|image|existing_image]`, `colors[i][sizes][j][id|size]`, `singleProductSizes[i][id|size]`, `bumps[i][existing_image]`. Colours/sizes sent back with an `id` keep it; omitted ones are deleted.
- `GET /category/{id or slug}?page=&per_page=` → `{ category, products: paginator }` (includes sub-categories).
- Colours are rows owned by a product: `POST /colors` (`product_id`, `color`, `image`), `POST /livesupdate/{id}` = `/colorsupdate/{id}`. `POST /sizes` takes `color_id`.

### Orders / customers

- `GET /customers` → array; `?page=` → `{ customers: paginator }`; `?export_csv=true` → `{ customers: [...] }` (the dashboard builds the CSV). Filters: `status` (tab key: `all`, `new_order`, `ready_to_ship`, ...), `start_date`, `end_date` (`YYYY-MM-DD`).
- `GET /customers/{order_id}` → `{ customer }` (thank-you page).
- `GET /customers/phone/{phone}` → `{ customer: { phone_number, delivery_status } }`.
- `GET /customers/search/customerdata?search=` → `{ customers: paginator }`.
- `POST /customers/{id}/add-points` (no body) → points = `bonus_percentage`% of the order total, credited to the account with the order's phone (created with `first_reg_bonus` if new). Response `{ points_added, total_points }`. Only once per order.
- Insufficient coins at checkout → `400 {"message": "Insufficient coins"}`.
- Leads: `GET|PUT|DELETE /leads/{id}`; `POST /leads/{id}/convert` marks the lead converted (and creates the order only if it doesn't exist yet).

### Users & coins

- `POST /userlogin` `{ phone }` → `{ token, user }`, `404` if no account. `POST /logout`.
- `POST /login` accepts an email **or phone** in `email`.
- `GET /users/{phone}` → user with `points`. `POST /users/{phone}/add-points` `{ points }` → `{ points_added, total_points }`.
- `GET|POST /bonuscoins`, `PUT|DELETE /bonuscoins/{id}` (`first_reg_bonus`, `bonus_percentage`, `coin_name`).
- `GET|POST /coinpackages`, `PUT|DELETE /coinpackages/{id}` (`name`, `coins`, `price`, `is_active`; public list shows active only).
- `POST /cointransactions` (`coin_package_id`, `name`, `phone`, `wallet_number`; `status` starts at 0), `PUT|DELETE /cointransactions/{id}`, `PUT /cointransactions/{id}/update-status` (sets `status` 1).
- `GET|POST /codadvances`, `/codadvancesupdate/{id}`, `/codadvancedelete/{id}` (`title`, `sub_title`, `headline`, `pay_amount`, `is_active`; public list shows active only).

### Other content

- `GET /legalpages/{slug}`; legal pages get a `slug` from the title.
- `/congratulations`, `/congratulationsupdate/{id}`, `/congratulationdelete/{id}` (`headline`, `paragraph`).
- `/communities`, `/communitiesupdate/{id}`, `/communitydelete/{id}` (`icon`, `name`, `description`, `image`, `banner`, `button_text`, `url`).
- `/offers`, `POST|PUT|DELETE /offers/{id}` (`title`, `discount`, `subtext`, `button_text`, `button_link`, `card_color`, `image`, `sort_order`).
- `/lab-tests`, `GET|POST|PUT|DELETE /lab-tests/{id}`; `labs[i][name|original_price|discounted_price|rating|is_recommended|logo]`.
- Prescriptions: see section 8.
- `GET|POST /site-settings` → `{ header_script, footer_script }`.
- `POST /fb-track` → accepted; not forwarded to Facebook until a Conversions API token is configured.
- Field names: delivery charges `district_name`, `delivery_charge`, `delivery_note`, `estimated_days`; payment methods `payment_method`, `payment_number`, `image`; Steadfast `apiKey`, `secretKey`; social links `name`, `url`, `icon_class`, `status`; contact info `phone`, `email`, `address`, `tnx_number`; menus `name`, `category_id`, `menu_type`, `order`; website logos `image` upload → `logo`, `brand_slogan`.

---

## 7. Home Lab & Wholesaler pages

- `GET /page-content/{page}` → `{ page, content }` (public); `POST|PUT /page-content/{page}` `{ content: {...} }` (admin). Pages in use: `home-lab`, `wholesaler`, `appointment`. Empty content means the frontend shows its built-in defaults.
- `POST /upload-image` (admin, multipart `image`) → `{ path, url }` for images used in page content.
- `GET /lab-tests?section=trending|packages|most_booked&category_id=` — public list shows active tests only. Lab tests also take `sections[]`, `category_ids[]`, `sort_order`, `is_active`, and `labs[i][existing_logo]` to keep a lab's logo when editing.
- `/lab-categories` (`name`, `icon`, `image`, `type`: `organ` | `concern`, `sort_order`, `is_active`) and `/lab-partners` (`name`, `icon`, `logo`, `sort_order`, `is_active`): `GET` public (active only), `POST` create, `POST|PUT|DELETE /{resource}/{id}` (admin).
- `GET /products?wholesale=1` → products listed on the Wholesaler page. `POST|PUT /products/{id}/wholesale` `{ is_wholesale, wholesale_price, wholesale_min_qty }` (admin).
- `POST /wholesale-applications` (public) `{ request_type: account|quote, owner_name, phone, business_name, email, address, trade_license, message }`; `GET /wholesale-applications?status=&page=` (admin paginator); `PUT /wholesale-applications/{id}` `{ status: pending|approved|rejected }`; `DELETE`.

## 8. Prescriptions & Appointments

### Prescriptions
Files are **private**: they are stored outside `/storage` and served only through signed links that expire after 2 hours.

- `POST /prescriptions` (public, multipart): `name`, `phone` (11-digit BD mobile), `notes`, `file` (JPG/PNG/WEBP/PDF, max 5 MB; the file content is checked, not just the extension).
- `GET /prescriptions?status=&search=&page=&per_page=` (admin) → array, or a paginator when `page` is given. Each row has `status`, `admin_note`, `file_type` (`image` | `pdf`) and `file_url` (signed link; add `&download=true` to download).
- `GET /prescriptions/{id}/file?expires=&signature=[&download=true]` → the file (link from `file_url`).
- `PUT /prescriptions/{id}` (admin) `{ status: pending|contacted|completed|cancelled, admin_note }`.
- `DELETE /prescriptions/{id}` (admin) — also deletes the file.
- Env: `PRIVATE_UPLOAD_DIR` (default `private_uploads/`), `PRESCRIPTION_URL_SECRET` (default: generated once and kept in `private_uploads/.url_secret`).

### Appointments
- `GET /appointment-types` (public: active only) and `POST /appointment-types`, `POST|PUT|DELETE /appointment-types/{id}` (admin): `name`, `icon`, `fee`, `duration`, `description`, `sort_order`, `is_active`.
- Booking rules come from page content `appointment`: `time_slots` (`[{ "time": "10:00 AM" }]`), `max_per_slot` (default 3), `booking_days_ahead` (default 30), `closed_days` (`[{ "day": "Friday" }]`).
- `GET /appointment-slots?date=YYYY-MM-DD` → `{ date, closed, slots: [{ time, remaining, available }], settings }`. Slots that already started today are unavailable.
- `POST /appointments` (public) `{ appointment_type_id, date, time, patient_name, mobile, age, gender: male|female|other, issue, notes }` → `201 { appointment_no: "APT123456", data }`. Rejects past dates, closed days, unknown slots and full slots (`409`).
- `GET /appointments?status=&date=&search=&page=` (admin) → paginator plus `counts` per status.
- `PUT /appointments/{id}` (admin) `{ status: pending|confirmed|completed|cancelled, date, time, admin_note }` — changing date/time re-checks availability. Cancelled bookings free their slot.
- `DELETE /appointments/{id}` (admin).

