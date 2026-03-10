# ch_pos — GoGizmo & GoFix POS Blueprint

**Version:** 1.0  
**Date:** 2026-03-08  
**Status:** Design — ready for implementation  

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Data Model Design](#2-data-model-design)
3. [POS Flows](#3-pos-flows)
4. [AI Integration Specification](#4-ai-integration-specification)
5. [ERPNext Implementation Guidance](#5-erpnext-implementation-guidance)
6. [Security, Permissions & Data Integrity](#6-security-permissions--data-integrity)
7. [Configuration Checklist](#7-configuration-checklist)

---

## 1. High-Level Architecture

### 1.1 Layered View

```
┌─────────────────────────────────────────────────────────────┐
│                        UI LAYER                              │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐                 │
│  │ System   │  │ Staff     │  │ Customer │                  │
│  │ POS      │  │ Tablet    │  │ Kiosk    │                  │
│  │ (Desktop)│  │ POS       │  │ POS      │                  │
│  └────┬─────┘  └─────┬─────┘  └────┬─────┘                 │
│       │               │              │                       │
│  ERPNext POS UI   Custom Vue    Custom Vue                   │
│  + Client Scripts  Page/Dialog   Page (read-only)            │
├───────┴───────────────┴──────────────┴───────────────────────┤
│                   APPLICATION LAYER                           │
│                                                               │
│  ch_pos app                                                  │
│  ├── pos_core/       Search, filter, cart logic              │
│  ├── pos_kiosk/      Guided selling, comparison, tokens      │
│  ├── pos_ai/         AI service integration                  │
│  └── pos_repair/     Service intake from POS counter         │
│                                                               │
│  Existing apps (consumed, not modified):                      │
│  ├── ch_item_master  Models, Prices, Offers, Stores, etc.   │
│  ├── ch_erp15        Marginal GST, Warehouse Capacity       │
│  ├── gofix           Service Requests, Service Order WF      │
│  └── buyback         Buyback Assessment, Pricing             │
├───────────────────────────────────────────────────────────────┤
│                   ERPNEXT CORE                                │
│  Item, Customer, POS Profile, POS Invoice, Sales Invoice,    │
│  Sales Order, Pricing Rule, Price List, Warehouse,           │
│  Payment Entry, Serial No, Loyalty Program                   │
├───────────────────────────────────────────────────────────────┤
│                   EXTERNAL SERVICES                           │
│  AI API (comparison, upsell, offer explainer)                │
│  Payment Gateway (UPI, Cards — future)                       │
│  SMS/WhatsApp (receipt, repair updates)                      │
└───────────────────────────────────────────────────────────────┘
```

### 1.2 How POS Profiles Map to Channels

Each physical store (CH Store) gets **three** ERPNext POS Profiles:

| Profile Name Pattern       | Channel     | Mode     | UI                                      |
|---------------------------|-------------|----------|-----------------------------------------|
| `{store} - Counter`       | System POS  | Desktop  | Standard ERPNext POS + client scripts   |
| `{store} - Tablet`        | Staff POS   | Tablet   | Custom Frappe page `pos-tablet`         |
| `{store} - Kiosk`         | Kiosk POS   | Kiosk    | Custom Frappe page `pos-kiosk`          |

All three share the same warehouse, company, cost center, and price list (CH POS). Behavior differences are driven by profile-level custom fields and role restrictions, not separate codebases.

### 1.3 App Structure

```
ch_pos/
├── ch_pos/
│   ├── pos_core/
│   │   └── doctype/
│   │       ├── pos_session_log/              # Tracks kiosk/tablet sessions
│   │       └── pos_profile_extension/        # Custom settings per profile
│   ├── pos_kiosk/
│   │   └── doctype/
│   │       ├── pos_kiosk_token/              # Quote tokens from kiosk
│   │       └── pos_guided_session/           # Guided selling session data
│   ├── pos_ai/
│   │   └── doctype/
│   │       ├── pos_ai_settings/              # API keys, prompts, toggles
│   │       ├── pos_comparison_request/       # Cached AI comparisons
│   │       └── pos_comparison_template/      # Static fallback comparisons
│   ├── pos_repair/
│   │   └── doctype/
│   │       └── pos_repair_intake/            # Quick intake from POS counter
│   ├── custom/                               # Client Scripts on core doctypes
│   │   ├── pos_invoice.js
│   │   └── sales_invoice.js
│   ├── overrides/                            # Python overrides
│   │   └── pos_invoice.py
│   ├── api/
│   │   ├── search.py                         # POS search & filter endpoints
│   │   ├── guided.py                         # Guided selling logic
│   │   ├── ai.py                             # AI integration endpoints
│   │   ├── offers.py                         # Offer resolution & explanation
│   │   └── repair.py                         # Repair intake endpoints
│   ├── public/
│   │   ├── js/
│   │   │   ├── pos_extensions.js             # Client-side POS overrides
│   │   │   ├── pos_kiosk_app.js              # Kiosk SPA
│   │   │   └── pos_tablet_app.js             # Tablet SPA
│   │   └── css/
│   ├── hooks.py
│   └── modules.txt                           # POS Core\nPOS Kiosk\nPOS AI\nPOS Repair
├── pyproject.toml
└── README.md
```

---

## 2. Data Model Design

### 2.1 What Already Exists (Reused As-Is)

These are **not** duplicated in ch_pos. The POS reads them directly.

| Entity                   | Source App       | POS Usage                                      |
|--------------------------|------------------|-------------------------------------------------|
| CH Category              | ch_item_master   | Top-level browse (Phones, Laptops, Accessories) |
| CH Sub Category          | ch_item_master   | Filter level (Smartphones, Tablets, Chargers)   |
| CH Model                 | ch_item_master   | Device grouping, specs, features                |
| Item + Variants          | ERPNext          | Sellable SKUs (variant by condition)            |
| CH Item Price            | ch_item_master   | Channel-specific pricing (POS channel)          |
| CH Item Offer            | ch_item_master   | Promotions → synced to Pricing Rules            |
| CH Store                 | ch_item_master   | Store master with capability flags              |
| CH Customer Device       | ch_item_master   | Device ownership after sale                     |
| CH Serial Lifecycle      | ch_item_master   | Serial state machine (In Stock→Sold→...)        |
| CH Warranty Plan         | ch_item_master   | Warranty/VAS plans available at POS             |
| CH Loyalty Transaction   | ch_item_master   | Points earn/redeem                              |
| CH Customer Store Visit  | ch_item_master   | Visit tracking                                  |
| Service Request          | gofix            | Repair intake                                   |
| Buyback Assessment       | buyback          | Exchange/trade-in flow                          |
| Pricing Rule             | ERPNext          | Offer application                               |
| POS Profile              | ERPNext          | Profile config                                  |
| POS Invoice              | ERPNext          | Transaction                                     |

### 2.2 Item/Variant Structure for Devices

**Already designed in ch_item_master.** The hierarchy is:

```
CH Category: "Phones"
  └── CH Sub Category: "Smartphones"  (prefix: "SM")
       └── CH Model: "iPhone 14 Pro Max Green"
            └── Item (ERPNext): "SM-IPH14PM-GRN-NEW"    (variant: New)
            └── Item (ERPNext): "SM-IPH14PM-GRN-REFURB" (variant: Refurbished)
            └── Item (ERPNext): "SM-IPH14PM-GRN-USED"   (variant: Used)
```

- CH Model holds specs (RAM, Storage, Camera, Battery, etc.) via `spec_values` child table and features via `model_features`.
- CH Item Price holds per-channel prices (MRP, MOP, selling_price for POS channel).
- Accessories are non-variant Items in separate Item Groups (Cases, Chargers, Screen Guards).

**Assumption:** Condition variants (New/Refurbished/Used) are already modeled as Item Variants using an Item Attribute "Condition". If not, this needs to be set up as part of configuration.

### 2.3 Repair Job Representation

**Already built in gofix.** The POS counter creates a **Service Request** → staff converts to **Sales Order** (with `is_service_order=1`) → technician creates **Job Sheet** → follows the 9-state workflow.

The POS adds a quick-intake shortcut (see POS Repair Intake doctype below) but funnels into the existing gofix flow.

### 2.4 New Doctypes in ch_pos

#### 2.4.1 POS Profile Extension (Single-per-profile settings)

**Purpose:** Store POS mode-specific configuration that doesn't belong in core POS Profile.  
**Why custom:** ERPNext POS Profile doesn't have fields for kiosk behavior, AI toggles, or guided selling config.

| Field                    | Type           | Description                                            |
|--------------------------|----------------|--------------------------------------------------------|
| pos_profile              | Link→POS Profile | Parent profile (unique)                              |
| pos_mode                 | Select         | System / Tablet / Kiosk                                |
| enable_guided_selling    | Check          | Show guided question flow                              |
| enable_ai_comparison     | Check          | Allow AI-powered comparisons                           |
| enable_ai_upsell         | Check          | Show AI upsell suggestions                             |
| enable_repair_intake     | Check          | Show "Repair" button on POS                            |
| enable_buyback_intake    | Check          | Show "Buyback/Exchange" button                         |
| max_comparison_items     | Int            | Max items in comparison (default 3)                    |
| kiosk_idle_timeout_sec   | Int            | Reset kiosk after inactivity (default 120)             |
| kiosk_allowed_item_groups| Table          | Restrict browsing to specific groups                   |
| show_cost_price          | Check          | Show incoming rate (staff/manager only)                |
| allow_manual_discount    | Check          | Allow manual discount entry                            |
| allow_rate_change        | Check          | Allow changing item rate                               |
| receipt_template         | Link→Print Format | Custom receipt per profile                          |

#### 2.4.2 POS Kiosk Token

**Purpose:** A lightweight "quote" that a kiosk generates for a customer to take to the counter. Not a Sales Order — just a numbered token with item selections.  
**Why custom:** No core doctype fits the "customer-generated, staff-convertible, expires in 30 min" use case.

| Field                | Type              | Description                                      |
|----------------------|-------------------|--------------------------------------------------|
| naming_series        | Select            | KTK-.YYYY.-.#####                                |
| store                | Link→Warehouse    | Which store                                      |
| status               | Select            | Active / Converted / Expired                     |
| items                | Table→POS Kiosk Token Item | Selected products                       |
| customer_name        | Data              | Optional name entered at kiosk                   |
| customer_phone       | Data              | Optional phone for SMS                           |
| comparison_request   | Link              | If customer did a comparison                     |
| total_estimate       | Currency          | Estimated total (informational)                  |
| expires_at           | Datetime          | Auto-set: creation + 30 min                      |
| converted_invoice    | Link→POS Invoice  | Set when staff converts                          |

**Child: POS Kiosk Token Item**

| Field        | Type           | Description                 |
|--------------|----------------|-----------------------------|
| item_code    | Link→Item      | Selected item               |
| item_name    | Data           | Fetched                     |
| qty          | Int            | Default 1                   |
| rate         | Currency       | POS selling price at time   |
| amount       | Currency       | qty × rate                  |
| offer_applied| Data           | Name of applicable offer    |

#### 2.4.3 POS Guided Session

**Purpose:** Records the guided selling interaction (answers to discovery questions) so staff and AI have context. Also enables analytics on what customers look for.  
**Why custom:** No core doctype tracks question→answer→recommendation flows.

| Field                | Type                   | Description                               |
|----------------------|------------------------|-------------------------------------------|
| naming_series        | Select                 | PGS-.YYYY.-.#####                         |
| store                | Link→Warehouse         |                                            |
| pos_profile          | Link→POS Profile       |                                            |
| status               | Select                 | In Progress / Completed / Abandoned        |
| category             | Link→CH Category       | What they're looking for                   |
| sub_category         | Link→CH Sub Category   | Narrowed down                              |
| responses            | Table→POS Guided Response | Question-answer pairs                   |
| recommended_items    | Table→POS Guided Recommendation | Results                          |
| comparison_request   | Link→POS Comparison Request | If they compared                      |
| kiosk_token          | Link→POS Kiosk Token   | If they generated a token                  |
| session_duration_sec | Int                    | Auto-calculated                            |

**Child: POS Guided Response**

| Field       | Type    | Description                                  |
|-------------|---------|----------------------------------------------|
| question    | Data    | "What's your budget?", "Primary use?"        |
| answer      | Data    | "20000-30000", "Camera"                      |

**Child: POS Guided Recommendation**

| Field       | Type         | Description                    |
|-------------|--------------|--------------------------------|
| item_code   | Link→Item    |                                |
| item_name   | Data         |                                |
| rank        | Int          | 1 = best match                 |
| match_score | Percent      | How well it fits preferences   |
| reason      | Small Text   | Why recommended                |

#### 2.4.4 POS Comparison Request

**Purpose:** Cache AI comparison results to avoid re-calling AI for the same items.  
**Why custom:** AI results need to be stored, versioned, and served quickly.

| Field                | Type            | Description                              |
|----------------------|-----------------|------------------------------------------|
| naming_series        | Select          | PCR-.YYYY.-.#####                        |
| items                | Table→POS Comparison Item | Items being compared (2-3)       |
| customer_preferences | JSON            | Budget, priorities as structured data     |
| comparison_result    | JSON            | Full AI response (specs table, pros/cons) |
| recommendation       | Text            | AI's recommended pick + reason            |
| source               | Select          | AI / Static Fallback                      |
| ai_model             | Data            | Which model generated it                  |
| ai_latency_ms        | Int             | Response time tracking                    |
| created_at_store     | Link→Warehouse  |                                           |

**Child: POS Comparison Item**

| Field       | Type       | Description         |
|-------------|------------|---------------------|
| item_code   | Link→Item  |                     |
| item_name   | Data       |                     |
| model       | Link→CH Model |                  |

#### 2.4.5 POS Comparison Template

**Purpose:** Static fallback comparison data for popular item pairs when AI is unavailable.  
**Why custom:** Need offline-capable comparison for top sellers.

| Field              | Type            | Description                              |
|--------------------|-----------------|------------------------------------------|
| item_1             | Link→Item       |                                          |
| item_2             | Link→Item       |                                          |
| item_3             | Link→Item       | Optional third item                      |
| comparison_data    | JSON            | Same schema as AI output                 |
| recommendation     | Text            | Static recommendation                    |
| last_reviewed      | Date            | When staff last verified this            |

#### 2.4.6 POS Repair Intake

**Purpose:** Quick-entry form at POS counter to capture device + issue before creating a full Service Request. Minimizes counter time.  
**Why custom:** Service Request has 82 fields — too heavy for a POS counter interaction. This captures the minimum, then auto-creates the Service Request.

| Field               | Type             | Description                              |
|---------------------|------------------|------------------------------------------|
| naming_series       | Select           | PRI-.YYYY.-.#####                        |
| store               | Link→Warehouse   |                                          |
| customer            | Link→Customer    |                                          |
| customer_name       | Data             | Fetched                                  |
| customer_phone      | Data             | For contact                              |
| device_category     | Link→CH Sub Category | Smartphone/Laptop/Tablet             |
| device_brand        | Data             | Quick entry, not necessarily linked      |
| device_model        | Data             | Quick entry                              |
| serial_no           | Link→Serial No   | If available                            |
| imei_number         | Data             | Manual entry                             |
| issue_description   | Small Text       | Customer's complaint in brief            |
| issue_category      | Link→Issue Category | Primary issue                         |
| mode_of_service     | Select           | Walk-in / Pickup / Courier               |
| password_pattern    | Data             | Device unlock info                       |
| status              | Select           | Draft / Converted / Cancelled            |
| service_request     | Link→Service Request | Auto-created on submit                |

On submit, this doctype auto-creates a gofix Service Request with all mapped fields.

#### 2.4.7 POS AI Settings (Single DocType)

**Purpose:** Central configuration for all AI features.

| Field                    | Type     | Description                                     |
|--------------------------|----------|-------------------------------------------------|
| enable_ai                | Check    | Global kill switch                               |
| ai_provider              | Select   | OpenAI / Anthropic / Google / Custom             |
| api_endpoint             | Data     | Base URL                                         |
| api_key                  | Password | Encrypted                                        |
| comparison_model         | Data     | e.g. "gpt-4o"                                   |
| comparison_system_prompt | Long Text| System prompt for comparison                     |
| upsell_system_prompt     | Long Text| System prompt for upsell                         |
| offer_explain_prompt     | Long Text| System prompt for offer explanation              |
| max_tokens               | Int      | Per request                                      |
| timeout_sec              | Int      | Default 10                                       |
| fallback_to_static       | Check    | Use POS Comparison Template when AI fails        |
| cache_ttl_hours          | Int      | How long to reuse cached AI responses            |

### 2.5 Custom Fields on Core Doctypes

| Core DocType    | Custom Field              | Type           | Purpose                                     |
|-----------------|---------------------------|----------------|----------------------------------------------|
| POS Profile     | custom_pos_mode           | Select         | System / Tablet / Kiosk                      |
| POS Profile     | custom_store              | Link→CH Store  | Link to CH Store for capability checks       |
| POS Invoice     | custom_kiosk_token        | Link→POS Kiosk Token | If converted from token               |
| POS Invoice     | custom_guided_session     | Link→POS Guided Session | Session that led to this sale        |
| POS Invoice     | custom_is_margin_scheme   | Check          | Any item uses margin GST                     |
| POS Invoice     | custom_repair_intake      | Link→POS Repair Intake | If repair was initiated               |
| POS Invoice     | custom_exchange_assessment| Link→Buyback Assessment | If exchange/trade-in involved        |
| POS Invoice Item| custom_warranty_plan      | Link→CH Warranty Plan | Warranty sold with this item           |
| POS Invoice Item| custom_is_margin_item     | Check          | This line uses margin scheme GST             |
| POS Invoice Item| custom_taxable_value      | Currency       | Margin amount (selling - purchase)           |
| POS Invoice Item| custom_exempted_value     | Currency       | Exempt portion under margin scheme           |
| Sales Invoice   | custom_exchange_credit    | Currency       | Trade-in credit applied                      |

---

## 3. POS Flows

### 3.1 System POS (Cash Counter) — Retail Sale

```
STEP  ACTION                                 DOCTYPE CREATED/UPDATED
─────────────────────────────────────────────────────────────────────
1     Staff opens POS, selects profile        POS Opening Entry (submit)
      "{Store} - Counter"

2     Customer arrives. Staff scans barcode   —
      or searches item by name/code/model

3     Item added to cart. System:             —
      a) Fetches CH Item Price (POS channel)
         for selling_price
      b) Evaluates Pricing Rules for auto
         discounts (from CH Item Offer sync)
      c) Shows applicable offers on screen
      d) Checks stock in store warehouse

4     If customer wants warranty/VAS plan:    —
      Staff selects CH Warranty Plan from
      dropdown on the item row

5     If exchange/trade-in:                   Buyback Assessment (via API)
      Staff opens exchange panel →            or link existing assessment
      quick grading or links existing
      Buyback Assessment → credit applied
      as negative line or discount

6     If customer has Kiosk Token:            —
      Staff scans/enters token number →
      cart auto-populated from token items
      POS Kiosk Token status → Converted

7     Cart finalized. Staff selects           —
      payment mode(s): Cash / Card / UPI
      Split payment supported natively

8     Submit                                  POS Invoice (submit)
                                              → Sales Invoice (auto)
                                              → GL Entries (auto)
                                              → Stock Ledger (auto)
                                              → CH Serial Lifecycle → "Sold"
                                              → CH Customer Device (created)
                                              → CH Loyalty Transaction (Earn)
                                              → CH Customer Store Visit (Purchase)

9     Print thermal receipt                   —

10    End of shift                            POS Closing Entry (submit)
```

### 3.2 System POS — Repair Intake

```
STEP  ACTION                                 DOCTYPE
─────────────────────────────────────────────────────
1     Customer walks in with broken device
      Staff clicks "Repair" in POS toolbar

2     Quick intake dialog opens:              —
      - Customer (search/create)
      - Device brand, model, serial/IMEI
      - Issue category + brief description
      - Mode: Walk-in

3     Staff submits intake                    POS Repair Intake (submit)
                                              → Service Request (auto-created)

4     If parts/service fee to collect now:    POS Invoice (advance payment)
      Staff can create POS Invoice for
      the estimated repair charge

5     Service Request follows gofix           Sales Order (is_service_order=1)
      workflow from here                      → Job Sheet → QC → Delivery
```

### 3.3 Staff Tablet POS — Assisted Sale

```
STEP  ACTION                                 DOCTYPE
─────────────────────────────────────────────────────
1     Staff logs into tablet, profile         POS Session Log (created)
      "{Store} - Tablet" auto-assigned

2     Staff walks with customer to            —
      display area. Opens tablet POS.

3     OPTION A: Guided Selling                POS Guided Session (created)
      - "What are you looking for?"
        → Category (Phone/Laptop/Accessory)
      - "What's your budget?"
        → Range slider
      - "What matters most?"
        → Camera / Battery / Gaming /
          Business / Budget
      System filters CH Models by specs
      matching preferences and shows
      ranked results with images + prices

4     Customer picks 2-3 models to            POS Comparison Request (created)
      compare. Staff taps "Compare".
      AI generates:
      - Specs table
      - Pros/cons in plain language
      - Recommendation based on prefs
      (Fallback: POS Comparison Template)

5     OPTION B: Direct Search                 —
      Staff searches by name, brand,
      barcode, model. Filters by RAM,
      storage, price range, condition,
      5G, etc.

6     Customer decides. Staff adds            —
      item to cart on tablet.

7     AI Upsell Panel shows:                  —
      "Customers who bought this also
      got: Tempered glass (₹299),
      Case (₹499), Extended warranty"

8     Staff can:                              —
      a) Complete sale on tablet              POS Invoice (if payment capable)
         (if payment device attached)
      b) Generate token for counter           POS Kiosk Token (created)
         Customer takes token to counter

9     Visit tracked                           CH Customer Store Visit
```

### 3.4 Customer Kiosk POS — Self-Service Discovery

```
STEP  ACTION                                 DOCTYPE
─────────────────────────────────────────────────────
1     Kiosk shows welcome screen              POS Session Log (created)
      "Tap to explore phones, laptops
       & accessories"

2     Customer selects category               POS Guided Session (created)
      (large tile buttons with images)

3     Guided questions (one per screen):      POS Guided Response (child rows)
      Q1: "What's your budget?"
          → Visual price range tiles
            (Under ₹10K, ₹10-20K, etc.)
      Q2: "What matters most to you?"
          → Icon tiles: Camera, Battery,
            Gaming, Business, Budget
      Q3: "Preferred brand?"
          → Brand logo tiles + "Any"
      Q4: "New, Refurbished, or Used?"
          → Condition tiles with price
            difference indicator

4     Results screen shows 4-8 items          POS Guided Recommendation
      ranked by match. Each card:             (child rows)
      - Image, Name, Price (all-inclusive)
      - Key specs (RAM, Storage, Battery)
      - "Best for Camera" / "Best Value"
        badges
      - Active offers highlighted

5     Customer taps "Compare" on 2-3          POS Comparison Request (created)
      items. Comparison screen shows:
      - Side-by-side specs table
      - AI pros/cons (or static fallback)
      - "Best for you" recommendation

6     Customer taps "I want this" on          POS Kiosk Token (created)
      selected item(s).
      Token screen shows:
      - Token number (large, bold)
      - QR code (staff scans to load)
      - "Show this to our staff at the
        counter"
      - Optional: Enter name/phone for
        SMS copy

7     Kiosk resets after 2 min idle           POS Guided Session status
      or customer taps "Done"                 → Completed / Abandoned

      ──── KIOSK CANNOT ────
      - Change prices
      - Apply manual discounts
      - Process payment
      - Access customer data
      - View cost prices
```

### 3.5 Returns & Exchanges

```
STEP  ACTION                                 DOCTYPE
─────────────────────────────────────────────────────
1     Customer brings item + receipt          —
      Staff opens original POS Invoice
      (search by name, phone, invoice#)

2     Staff clicks "Return"                   POS Invoice (return, qty negative)
      Selects items to return,                → Credit Note (auto)
      reason code                             → Stock reversal (auto)
                                              → CH Serial Lifecycle → "Returned"

3     If exchange (return + new purchase):
      a) Process return as above
      b) Create new POS Invoice               POS Invoice (new sale)
         Apply return credit as payment
         method "Credit Note" or adjust
         via custom_exchange_credit

4     If partial refund:                      Payment Entry (if cash back)
      Difference refunded to original
      payment method or cash
```

---

## 4. AI Integration Specification

### 4.1 Product Comparison

**System Prompt (stored in POS AI Settings):**

```
You are a mobile device expert assistant for a retail store. You help 
customers compare phones, laptops, and tablets.

Rules:
- Be objective and factual about specs.
- Write pros/cons in simple language a non-technical person can understand.
- Never mention competitor stores or pricing from other retailers.
- Format output strictly as JSON matching the provided schema.
- Base recommendation on the customer's stated preferences.
- If preferences are not provided, recommend based on overall value.
- Keep each pro/con to one sentence.
- Maximum 4 pros and 3 cons per product.
```

**Input JSON Schema:**

```json
{
  "products": [
    {
      "item_code": "SM-IPH14PM-GRN-NEW",
      "name": "iPhone 14 Pro Max (Green, New)",
      "brand": "Apple",
      "price": 129900,
      "condition": "New",
      "specs": {
        "ram_gb": 6,
        "storage_gb": 256,
        "battery_mah": 4323,
        "display_size_inch": 6.7,
        "display_type": "Super Retina XDR OLED",
        "rear_camera_mp": 48,
        "front_camera_mp": 12,
        "processor": "A16 Bionic",
        "five_g": true,
        "weight_g": 240,
        "os": "iOS 16",
        "warranty_months": 12
      },
      "features": ["Dynamic Island", "Always-On Display", "Crash Detection"],
      "current_offers": ["₹5,000 exchange bonus on any old iPhone"]
    }
  ],
  "customer_preferences": {
    "budget_max": 140000,
    "priorities": ["camera", "battery"],
    "use_case": "photography",
    "brand_preference": null,
    "condition_preference": "New"
  }
}
```

**Output JSON Schema:**

```json
{
  "comparison_table": {
    "headers": ["Spec", "iPhone 14 Pro Max", "Samsung S23 Ultra", "Pixel 8 Pro"],
    "rows": [
      ["Price", "₹1,29,900", "₹1,24,999", "₹1,06,999"],
      ["RAM", "6 GB", "12 GB", "12 GB"],
      ["Storage", "256 GB", "256 GB", "256 GB"],
      ["Battery", "4,323 mAh", "5,000 mAh", "5,050 mAh"],
      ["Display", "6.7\" OLED", "6.8\" AMOLED", "6.7\" OLED"],
      ["Rear Camera", "48 MP", "200 MP", "50 MP"],
      ["5G", "Yes", "Yes", "Yes"],
      ["Warranty", "12 months", "12 months", "12 months"]
    ]
  },
  "product_analysis": [
    {
      "item_code": "SM-IPH14PM-GRN-NEW",
      "pros": [
        "Excellent 48MP camera with best-in-class video recording",
        "Smoothest performance with A16 chip",
        "Dynamic Island is unique and useful",
        "Strong resale value"
      ],
      "cons": [
        "Most expensive option",
        "Only 6GB RAM, less than competitors",
        "Smaller battery than Samsung and Pixel"
      ]
    }
  ],
  "recommendation": {
    "item_code": "SM-IPH14PM-GRN-NEW",
    "reason": "Since you prioritize camera quality for photography, the iPhone 14 Pro Max offers the best overall camera system with ProRAW, Cinematic mode, and Action mode. While the Samsung has a higher megapixel count, the iPhone's image processing is consistently rated top for real-world photos. It fits within your ₹1,40,000 budget."
  }
}
```

**Fallback:** When AI unavailable, load `POS Comparison Template` matching the same item pair (or closest). If no template exists, show specs-only table (no pros/cons/recommendation) built from CH Model spec_values.

### 4.2 Offer Explainer

**System Prompt:**

```
You are a friendly retail assistant. Explain the discounts and offers 
applied to a customer's shopping cart in simple, clear language.
One short paragraph, max 3 sentences. Use ₹ for currency.
Do not mention internal codes or rule names.
```

**Input:**

```json
{
  "cart_items": [
    {
      "item_name": "iPhone 14 Pro Max (Green, New)",
      "qty": 1,
      "original_price": 134900,
      "selling_price": 129900
    },
    {
      "item_name": "Tempered Glass for iPhone 14 Pro Max",
      "qty": 1,
      "original_price": 499,
      "selling_price": 299
    }
  ],
  "offers_applied": [
    {
      "offer_name": "iPhone Launch Offer",
      "type": "Flat Discount",
      "value": 5000,
      "applied_on": "iPhone 14 Pro Max (Green, New)"
    },
    {
      "offer_name": "Accessory Bundle",
      "type": "Percentage Discount",
      "value": 40,
      "applied_on": "Tempered Glass for iPhone 14 Pro Max"
    }
  ],
  "cart_total": 130199,
  "total_savings": 5200
}
```

**Output:**

```json
{
  "explanation": "You're saving ₹5,200 today! Your iPhone 14 Pro Max has a ₹5,000 launch discount, and because you're buying it with a tempered glass, the screen protector is 40% off at just ₹299 instead of ₹499."
}
```

**Fallback:** Template-based string: "You're saving ₹{total_savings} with {n} offer(s) applied."

### 4.3 Upsell Helper

**System Prompt:**

```
You are a helpful retail assistant. Given the customer's selected item,
suggest 2-3 relevant accessories or services they might need.
Be helpful, not pushy. Each suggestion: one sentence explaining why.
Return JSON only.
```

**Input:**

```json
{
  "selected_item": {
    "item_code": "SM-IPH14PM-GRN-NEW",
    "name": "iPhone 14 Pro Max (Green, New)",
    "category": "Smartphones",
    "brand": "Apple",
    "price": 129900
  },
  "available_accessories": [
    {"item_code": "ACC-TG-IPH14PM", "name": "Tempered Glass", "price": 499},
    {"item_code": "ACC-CASE-IPH14PM", "name": "Silicone Case", "price": 799},
    {"item_code": "ACC-CHRG-20W", "name": "20W Fast Charger", "price": 1499},
    {"item_code": "ACC-APOD-3", "name": "AirPods 3rd Gen", "price": 17900}
  ],
  "available_services": [
    {"plan_name": "1-Year Extended Warranty", "price": 4999},
    {"plan_name": "Screen Protection Plan", "price": 2999}
  ],
  "cart_items": []
}
```

**Output:**

```json
{
  "suggestions": [
    {
      "item_code": "ACC-TG-IPH14PM",
      "type": "accessory",
      "reason": "A tempered glass protects your Pro Max's beautiful display from scratches and drops — essential for a ₹1.3 lakh phone.",
      "priority": 1
    },
    {
      "item_code": "ACC-CASE-IPH14PM",
      "type": "accessory",
      "reason": "A slim case keeps the Green finish looking new without adding bulk.",
      "priority": 2
    },
    {
      "plan_name": "Screen Protection Plan",
      "type": "service",
      "reason": "For ₹2,999 you get hassle-free screen replacement for a year — screen repairs cost ₹25,000+.",
      "priority": 3
    }
  ]
}
```

**Fallback:** Rule-based suggestions from `CH Sub Category` → linked accessories Item Group. Show accessories from same brand + category with no AI text.

---

## 5. ERPNext Implementation Guidance

### 5.1 Custom Doctypes Summary

| # | DocType                    | Module    | Type       | Key Purpose                          |
|---|----------------------------|-----------|------------|--------------------------------------|
| 1 | POS Profile Extension      | POS Core  | Single-like| Per-profile mode settings            |
| 2 | POS Session Log            | POS Core  | Log        | Track tablet/kiosk sessions          |
| 3 | POS Kiosk Token            | POS Kiosk | Submittable| Quote token from kiosk               |
| 4 | POS Kiosk Token Item       | POS Kiosk | Child      | Items in token                       |
| 5 | POS Guided Session         | POS Kiosk | Document   | Guided selling session               |
| 6 | POS Guided Response        | POS Kiosk | Child      | Q&A pairs                            |
| 7 | POS Guided Recommendation  | POS Kiosk | Child      | Ranked results                       |
| 8 | POS Comparison Request     | POS AI    | Document   | AI comparison cache                  |
| 9 | POS Comparison Item        | POS AI    | Child      | Items in comparison                  |
| 10| POS Comparison Template    | POS AI    | Document   | Static fallback comparisons          |
| 11| POS AI Settings            | POS AI    | Single     | AI configuration                     |
| 12| POS Repair Intake          | POS Repair| Submittable| Quick repair entry at counter        |

### 5.2 Custom Fields on Core Doctypes

```python
# In ch_pos/hooks.py → fixtures or install script

# POS Profile
custom_fields = {
    "POS Profile": [
        {
            "fieldname": "custom_pos_mode",
            "fieldtype": "Select",
            "label": "POS Mode",
            "options": "System\nTablet\nKiosk",
            "insert_after": "company",
        },
        {
            "fieldname": "custom_store",
            "fieldtype": "Link",
            "label": "Store",
            "options": "CH Store",
            "insert_after": "custom_pos_mode",
        },
    ],
    "POS Invoice": [
        {
            "fieldname": "custom_kiosk_token",
            "fieldtype": "Link",
            "label": "Kiosk Token",
            "options": "POS Kiosk Token",
            "insert_after": "pos_profile",
            "read_only": 1,
        },
        {
            "fieldname": "custom_guided_session",
            "fieldtype": "Link",
            "label": "Guided Session",
            "options": "POS Guided Session",
            "insert_after": "custom_kiosk_token",
            "read_only": 1,
        },
        {
            "fieldname": "custom_is_margin_scheme",
            "fieldtype": "Check",
            "label": "Has Margin Scheme Items",
            "insert_after": "taxes_and_charges",
            "read_only": 1,
        },
        {
            "fieldname": "custom_repair_intake",
            "fieldtype": "Link",
            "label": "Repair Intake",
            "options": "POS Repair Intake",
            "insert_after": "custom_guided_session",
            "read_only": 1,
        },
        {
            "fieldname": "custom_exchange_assessment",
            "fieldtype": "Link",
            "label": "Exchange Assessment",
            "options": "Buyback Assessment",
            "insert_after": "custom_repair_intake",
            "read_only": 1,
        },
    ],
    "POS Invoice Item": [
        {
            "fieldname": "custom_warranty_plan",
            "fieldtype": "Link",
            "label": "Warranty Plan",
            "options": "CH Warranty Plan",
            "insert_after": "item_code",
        },
        {
            "fieldname": "custom_is_margin_item",
            "fieldtype": "Check",
            "label": "Margin Scheme",
            "insert_after": "amount",
            "read_only": 1,
        },
        {
            "fieldname": "custom_taxable_value",
            "fieldtype": "Currency",
            "label": "Taxable Value (Margin)",
            "insert_after": "custom_is_margin_item",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_margin_item",
        },
        {
            "fieldname": "custom_exempted_value",
            "fieldtype": "Currency",
            "label": "Exempted Value",
            "insert_after": "custom_taxable_value",
            "read_only": 1,
            "depends_on": "eval:doc.custom_is_margin_item",
        },
    ],
}
```

### 5.3 Client Scripts

#### 5.3.1 POS Invoice Client Script (`custom/pos_invoice.js`)

**What it does:**
- On item add: checks if item's condition variant is "Used" or "Refurbished" → sets `custom_is_margin_item = 1`, calculates `custom_taxable_value` as selling_price minus last purchase rate (incoming_rate)
- On item add: fetches applicable CH Item Offers and displays them in an indicator strip below the item row
- On payment: if `custom_exchange_assessment` is linked, applies the buyback credit as a negative adjustment
- Shows "Repair" and "Exchange" action buttons in the POS toolbar when profile has those features enabled (from POS Profile Extension)
- On kiosk token scan: calls `ch_pos.api.search.load_kiosk_token` to populate cart

#### 5.3.2 POS Extensions (`public/js/pos_extensions.js`)

**What it does:**
- Extends the standard ERPNext POS Item Selector to:
  - Add category/subcategory filter tabs using CH Category / CH Sub Category
  - Add attribute filter sidebar (RAM, Storage, Price Range, Condition, Brand) reading from CH Sub Category specs
  - Show CH Item Price (POS channel) selling_price instead of standard item_price
  - Show stock badge with real-time qty from store warehouse
  - Show offer badges on item tiles
- Extends POS Item Details to show:
  - Warranty plan selector dropdown
  - Available offers for selected item
  - AI upsell suggestions panel (if enabled)
- Extends POS Payment to show:
  - AI offer explainer text
  - Total savings summary

### 5.4 Server Scripts / Whitelisted Methods

#### 5.4.1 `ch_pos.api.search`

```python
@frappe.whitelist()
def pos_item_search(
    search_term: str,
    pos_profile: str,
    filters: dict | None = None,
    page: int = 0,
    page_size: int = 20,
) -> dict:
    """
    Unified POS search endpoint.
    
    Searches across: item_code, item_name, barcode, CH Model name,
    brand, OEM part number.
    
    Filters (optional):
      category, sub_category, brand, condition,
      price_min, price_max, ram_gb, storage_gb,
      has_5g, has_offers, in_stock_only
    
    Returns:
      {
        "items": [
          {
            "item_code", "item_name", "image", "brand",
            "selling_price", "mrp", "stock_qty",
            "condition", "specs": {...},
            "offers": [{"name", "description", "value"}],
            "warranty_plans": [{"name", "plan_name", "price"}]
          }
        ],
        "total": int,
        "filters_available": {
          "brands": [...], "conditions": [...],
          "ram_options": [...], "storage_options": [...]
        }
      }
    """

@frappe.whitelist()
def load_kiosk_token(token: str) -> dict:
    """
    Load a POS Kiosk Token and return its items for cart population.
    Validates token is Active and not expired.
    Returns: {"items": [...], "customer_name": str, "customer_phone": str}
    """

@frappe.whitelist()
def get_item_detail_for_pos(
    item_code: str, warehouse: str, price_list: str
) -> dict:
    """
    Detailed item info for POS item detail panel.
    Returns: specs, features, offers, warranty options, stock,
             incoming_rate (if user has permission), margin info.
    """
```

#### 5.4.2 `ch_pos.api.guided`

```python
@frappe.whitelist()
def get_guided_questions(sub_category: str) -> list[dict]:
    """
    Return guided selling questions for a sub-category.
    Built from CH Sub Category specs + static discovery questions
    (budget, priority, brand preference, condition).
    Returns: [{"question", "type": "range|choice|multi", "options": [...]}]
    """

@frappe.whitelist()
def get_guided_recommendations(
    sub_category: str,
    responses: list[dict],
    warehouse: str,
    limit: int = 8,
) -> list[dict]:
    """
    Given guided session responses, return ranked item recommendations.
    Filters by stock availability in warehouse.
    Scores items by match to preferences (budget fit, spec match).
    Returns: [{"item_code", "item_name", "price", "match_score", 
               "reason", "specs", "offers"}]
    """
```

#### 5.4.3 `ch_pos.api.ai`

```python
@frappe.whitelist()
def compare_items(
    item_codes: list[str],
    customer_preferences: dict | None = None,
) -> dict:
    """
    Generate AI comparison for 2-3 items.
    Checks cache first (POS Comparison Request with same items).
    Falls back to POS Comparison Template if AI fails.
    Falls back to specs-only table if no template.
    Returns: comparison_table, product_analysis, recommendation
    """

@frappe.whitelist()
def get_upsell_suggestions(
    item_code: str,
    cart_items: list[str] | None = None,
) -> list[dict]:
    """
    AI upsell suggestions for an item.
    Loads available accessories from same sub-category.
    Loads warranty/VAS plans from CH Warranty Plan.
    Returns: [{"item_code/plan_name", "type", "reason", "price"}]
    """

@frappe.whitelist()
def explain_offers(cart: dict) -> str:
    """
    AI-generated plain-language explanation of applied offers.
    Returns: explanation string
    """
```

#### 5.4.4 `ch_pos.api.offers`

```python
@frappe.whitelist()
def get_applicable_offers(
    item_code: str | None = None,
    item_group: str | None = None,
    cart_total: float = 0,
    payment_mode: str | None = None,
) -> list[dict]:
    """
    Return all CH Item Offers applicable to an item or cart.
    Checks: channel=POS, date validity, conditions (min_bill, payment mode).
    Returns: [{"offer_name", "offer_type", "value", "description",
               "conditions_text"}]
    """

@frappe.whitelist()
def get_best_offer_combination(cart_items: list[dict]) -> dict:
    """
    Given cart items, find the best combination of non-conflicting offers.
    Uses priority and stackability rules from CH Item Offer.
    Returns: {"offers": [...], "total_savings": float, "explanation": str}
    """
```

#### 5.4.5 `ch_pos.api.repair`

```python
@frappe.whitelist()
def create_repair_intake(data: dict) -> dict:
    """
    Create POS Repair Intake and auto-generate Service Request.
    Maps minimal POS fields to full Service Request fields.
    Returns: {"intake_name", "service_request_name"}
    """
```

### 5.5 Hooks

```python
# ch_pos/hooks.py

app_name = "ch_pos"
app_title = "CH POS"
app_publisher = "GoFix"
app_description = "POS solution for GoGizmo & GoFix retail stores"

required_apps = ["frappe", "erpnext", "ch_item_master"]

# Modules
modules = [
    {"module_name": "POS Core", "type": "module"},
    {"module_name": "POS Kiosk", "type": "module"},
    {"module_name": "POS AI", "type": "module"},
    {"module_name": "POS Repair", "type": "module"},
]

# Override POS Invoice for margin scheme calculation
override_doctype_class = {
    "POS Invoice": "ch_pos.overrides.pos_invoice.CustomPOSInvoice",
}

# Client scripts
doctype_js = {
    "POS Invoice": "custom/pos_invoice.js",
}

# App-level JS (extends POS UI)
app_include_js = [
    "/assets/ch_pos/js/pos_extensions.js",
]

# Doc events
doc_events = {
    "POS Invoice": {
        "validate": "ch_pos.overrides.pos_invoice.validate_margin_scheme",
        "on_submit": [
            "ch_pos.overrides.pos_invoice.create_customer_device_records",
            "ch_pos.overrides.pos_invoice.update_serial_lifecycle",
            "ch_pos.overrides.pos_invoice.record_store_visit",
            "ch_pos.overrides.pos_invoice.earn_loyalty_points",
            "ch_pos.overrides.pos_invoice.update_kiosk_token_status",
        ],
        "on_cancel": [
            "ch_pos.overrides.pos_invoice.reverse_customer_device_records",
            "ch_pos.overrides.pos_invoice.reverse_serial_lifecycle",
        ],
    },
    "POS Closing Entry": {
        "on_submit": "ch_pos.overrides.pos_closing.log_session_end",
    },
}

# Scheduler
scheduler_events = {
    "hourly": [
        "ch_pos.pos_kiosk.doctype.pos_kiosk_token.pos_kiosk_token.expire_old_tokens",
    ],
}

# Fixtures (install custom fields)
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [["module", "=", "CH POS"]],
    },
]

# Website pages for kiosk and tablet
website_route_rules = [
    {"from_route": "/pos-kiosk", "to_route": "pos_kiosk"},
    {"from_route": "/pos-tablet", "to_route": "pos_tablet"},
]
```

### 5.6 POS Invoice Override (`overrides/pos_invoice.py`)

```python
Key logic in CustomPOSInvoice:

validate_margin_scheme():
    For each item in the invoice:
    - If item's Item record has attribute "Condition" = "Used" or "Refurbished":
      - Set custom_is_margin_item = 1
      - Fetch incoming_rate (last purchase rate) from Stock Ledger
      - custom_taxable_value = max(0, selling_rate - incoming_rate)
      - custom_exempted_value = selling_rate - custom_taxable_value
      - Recalculate tax rows:
        Tax applies ONLY on custom_taxable_value, not full amount
      - Set custom_is_margin_scheme = 1 on parent
    
    # Reuse the proven calculation pattern from ch_erp15's
    # CustomPurchaseOrder._calculate_marginal_taxes() but for
    # the selling side.

create_customer_device_records():
    For each serialized item sold:
    - Create CH Customer Device linking serial_no → customer
    - If custom_warranty_plan is set, populate warranty fields

update_serial_lifecycle():
    For each serialized item:
    - Update CH Serial Lifecycle → status = "Sold"
    - Add lifecycle_log entry with invoice reference

record_store_visit():
    Create CH Customer Store Visit:
    - visit_type = "Purchase"
    - reference_doctype = "POS Invoice"
    - reference_name = invoice name
    - store = from POS Profile's warehouse

earn_loyalty_points():
    If customer has loyalty enrolled:
    - Calculate points based on invoice total
    - Create CH Loyalty Transaction (type=Earn)

update_kiosk_token_status():
    If custom_kiosk_token is set:
    - Set POS Kiosk Token status = "Converted"
    - Set converted_invoice = invoice name
```

### 5.7 Migration, Testing & Upgrade Safety

**Migrations:**
- All custom fields created via fixtures (auto-applied on `bench migrate`)
- New doctypes created via standard Frappe migration
- No core file modifications — pure hooks + overrides + client scripts
- Data patches in `ch_pos/patches/` following Frappe convention

**Testing strategy:**
- Unit tests for margin scheme calculation (reuse ch_erp15's test patterns)
- Unit tests for offer resolution logic
- Integration tests: POS Invoice → Customer Device → Serial Lifecycle chain
- API tests: search, guided selling, kiosk token lifecycle
- AI tests: mock AI responses, verify fallback behavior

**Upgrade safety:**
- No monkey-patching of core POS JS (use `after_include` pattern or override specific methods via frappe.ui.form.on extensions)
- Custom fields are in separate module namespace — won't conflict
- Override class extends base `POSInvoice` — picks up upstream changes
- `required_apps` ensures dependency order

---

## 6. Security, Permissions & Data Integrity

### 6.1 Roles

| Role                | Description                                      |
|---------------------|--------------------------------------------------|
| POS Cashier         | System POS user at counter                       |
| POS Staff           | Tablet POS user (floor sales)                    |
| POS Kiosk           | Service account for kiosk devices                |
| POS Manager         | Store manager — full POS access                  |
| POS Admin           | Cross-store admin, AI settings, offer config     |

### 6.2 Permission Matrix

| DocType / Action          | Kiosk | Staff | Cashier | Manager | Admin |
|---------------------------|-------|-------|---------|---------|-------|
| **POS Invoice**           |       |       |         |         |       |
| - Create                  | —     | R/W*  | R/W     | R/W     | R/W   |
| - Submit                  | —     | —*    | Yes     | Yes     | Yes   |
| - Cancel                  | —     | —     | —       | Yes     | Yes   |
| - View cost/incoming rate | —     | —     | —       | Yes     | Yes   |
| - Change item rate        | —     | —     | —       | Yes     | Yes   |
| - Apply manual discount   | —     | —     | Config  | Yes     | Yes   |
| **POS Kiosk Token**       |       |       |         |         |       |
| - Create                  | Yes   | Yes   | —       | Yes     | Yes   |
| - Read                    | Own   | All   | All     | All     | All   |
| **POS Guided Session**    |       |       |         |         |       |
| - Create                  | Yes   | Yes   | —       | Yes     | Yes   |
| - Read                    | —     | Own   | —       | All     | All   |
| **POS Comparison Request**|       |       |         |         |       |
| - Create                  | Yes   | Yes   | —       | Yes     | Yes   |
| - Read                    | —     | Own   | —       | All     | All   |
| **POS Repair Intake**     |       |       |         |         |       |
| - Create                  | —     | —     | R/W     | R/W     | R/W   |
| - Submit                  | —     | —     | Yes     | Yes     | Yes   |
| **POS AI Settings**       |       |       |         |         |       |
| - Read/Write              | —     | —     | —       | —       | Yes   |
| **POS Profile Extension** |       |       |         |         |       |
| - Read/Write              | —     | —     | —       | Yes     | Yes   |
| **POS Comparison Template**|      |       |         |         |       |
| - Read/Write              | —     | —     | —       | Yes     | Yes   |
| **Customer data (view)**  | —     | Name  | Name+Ph | Full    | Full  |
| **CH Item Price (cost)**  | —     | —     | —       | View    | View  |
| **CH Item Offer (config)**| —     | —     | —       | —       | R/W   |

\* Staff on tablet can create invoice but typically generates a token instead. Submit depends on `POS Profile Extension.allow_submit_from_tablet`.

### 6.3 Kiosk Security

- **Dedicated user account**: `kiosk@{store}.local` with only POS Kiosk role
- **Session timeout**: Auto-logout after `kiosk_idle_timeout_sec` (default 120s)
- **No URL bar access**: Kiosk page is a full-screen SPA, no Frappe desk exposure
- **Rate limiting**: Max 10 AI comparison requests per kiosk per hour
- **No customer PII visible**: Kiosk cannot search or view customer records
- **No price editing**: All prices read-only from CH Item Price

### 6.4 Validation Rules

| Rule                                          | Where Enforced      |
|-----------------------------------------------|---------------------|
| Margin scheme GST only on Used/Refurbished    | POS Invoice validate|
| Cannot sell item with 0 stock in warehouse    | POS Invoice validate|
| Warehouse capacity check on incoming stock    | Existing ch_erp15   |
| Kiosk token expires after 30 minutes          | Scheduler (hourly)  |
| Only one active POS Opening per user+profile  | ERPNext core        |
| Manual discount requires Manager approval     | POS Invoice validate|
| Exchange credit cannot exceed item value      | POS Invoice validate|
| IMEI/Serial must exist for serialized items   | POS Invoice validate|
| Repair intake must have customer + issue      | POS Repair Intake validate |

---

## 7. Configuration Checklist

Follow this order when setting up ch_pos on a fresh instance.

### Phase 1: Prerequisites

- [ ] **1.1** Install required apps: `frappe`, `erpnext`, `india_compliance`, `ch_item_master`, `ch_erp15`, `gofix`
- [ ] **1.2** Install ch_pos: `bench get-app ch_pos && bench --site {site} install-app ch_pos`
- [ ] **1.3** Run migrate: `bench --site {site} migrate`
- [ ] **1.4** Verify custom fields created on POS Profile, POS Invoice, POS Invoice Item

### Phase 2: Masters Setup

- [ ] **2.1** CH Categories created (Phones, Laptops, Tablets, Accessories, Repairs)
- [ ] **2.2** CH Sub Categories created with specs defined (Smartphones, Feature Phones, etc.)
- [ ] **2.3** CH Models created with spec_values and features populated
- [ ] **2.4** Items created with variants (New/Refurbished/Used) and barcodes
- [ ] **2.5** CH Stores created with capability flags (`is_retail_enabled`, etc.)
- [ ] **2.6** Warehouses created per store (linked to Company)
- [ ] **2.7** CH Price Channel "POS" exists and linked to Price List "CH POS"
- [ ] **2.8** CH Item Prices set for POS channel (MRP, selling_price per item)

### Phase 3: Tax & Accounting

- [ ] **3.1** GST Tax Templates created:
  - Standard GST (for new items and accessories)
  - Margin Scheme GST (for used/refurbished devices)
- [ ] **3.2** Tax Category set up for margin scheme items
- [ ] **3.3** Cost Centers created per store
- [ ] **3.4** Accounting dimensions: store-wise P&L (GoFix vs GoGizmo split via Cost Center or custom dimension)

### Phase 4: Offers & Pricing

- [ ] **4.1** CH Item Offers created with POS channel
- [ ] **4.2** Verify Pricing Rules auto-synced from approved offers
- [ ] **4.3** Loyalty Program configured (earn rate, redeem rate)
- [ ] **4.4** CH Warranty Plans created (durations, prices, channels)

### Phase 5: POS Profiles

- [ ] **5.1** Create POS Profile: `{Store} - Counter`
  - Mode: System, Warehouse: store warehouse, Price List: CH POS
  - Payment methods: Cash, Card, UPI
  - Assigned to: POS Cashier users
- [ ] **5.2** Create POS Profile: `{Store} - Tablet`
  - Mode: Tablet, same warehouse/price list
  - Assigned to: POS Staff users
- [ ] **5.3** Create POS Profile: `{Store} - Kiosk`
  - Mode: Kiosk, same warehouse/price list
  - Assigned to: kiosk service account
- [ ] **5.4** Create POS Profile Extension for each profile
  - Counter: enable_repair_intake=1, enable_buyback_intake=1
  - Tablet: enable_guided_selling=1, enable_ai_comparison=1, enable_ai_upsell=1
  - Kiosk: enable_guided_selling=1, enable_ai_comparison=1, kiosk_idle_timeout_sec=120

### Phase 6: Roles & Users

- [ ] **6.1** Create roles: POS Cashier, POS Staff, POS Kiosk, POS Manager, POS Admin
- [ ] **6.2** Assign permissions per the matrix above
- [ ] **6.3** Create kiosk service accounts per store
- [ ] **6.4** Assign POS Cashier/Staff roles to store employees

### Phase 7: AI Configuration

- [ ] **7.1** Create POS AI Settings:
  - Enable AI, set provider and API key
  - Paste system prompts (comparison, upsell, offer explainer)
  - Set timeout (10s), cache TTL (24h), fallback enabled
- [ ] **7.2** Create POS Comparison Templates for top 10 popular item pairs
- [ ] **7.3** Test AI endpoints: `compare_items`, `get_upsell_suggestions`, `explain_offers`

### Phase 8: Build & Test

- [ ] **8.1** Build assets: `bench build --app ch_pos`
- [ ] **8.2** Test System POS flow: open → add items → payment → submit → verify GL + stock + serial lifecycle
- [ ] **8.3** Test margin scheme: sell a "Used" device → verify GST on margin only
- [ ] **8.4** Test Kiosk flow: guided session → compare → generate token
- [ ] **8.5** Test Tablet flow: search → filter → add to cart → generate token or submit
- [ ] **8.6** Test Repair intake: quick form → verify Service Request created
- [ ] **8.7** Test Exchange flow: link Buyback Assessment → verify credit applied
- [ ] **8.8** Test Offers: add item with active offer → verify discount applied + AI explanation

### Phase 9: Go-Live

- [ ] **9.1** Deploy to production
- [ ] **9.2** Configure kiosk devices (browser kiosk mode, URL to `/pos-kiosk`)
- [ ] **9.3** Configure tablets (browser shortcut to `/pos-tablet`)
- [ ] **9.4** Train cashiers on System POS workflow
- [ ] **9.5** Train floor staff on Tablet POS + guided selling
- [ ] **9.6** Monitor first-day POS Closing reconciliation per store

---

## Appendix A: Key Assumptions

| # | Assumption | Impact if Wrong |
|---|-----------|-----------------|
| A1 | Item Variants for condition (New/Refurb/Used) already exist via Item Attribute | Need to create attribute + regenerate variants |
| A2 | CH Item Price POS channel prices are maintained and current | POS will show stale/missing prices |
| A3 | ERPNext POS Profile's `applicable_for_users` is sufficient for kiosk lockdown | May need additional browser-level kiosk lockdown |
| A4 | Standard ERPNext POS UI is extensible enough via client scripts | May need custom Vue page for system POS too |
| A5 | AI service (OpenAI/Anthropic) is available with <2s response time | Fallback to static comparisons |
| A6 | gofix Service Request API is stable and accepts programmatic creation | May need to coordinate with gofix team |
| A7 | india_compliance handles standard GST; margin scheme needs custom logic | Already proven in ch_erp15 for purchases |
| A8 | One POS Opening/Closing cycle per shift per cashier (standard ERPNext) | No change needed |

## Appendix B: Margin Scheme GST — Selling Side Logic

Replicating ch_erp15's purchase-side marginal tax logic for the selling side:

```
For a Used device:
  Purchase price (incoming_rate): ₹40,000
  Selling price: ₹52,000
  Margin: ₹12,000

  GST treatment (18%):
    Taxable value: ₹12,000  (margin only)
    Exempted value: ₹40,000 (cost recovery — no GST)
    GST amount: ₹12,000 × 18% = ₹2,160
    
  Invoice shows:
    Item amount: ₹52,000
    Of which Taxable: ₹12,000
    Of which Exempt: ₹40,000
    GST: ₹2,160
    Grand Total: ₹52,000 (GST inclusive in MRP)
    
  Note: Under margin scheme, GST is INCLUDED in the selling price,
  not added on top. The ₹52,000 already contains ₹2,160 GST.
  Actual calculation:
    Margin = 52000 - 40000 = 12000
    GST = 12000 × 18/118 = ₹1,830.51 (extracted from inclusive price)
    Taxable margin = 12000 - 1830.51 = ₹10,169.49
```

**Assumption A7:** This mirrors the proven ch_erp15 approach. The `validate_margin_scheme` hook on POS Invoice will implement this extraction.
