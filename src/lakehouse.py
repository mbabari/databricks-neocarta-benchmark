"""Synthetic Unity Catalog lakehouse used to stress Text2SQL WITH vs WITHOUT Neocarta.

~260 tables across ODS / data-mart / landing / decoy schemas. Physical schema,
table, and column names are opaque enterprise legacy codes (ods_crm_01.t_0100,
dm_agg_10.a_1004, lnd_sls_raw.x_ord_2024) — the business meaning lives ONLY in
COMMENT metadata, so name-guessing fails and agents must either scan hundreds of
tables (WITHOUT) or retrieve by meaning through the Neo4j semantic layer (WITH).

Tables are authored below with readable logical names; `_apply_physical_naming`
renames everything (schemas, tables, columns, FKs, cross-references inside
comments) at build time. `seed_lakehouse.py` renders SQL and applies it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

YEARS = list(range(2016, 2026))
REGIONS = ("emea", "amer", "apac", "latam")

# Physical schema names (what actually exists in Unity Catalog).
SCHEMA_COMMENTS: dict[str, str] = {
    "lnd_sls_raw": (
        "Landing zone for raw sales/commerce extracts and unlabelled feed dumps. "
        "Uncleaned, duplicated, year- and region-sharded. Not for reporting."
    ),
    "lnd_svc_raw": (
        "Landing zone for raw customer-support extracts: ticket dumps, chats, "
        "CSAT surveys. Includes year and regional shards. Not the system of record."
    ),
    "lnd_fin_raw": (
        "Landing zone for raw finance extracts: invoices, GL journals, payments. "
        "Mixed currencies, voided documents included. Not for revenue reporting."
    ),
    "ods_crm_01": (
        "Operational data store 01 — cleaned, conformed CRM entities: customer "
        "master, contacts, accounts, paid subscription contracts. System of "
        "record for customer identity, commercial status, and subscription state."
    ),
    "ods_com_02": (
        "Operational data store 02 — cleaned commercial facts: orders, order "
        "lines, product catalog, shipments, returns."
    ),
    "ods_svc_03": (
        "Operational data store 03 — cleaned customer-support entities: tickets, "
        "agents, SLA breaches. System of record for support incidents."
    ),
    "dm_agg_10": (
        "Data mart 10 — governed executive analytics aggregates: customer 360, "
        "subscriber health, open critical incidents, bookings, churn, net "
        "revenue retention. Certified for reporting."
    ),
    "dm_fin_20": (
        "Data mart 20 — governed finance aggregates: annual recurring revenue "
        "(ARR), recognized revenue, unpaid invoices, dunning/collections. "
        "Certified for finance reporting."
    ),
    "arch_hist": (
        "Frozen historical snapshots (pre-2023). Never use for current ARR, "
        "active subscriptions, or open tickets."
    ),
    "etl_stg": (
        "Transient load area for the nightly ETL. Tables are truncated and "
        "reloaded; never query staging for business answers."
    ),
    "mkt_ods": (
        "Marketing store: campaigns, leads, email engagement. 'Customers' here "
        "are marketing audience members, not paying subscribers."
    ),
    "hcm_ods": (
        "Human-resources store. Tickets here are HR cases, not customer support. "
        "Employee records are internal — do not join to CRM."
    ),
}

# Logical (authoring) schema -> physical schema.
LOGICAL_TO_PHYS_SCHEMA: dict[str, str] = {
    "bronze_sales": "lnd_sls_raw",
    "bronze_support": "lnd_svc_raw",
    "bronze_finance": "lnd_fin_raw",
    "silver_crm": "ods_crm_01",
    "silver_sales": "ods_com_02",
    "silver_support": "ods_svc_03",
    "gold_analytics": "dm_agg_10",
    "gold_finance": "dm_fin_20",
    "archive": "arch_hist",
    "staging": "etl_stg",
    "marketing": "mkt_ods",
    "hr": "hcm_ods",
}

GOVERNED_LOGICAL = {"silver_crm", "silver_sales", "silver_support", "gold_analytics", "gold_finance"}

# Explicit physical codes for governed tables (fully opaque, mainframe-style).
PHYS_TABLES: dict[tuple[str, str], str] = {
    ("silver_crm", "customers"): "t_0100",
    ("silver_crm", "contacts"): "t_0110",
    ("silver_crm", "accounts"): "t_0120",
    ("silver_crm", "addresses"): "t_0130",
    ("silver_crm", "subscriptions"): "t_0140",
    ("silver_crm", "subscription_events"): "t_0141",
    ("silver_sales", "customers"): "t_0200",
    ("silver_sales", "product_categories"): "t_0210",
    ("silver_sales", "products"): "t_0220",
    ("silver_sales", "orders"): "t_0230",
    ("silver_sales", "order_items"): "t_0231",
    ("silver_sales", "shipments"): "t_0240",
    ("silver_sales", "returns"): "t_0250",
    ("silver_support", "customers"): "t_0300",
    ("silver_support", "agents"): "t_0310",
    ("silver_support", "tickets"): "t_0320",
    ("silver_support", "ticket_comments"): "t_0321",
    ("silver_support", "sla_breaches"): "t_0330",
    ("gold_analytics", "customer_360"): "a_1001",
    ("gold_analytics", "subscriber_health"): "a_1002",
    ("gold_analytics", "open_incidents"): "a_1003",
    ("gold_analytics", "subscriber_p1_incidents"): "a_1004",
    ("gold_analytics", "bookings_by_product_region_year"): "a_1005",
    ("gold_analytics", "churned_customers"): "a_1006",
    ("gold_analytics", "nrr_monthly"): "a_1007",
    ("gold_analytics", "product_usage"): "a_1008",
    ("gold_finance", "arr_by_product_region_year"): "f_2001",
    ("gold_finance", "unpaid_invoices"): "f_2002",
    ("gold_finance", "revenue_recognition"): "f_2003",
    ("gold_finance", "dunning_queue"): "f_2004",
    ("gold_finance", "arr_monthly"): "f_2005",
}

# Decoy schemas keep an abbreviated (still cryptic) legacy name with a prefix.
DECOY_PREFIX: dict[str, str] = {
    "bronze_sales": "x",
    "bronze_support": "x",
    "bronze_finance": "x",
    "archive": "h",
    "staging": "w",
    "marketing": "m",
    "hr": "e",
}

# Token-level abbreviation used for decoy table names and ALL column names.
ABBREV: dict[str, str] = {
    "customer": "cst", "customers": "cst", "cust": "cst",
    "order": "ord", "orders": "ord",
    "ticket": "tkt", "tickets": "tkt",
    "invoice": "inv", "invoices": "inv",
    "product": "prd", "products": "prd",
    "subscription": "sub", "subscriptions": "sub",
    "region": "rgn", "regional": "rgn",
    "status": "sts", "email": "eml", "name": "nm", "full": "fl",
    "country": "ctry", "amount": "amt", "date": "dt", "year": "yr",
    "month": "mo", "week": "wk", "priority": "prty", "subject": "subj",
    "opened": "opn", "open": "opn", "closed": "cls", "created": "crt",
    "updated": "upd", "at": "ts", "plan": "pln", "segment": "seg",
    "agent": "agt", "agents": "agt", "comment": "cmnt", "comments": "cmnt",
    "start": "strt", "started": "strt", "end": "end", "event": "ev", "events": "ev",
    "type": "typ", "category": "ctgy", "categories": "ctgy", "price": "prc",
    "list": "lst", "last": "lst", "line": "ln", "lines": "ln",
    "items": "itm", "item": "itm", "master": "mstr", "snapshot": "snap",
    "payments": "pmt", "payment": "pmt", "returns": "rtn", "return": "rtn",
    "refunds": "rfnd", "refund": "rfnd", "sessions": "sess", "session": "sess",
    "campaign": "cmpgn", "campaigns": "cmpgn", "employee": "empl",
    "employees": "empl", "department": "dept", "departments": "dept",
    "currency": "curr", "quantity": "qty", "shipment": "shpmt",
    "shipments": "shpmt", "shipped": "shpd", "address": "addr",
    "addresses": "addr", "account": "acct", "accounts": "acct",
    "contact": "cnt", "contacts": "cnt", "monthly": "mthly",
    "headcount": "hdcnt", "articles": "artcl", "usage": "usg",
    "users": "usr", "active": "actv", "bookings": "bkng", "booking": "bkng",
    "recognition": "recog", "recognized": "recog", "leads": "ld", "lead": "ld",
    "landing": "lndg", "batches": "btch", "churned": "chrn", "reason": "rsn",
    "carrier": "carr", "role": "role", "owner": "ownr", "kind": "knd",
    "city": "cty", "score": "scr", "team": "tm", "author": "authr",
    "body": "bdy", "title": "ttl", "transcript": "trnscrpt", "due": "due",
    "attempt": "atmpt", "breach": "brch", "breached": "brch", "breaches": "brch",
    "channel": "chnl", "source": "src", "qualified": "qlfd", "weight": "wgt",
    "anonymous": "anon", "website": "web", "touches": "tch", "touch": "tch",
    "sends": "snd", "send": "snd", "opens": "opns", "sent": "snt",
    "vendor": "vndr", "parent": "prnt", "days": "dys", "requests": "req",
    "incidents": "incdnt", "health": "hlth", "unpaid": "unpd",
    "revenue": "rev", "queue": "q", "load": "ld", "file": "fl",
}


@dataclass
class Column:
    name: str
    dtype: str
    nullable: bool = True
    comment: str | None = None
    pk: bool = False
    seeded: bool = True  # False = filler/ops column excluded from INSERT rows


@dataclass
class ForeignKey:
    column: str
    ref_schema: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    schema: str
    name: str
    comment: str
    columns: list[Column]
    fks: list[ForeignKey] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.name}"


def _col(
    name: str,
    dtype: str = "STRING",
    *,
    pk: bool = False,
    comment: str | None = None,
    nullable: bool | None = None,
) -> Column:
    if nullable is None:
        nullable = not pk
    return Column(name, dtype, nullable=nullable, comment=comment, pk=pk)


def _entity_cols(
    pk: str,
    extras: list[Column],
    *,
    with_ts: bool = True,
) -> list[Column]:
    cols = [_col(pk, "BIGINT", pk=True, comment=f"Primary key ({pk}).")]
    cols.extend(extras)
    if with_ts:
        ts_created = _col("created_at", "TIMESTAMP", comment="Row create timestamp.")
        ts_updated = _col("updated_at", "TIMESTAMP", comment="Row last-update timestamp.")
        ts_created.seeded = False
        ts_updated.seeded = False
        cols.append(ts_created)
        cols.append(ts_updated)
    return cols


def _ts(value: str) -> str:
    """Literal for Databricks TIMESTAMP."""
    return f"TIMESTAMP '{value}'"


def _bronze_landing(
    schema: str,
    name: str,
    entity: str,
    extra_note: str,
    extras: list[Column],
) -> Table:
    return Table(
        schema=schema,
        name=name,
        comment=(
            f"Bronze landing copy of {entity}. {extra_note} "
            "Not cleaned, not the system of record, do not use for governed metrics."
        ),
        columns=_entity_cols(f"{entity}_id", extras),
    )


def _abbr(name: str) -> str:
    """Abbreviate a snake_case identifier token-by-token (customer_id -> cst_id)."""
    tokens = [t for t in name.split("_") if t]
    return "_".join(ABBREV.get(t, t) for t in tokens)


def _feed_decoys() -> list[Table]:
    """Wide, unlabelled raw feed extracts — pure brute-force cost, zero signal."""
    specs = [
        ("bronze_sales", "SYS-COM", 40),
        ("bronze_support", "SYS-SVC", 35),
        ("bronze_finance", "SYS-FIN", 35),
    ]
    tables: list[Table] = []
    for schema, sys_code, count in specs:
        tag = sys_code.split("-")[1].lower()
        for i in range(1, count + 1):
            cols = [
                _col("rec_id", "BIGINT", pk=True),
                _col("src_sys_cd"),
                _col("etl_btch_id", "BIGINT"),
                _col("ld_ts", "TIMESTAMP"),
            ] + [_col(f"fld_{j:03d}") for j in range(1, 25)]
            tables.append(
                Table(
                    schema=schema,
                    name=f"feed_{tag}_{i:03d}",
                    comment=(
                        f"Raw fixed-width extract {i:03d} replicated from upstream "
                        f"{sys_code}. Field mapping lives in the retired ETL spec; "
                        "fields are unlabelled (fld_001..fld_024). Unvalidated, "
                        "mixed types, not for reporting."
                    ),
                    columns=cols,
                )
            )
    return tables


def _humanize(identifier: str) -> str:
    return identifier.replace("_", " ").strip().capitalize() + "."


def _apply_physical_naming(tables: list[Table]) -> list[Table]:
    """Rename schemas/tables/columns to opaque physical codes, in place.

    Business meaning is preserved exclusively in COMMENT text; cross-references
    inside comments (e.g. 'use gold_analytics.bookings_by_product_region_year')
    are rewritten to the new physical names so the metadata stays coherent.
    """
    # Pass 1: decide every table's physical schema + name.
    new_names: dict[tuple[str, str], tuple[str, str]] = {}
    taken: dict[str, set[str]] = {}
    logical_name_counts: dict[str, int] = {}
    for t in tables:
        logical_name_counts[t.name] = logical_name_counts.get(t.name, 0) + 1
    for t in tables:
        phys_schema = LOGICAL_TO_PHYS_SCHEMA[t.schema]
        used = taken.setdefault(phys_schema, set())
        key = (t.schema, t.name)
        if key in PHYS_TABLES:
            phys_name = PHYS_TABLES[key]
        else:
            base = f"{DECOY_PREFIX[t.schema]}_{_abbr(t.name)}"
            phys_name = base
            n = 1
            while phys_name in used:
                n += 1
                phys_name = f"{base}_{n:02d}"
        used.add(phys_name)
        new_names[key] = (phys_schema, phys_name)

    # Comment rewrite map: qualified refs, unique underscored bare names, schemas.
    replacements: dict[str, str] = {}
    for (ls, lt), (ps, pt) in new_names.items():
        replacements[f"{ls}.{lt}"] = f"{ps}.{pt}"
        if "_" in lt and logical_name_counts[lt] == 1:
            replacements[lt] = pt
    replacements.update(LOGICAL_TO_PHYS_SCHEMA)
    ordered = [
        (re.compile(rf"\b{re.escape(old)}\b"), new)
        for old, new in sorted(replacements.items(), key=lambda kv: len(kv[0]), reverse=True)
    ]

    def rewrite(text: str | None) -> str | None:
        if not text:
            return text
        for pattern, new in ordered:
            text = pattern.sub(new, text)
        return text

    # Pass 2: mutate tables.
    for t in tables:
        logical_schema, logical_name = t.schema, t.name
        governed = logical_schema in GOVERNED_LOGICAL
        t.schema, t.name = new_names[(logical_schema, logical_name)]
        t.comment = rewrite(t.comment)

        seen_cols: set[str] = set()
        for c in t.columns:
            orig = c.name
            new_col = _abbr(orig)
            n = 1
            while new_col in seen_cols:
                n += 1
                new_col = f"{_abbr(orig)}_{n}"
            seen_cols.add(new_col)
            c.name = new_col
            c.comment = rewrite(c.comment)
            if governed and not c.comment:
                c.comment = _humanize(orig)

        for fk in t.fks:
            fk.column = _abbr(fk.column)
            ref_key = (fk.ref_schema, fk.ref_table)
            if ref_key in new_names:
                fk.ref_schema, fk.ref_table = new_names[ref_key]
            fk.ref_column = _abbr(fk.ref_column)

        # Ops filler: silver ODS tables get ETL bookkeeping columns; decoys get
        # unlabelled filler so column listings are expensive to read.
        if logical_schema in {"silver_crm", "silver_sales", "silver_support"}:
            fillers = [
                _col("src_sys_cd", comment="Source system code (ETL bookkeeping)."),
                _col("etl_btch_id", "BIGINT", comment="ETL batch id."),
                _col("rec_eff_dt", "DATE", comment="Record effective date (SCD)."),
                _col("del_flg", "BOOLEAN", comment="Soft-delete flag."),
            ]
        elif not governed and not logical_name.startswith("feed_"):
            fillers = [_col(f"fld_{j:03d}") for j in range(101, 111)]
        else:
            fillers = []
        for f in fillers:
            f.seeded = False
            if f.name not in seen_cols:
                seen_cols.add(f.name)
                t.columns.append(f)
    return tables


def build_lakehouse() -> list[Table]:
    """Return every table in the demo catalog (order = create order)."""
    tables: list[Table] = []
    tables.extend(_silver_crm())
    tables.extend(_silver_sales())
    tables.extend(_silver_support())
    tables.extend(_gold_analytics())
    tables.extend(_gold_finance())
    tables.extend(_bronze_sales())
    tables.extend(_bronze_support())
    tables.extend(_bronze_finance())
    tables.extend(_archive())
    tables.extend(_staging())
    tables.extend(_marketing())
    tables.extend(_hr())
    tables.extend(_feed_decoys())
    return _apply_physical_naming(tables)


def schemas() -> list[str]:
    return list(SCHEMA_COMMENTS)


# ---------------------------------------------------------------------------
# Silver / gold (rich comments, PK/FK, seed rows that back demo questions)
# ---------------------------------------------------------------------------

def _silver_crm() -> list[Table]:
    customers = Table(
        schema="silver_crm",
        name="customers",
        comment=(
            "Cleaned paying-customer master (CRM system of record). One row per "
            "commercial customer. Use this for customer status (active / churned / "
            "trial), region, and identity. Do not use bronze, marketing, or HR "
            "tables named customers."
        ),
        columns=_entity_cols(
            "customer_id",
            [
                _col("email", comment="Unique business email."),
                _col("full_name", comment="Customer display name."),
                _col("country", comment="ISO country code."),
                _col(
                    "region",
                    comment="Sales region: EMEA, AMER, APAC, or LATAM.",
                ),
                _col(
                    "status",
                    comment=(
                        "Commercial status: active (paying), churned (cancelled), "
                        "or trial (not yet paying)."
                    ),
                ),
                _col("segment", comment="Enterprise, mid-market, or SMB."),
            ],
        ),
        rows=[
            (1, "alice@acme.example", "Alice Martin", "FR", "EMEA", "active", "enterprise"),
            (2, "bob@globex.example", "Bob Chen", "US", "AMER", "active", "mid-market"),
            (3, "carol@initech.example", "Carol Diaz", "DE", "EMEA", "churned", "enterprise"),
            (4, "dan@umbrella.example", "Dan Okonkwo", "NG", "APAC", "active", "smb"),
            (5, "eve@soylent.example", "Eve Rossi", "IT", "EMEA", "active", "enterprise"),
            (6, "frank@hooli.example", "Frank Liu", "US", "AMER", "churned", "smb"),
            (7, "grace@piedpiper.example", "Grace Kim", "KR", "APAC", "trial", "mid-market"),
            (8, "hiro@nakatomi.example", "Hiro Tanaka", "JP", "APAC", "active", "enterprise"),
        ],
    )
    contacts = Table(
        schema="silver_crm",
        name="contacts",
        comment="Cleaned CRM contacts (people) belonging to a customer account.",
        columns=_entity_cols(
            "contact_id",
            [
                _col("customer_id", "BIGINT", nullable=False, comment="FK to silver_crm.customers."),
                _col("email", comment="Contact email."),
                _col("role", comment="Champion, economic buyer, or user."),
            ],
        ),
        fks=[ForeignKey("customer_id", "silver_crm", "customers", "customer_id")],
        rows=[
            (11, 1, "alice@acme.example", "champion"),
            (12, 2, "bob@globex.example", "economic_buyer"),
            (13, 3, "carol@initech.example", "champion"),
            (14, 5, "eve@soylent.example", "champion"),
        ],
    )
    accounts = Table(
        schema="silver_crm",
        name="accounts",
        comment="CRM parent accounts. Prefer customers for Text2SQL about paying subscribers.",
        columns=_entity_cols(
            "account_id",
            [
                _col("customer_id", "BIGINT", nullable=False),
                _col("account_name"),
                _col("owner_email"),
            ],
        ),
        fks=[ForeignKey("customer_id", "silver_crm", "customers", "customer_id")],
        rows=[
            (101, 1, "Acme SAS", "ae.emea@example.com"),
            (102, 2, "Globex Inc", "ae.amer@example.com"),
            (103, 5, "Soylent SpA", "ae.emea@example.com"),
        ],
    )
    addresses = Table(
        schema="silver_crm",
        name="addresses",
        comment="Billing and shipping addresses for CRM customers.",
        columns=_entity_cols(
            "address_id",
            [
                _col("customer_id", "BIGINT", nullable=False),
                _col("kind", comment="billing or shipping."),
                _col("city"),
                _col("country"),
            ],
        ),
        fks=[ForeignKey("customer_id", "silver_crm", "customers", "customer_id")],
        rows=[
            (201, 1, "billing", "Paris", "FR"),
            (202, 5, "billing", "Milan", "IT"),
        ],
    )
    subscriptions = Table(
        schema="silver_crm",
        name="subscriptions",
        comment=(
            "Paid subscription contracts. status=active means the customer is a "
            "current paying subscriber; status=cancelled is churned. mrr is monthly "
            "recurring revenue in USD. This is the system of record for 'active "
            "subscription' questions — not bronze orders or marketing leads."
        ),
        columns=_entity_cols(
            "subscription_id",
            [
                _col("customer_id", "BIGINT", nullable=False, comment="FK to silver_crm.customers."),
                _col("plan", comment="Product plan: platform, analytics, or support_plus."),
                _col(
                    "status",
                    comment="active (paying), cancelled (churned), or trialing.",
                ),
                _col("mrr", "DOUBLE", comment="Monthly recurring revenue in USD."),
                _col("start_date", "DATE", comment="Contract start date."),
                _col("end_date", "DATE", comment="Contract end; NULL if still active."),
                _col("region", comment="Contract region: EMEA, AMER, APAC, LATAM."),
            ],
        ),
        fks=[ForeignKey("customer_id", "silver_crm", "customers", "customer_id")],
        rows=[
            (301, 1, "platform", "active", 12000.0, "2023-01-01", None, "EMEA"),
            (302, 2, "analytics", "active", 4500.0, "2023-06-01", None, "AMER"),
            (303, 3, "platform", "cancelled", 8000.0, "2022-01-01", "2024-03-31", "EMEA"),
            (304, 4, "support_plus", "active", 900.0, "2024-02-01", None, "APAC"),
            (305, 5, "platform", "active", 15000.0, "2022-09-01", None, "EMEA"),
            (306, 6, "analytics", "cancelled", 700.0, "2023-01-01", "2024-06-30", "AMER"),
            (307, 7, "platform", "trialing", 0.0, "2025-01-01", None, "APAC"),
            (308, 8, "analytics", "active", 6000.0, "2024-01-01", None, "APAC"),
        ],
    )
    events = Table(
        schema="silver_crm",
        name="subscription_events",
        comment="Subscription lifecycle events (started, renewed, cancelled, plan_changed).",
        columns=_entity_cols(
            "event_id",
            [
                _col("subscription_id", "BIGINT", nullable=False),
                _col("event_type"),
                _col("event_date", "DATE"),
            ],
            with_ts=False,
        ),
        fks=[ForeignKey("subscription_id", "silver_crm", "subscriptions", "subscription_id")],
        rows=[
            (1, 305, "started", "2022-09-01"),
            (2, 303, "cancelled", "2024-03-31"),
            (3, 306, "cancelled", "2024-06-30"),
        ],
    )
    return [customers, contacts, accounts, addresses, subscriptions, events]


def _silver_sales() -> list[Table]:
    customers = Table(
        schema="silver_sales",
        name="customers",
        comment=(
            "Sales-conformed customer dimension (subset of CRM). Prefer "
            "silver_crm.customers as the system of record for subscriber status."
        ),
        columns=_entity_cols(
            "customer_id",
            [
                _col("email"),
                _col("full_name"),
                _col("region"),
            ],
        ),
        rows=[
            (1, "alice@acme.example", "Alice Martin", "EMEA"),
            (2, "bob@globex.example", "Bob Chen", "AMER"),
            (5, "eve@soylent.example", "Eve Rossi", "EMEA"),
            (8, "hiro@nakatomi.example", "Hiro Tanaka", "APAC"),
        ],
    )
    categories = Table(
        schema="silver_sales",
        name="product_categories",
        comment="Product category dimension for the cleaned sales mart.",
        columns=_entity_cols(
            "category_id",
            [_col("category_name", comment="platform, analytics, or services.")],
            with_ts=False,
        ),
        rows=[(1, "platform"), (2, "analytics"), (3, "services")],
    )
    products = Table(
        schema="silver_sales",
        name="products",
        comment=(
            "Cleaned product catalog used for bookings and ARR. SKU-level. "
            "Do not use bronze sku_master."
        ),
        columns=_entity_cols(
            "product_id",
            [
                _col("sku"),
                _col("product_name"),
                _col("category_id", "BIGINT", nullable=False),
                _col("list_price", "DOUBLE"),
            ],
        ),
        fks=[ForeignKey("category_id", "silver_sales", "product_categories", "category_id")],
        rows=[
            (10, "PLT-ENT", "Platform Enterprise", 1, 12000.0),
            (11, "ANL-PRO", "Analytics Pro", 2, 4500.0),
            (12, "SUP-PLUS", "Support Plus", 3, 900.0),
        ],
    )
    orders = Table(
        schema="silver_sales",
        name="orders",
        comment=(
            "Cleaned commercial orders (all years, all regions). Use gold bookings "
            "facts for 2024 EMEA rankings; this table is the conformed source."
        ),
        columns=_entity_cols(
            "order_id",
            [
                _col("customer_id", "BIGINT", nullable=False),
                _col("order_date", "DATE"),
                _col("status", comment="paid, refunded, or pending."),
                _col("amount", "DOUBLE", comment="Order amount in USD."),
                _col("currency"),
                _col("region"),
                _col("order_year", "INT", comment="Calendar year of the order."),
            ],
        ),
        fks=[ForeignKey("customer_id", "silver_sales", "customers", "customer_id")],
        rows=[
            (1001, 1, "2024-03-12", "paid", 24000.0, "USD", "EMEA", 2024),
            (1002, 5, "2024-07-01", "paid", 30000.0, "USD", "EMEA", 2024),
            (1003, 2, "2024-02-10", "paid", 9000.0, "USD", "AMER", 2024),
            (1004, 8, "2024-11-05", "paid", 12000.0, "USD", "APAC", 2024),
            (1005, 1, "2023-08-01", "paid", 18000.0, "USD", "EMEA", 2023),
        ],
    )
    items = Table(
        schema="silver_sales",
        name="order_items",
        comment="Cleaned order lines. Join to products for product-level bookings.",
        columns=_entity_cols(
            "order_item_id",
            [
                _col("order_id", "BIGINT", nullable=False),
                _col("product_id", "BIGINT", nullable=False),
                _col("qty", "INT"),
                _col("line_amount", "DOUBLE"),
            ],
            with_ts=False,
        ),
        fks=[
            ForeignKey("order_id", "silver_sales", "orders", "order_id"),
            ForeignKey("product_id", "silver_sales", "products", "product_id"),
        ],
        rows=[
            (1, 1001, 10, 2, 24000.0),
            (2, 1002, 10, 2, 24000.0),
            (3, 1002, 11, 1, 6000.0),
            (4, 1003, 11, 2, 9000.0),
            (5, 1004, 10, 1, 12000.0),
            (6, 1005, 10, 1, 18000.0),
        ],
    )
    shipments = Table(
        schema="silver_sales",
        name="shipments",
        comment="Physical/license shipments for cleaned orders.",
        columns=_entity_cols(
            "shipment_id",
            [
                _col("order_id", "BIGINT", nullable=False),
                _col("shipped_date", "DATE"),
                _col("carrier"),
            ],
        ),
        fks=[ForeignKey("order_id", "silver_sales", "orders", "order_id")],
        rows=[(1, 1001, "2024-03-13", "license")],
    )
    returns = Table(
        schema="silver_sales",
        name="returns",
        comment="Cleaned product returns. Do not confuse with bronze refunds.",
        columns=_entity_cols(
            "return_id",
            [
                _col("order_id", "BIGINT", nullable=False),
                _col("reason"),
                _col("amount", "DOUBLE"),
            ],
        ),
        fks=[ForeignKey("order_id", "silver_sales", "orders", "order_id")],
        rows=[],
    )
    return [customers, categories, products, orders, items, shipments, returns]


def _silver_support() -> list[Table]:
    customers = Table(
        schema="silver_support",
        name="customers",
        comment=(
            "Support-conformed customer dimension. Prefer silver_crm.customers "
            "for commercial status; use this only to join tickets to a customer_id."
        ),
        columns=_entity_cols(
            "customer_id",
            [_col("email"), _col("full_name"), _col("region")],
        ),
        rows=[
            (1, "alice@acme.example", "Alice Martin", "EMEA"),
            (2, "bob@globex.example", "Bob Chen", "AMER"),
            (5, "eve@soylent.example", "Eve Rossi", "EMEA"),
            (8, "hiro@nakatomi.example", "Hiro Tanaka", "APAC"),
        ],
    )
    agents = Table(
        schema="silver_support",
        name="agents",
        comment="Support agents who own tickets. Not HR employees.",
        columns=_entity_cols(
            "agent_id",
            [_col("agent_name"), _col("team", comment="tier1, tier2, or incident.")],
        ),
        rows=[
            (1, "Pat Lee", "incident"),
            (2, "Sam Ortiz", "tier2"),
        ],
    )
    tickets = Table(
        schema="silver_support",
        name="tickets",
        comment=(
            "Cleaned customer-support tickets (all years). priority P1 is critical. "
            "status=open means the incident is still unresolved. Includes the "
            "post-resolution CSAT (customer satisfaction) score for closed tickets, "
            "so use this for average-CSAT-by-year questions. This is the system "
            "of record for support-ticket questions — not bronze year shards, "
            "not HR tickets, not archive."
        ),
        columns=_entity_cols(
            "ticket_id",
            [
                _col("customer_id", "BIGINT", nullable=False),
                _col("agent_id", "BIGINT"),
                _col(
                    "priority",
                    comment="P1 critical, P2 high, P3 medium, P4 low.",
                ),
                _col("status", comment="open, pending, or closed."),
                _col("subject"),
                _col("opened_at", "TIMESTAMP"),
                _col("closed_at", "TIMESTAMP"),
                _col("csat", "DOUBLE", comment="Post-resolution CSAT 1–5; NULL if still open."),
                _col("region"),
                _col("ticket_year", "INT"),
            ],
        ),
        fks=[
            ForeignKey("customer_id", "silver_support", "customers", "customer_id"),
            ForeignKey("agent_id", "silver_support", "agents", "agent_id"),
        ],
        rows=[
            (401, 2, 1, "P1", "open", "Production outage", "2025-11-02 09:00:00", None, None, "AMER", 2025),
            (402, 5, 1, "P1", "open", "SSO login failures", "2025-12-01 14:00:00", None, None, "EMEA", 2025),
            (403, 1, 2, "P3", "closed", "Invoice PDF", "2024-04-01 10:00:00", "2024-04-02 12:00:00", 4.0, "EMEA", 2024),
            (404, 8, 2, "P2", "closed", "Slow dashboard", "2023-09-15 08:00:00", "2023-09-16 08:00:00", 3.0, "APAC", 2023),
            (405, 5, 2, "P4", "closed", "How to export CSV", "2023-02-01 11:00:00", "2023-02-01 15:00:00", 5.0, "EMEA", 2023),
        ],
    )
    comments = Table(
        schema="silver_support",
        name="ticket_comments",
        comment="Internal and public comments on cleaned support tickets.",
        columns=_entity_cols(
            "comment_id",
            [
                _col("ticket_id", "BIGINT", nullable=False),
                _col("author"),
                _col("body"),
            ],
        ),
        fks=[ForeignKey("ticket_id", "silver_support", "tickets", "ticket_id")],
        rows=[(1, 402, "Pat Lee", "Paging on-call for SSO outage.")],
    )
    sla = Table(
        schema="silver_support",
        name="sla_breaches",
        comment="Tickets that missed their SLA. Join to tickets for priority/status.",
        columns=_entity_cols(
            "breach_id",
            [
                _col("ticket_id", "BIGINT", nullable=False),
                _col("sla_name"),
                _col("breached_at", "TIMESTAMP"),
            ],
            with_ts=False,
        ),
        fks=[ForeignKey("ticket_id", "silver_support", "tickets", "ticket_id")],
        rows=[(1, 401, "P1_ack_15m", "2025-11-02 09:22:00")],
    )
    return [customers, agents, tickets, comments, sla]


def _gold_analytics() -> list[Table]:
    customer_360 = Table(
        schema="gold_analytics",
        name="customer_360",
        comment=(
            "Governed customer 360: one row per commercial customer with current "
            "subscription status, region, segment, and open-ticket counts "
            "including open critical (P1) tickets. The single wide dimension for "
            "executive customer questions — no joins needed."
        ),
        columns=[
            _col("customer_id", "BIGINT", pk=True),
            _col("full_name"),
            _col("email"),
            _col("region"),
            _col("segment"),
            _col("status", comment="active, churned, or trial."),
            _col("active_subscription", "BOOLEAN"),
            _col("mrr", "DOUBLE"),
            _col("open_tickets", "INT"),
            _col("open_p1_tickets", "INT"),
        ],
        fks=[ForeignKey("customer_id", "silver_crm", "customers", "customer_id")],
        rows=[
            (1, "Alice Martin", "alice@acme.example", "EMEA", "enterprise", "active", True, 12000.0, 0, 0),
            (2, "Bob Chen", "bob@globex.example", "AMER", "mid-market", "active", True, 4500.0, 1, 1),
            (3, "Carol Diaz", "carol@initech.example", "EMEA", "enterprise", "churned", False, 0.0, 0, 0),
            (4, "Dan Okonkwo", "dan@umbrella.example", "APAC", "smb", "active", True, 900.0, 0, 0),
            (5, "Eve Rossi", "eve@soylent.example", "EMEA", "enterprise", "active", True, 15000.0, 1, 1),
            (6, "Frank Liu", "frank@hooli.example", "AMER", "smb", "churned", False, 0.0, 0, 0),
            (7, "Grace Kim", "grace@piedpiper.example", "APAC", "mid-market", "trial", False, 0.0, 0, 0),
            (8, "Hiro Tanaka", "hiro@nakatomi.example", "APAC", "enterprise", "active", True, 6000.0, 0, 0),
        ],
    )
    subscriber_health = Table(
        schema="gold_analytics",
        name="subscriber_health",
        comment=(
            "Governed one-stop view of current paying subscribers: customer name, "
            "plan, and monthly recurring revenue (MRR) for every active "
            "subscription. Excludes trial and churned accounts. Use for 'who are "
            "our paying subscribers and what plan/MRR are they on' questions."
        ),
        columns=[
            _col("customer_id", "BIGINT", pk=True),
            _col("full_name"),
            _col("plan"),
            _col("mrr", "DOUBLE"),
            _col("region"),
            _col("status"),
        ],
        fks=[ForeignKey("customer_id", "silver_crm", "customers", "customer_id")],
        rows=[
            (1, "Alice Martin", "platform", 12000.0, "EMEA", "active"),
            (2, "Bob Chen", "analytics", 4500.0, "AMER", "active"),
            (4, "Dan Okonkwo", "support_plus", 900.0, "APAC", "active"),
            (5, "Eve Rossi", "platform", 15000.0, "EMEA", "active"),
            (8, "Hiro Tanaka", "analytics", 6000.0, "APAC", "active"),
        ],
    )
    open_incidents = Table(
        schema="gold_analytics",
        name="open_incidents",
        comment=(
            "Governed open customer-support incidents. Includes all priorities. "
            "For critical-only questions filter priority='P1' or use "
            "subscriber_p1_incidents."
        ),
        columns=[
            _col("ticket_id", "BIGINT", pk=True),
            _col("customer_id", "BIGINT", nullable=False),
            _col("priority"),
            _col("subject"),
            _col("opened_at", "TIMESTAMP"),
            _col("region"),
        ],
        fks=[
            ForeignKey("customer_id", "silver_crm", "customers", "customer_id"),
            ForeignKey("ticket_id", "silver_support", "tickets", "ticket_id"),
        ],
        rows=[
            (401, 2, "P1", "Production outage", "2025-11-02 09:00:00", "AMER"),
            (402, 5, "P1", "SSO login failures", "2025-12-01 14:00:00", "EMEA"),
        ],
    )
    p1 = Table(
        schema="gold_analytics",
        name="subscriber_p1_incidents",
        comment=(
            "Governed intersection: customers who currently have an ACTIVE paid "
            "subscription AND an OPEN critical (P1) support incident. This is the "
            "table to use for questions about active subscribers with open critical "
            "tickets. Do not use bronze ticket dumps or HR tickets."
        ),
        columns=[
            _col("customer_id", "BIGINT", pk=True),
            _col("full_name"),
            _col("email"),
            _col("region"),
            _col("plan"),
            _col("mrr", "DOUBLE"),
            _col("ticket_id", "BIGINT", nullable=False),
            _col("ticket_subject"),
            _col("opened_at", "TIMESTAMP"),
        ],
        fks=[
            ForeignKey("customer_id", "silver_crm", "customers", "customer_id"),
            ForeignKey("ticket_id", "silver_support", "tickets", "ticket_id"),
        ],
        rows=[
            (2, "Bob Chen", "bob@globex.example", "AMER", "analytics", 4500.0, 401, "Production outage", "2025-11-02 09:00:00"),
            (5, "Eve Rossi", "eve@soylent.example", "EMEA", "platform", 15000.0, 402, "SSO login failures", "2025-12-01 14:00:00"),
        ],
    )
    bookings = Table(
        schema="gold_analytics",
        name="bookings_by_product_region_year",
        comment=(
            "Governed bookings fact: paid order amount rolled up by product, sales "
            "region, and calendar year. Use this for '2024 EMEA bookings by product' "
            "— not bronze orders_2024, not orders_emea landing tables, not archive."
        ),
        columns=[
            _col("bookings_id", "BIGINT", pk=True),
            _col("product_id", "BIGINT", nullable=False),
            _col("product_name"),
            _col("region"),
            _col("booking_year", "INT"),
            _col("bookings_usd", "DOUBLE", comment="Sum of paid bookings in USD."),
        ],
        fks=[ForeignKey("product_id", "silver_sales", "products", "product_id")],
        rows=[
            (1, 10, "Platform Enterprise", "EMEA", 2024, 48000.0),
            (2, 11, "Analytics Pro", "EMEA", 2024, 6000.0),
            (3, 11, "Analytics Pro", "AMER", 2024, 9000.0),
            (4, 10, "Platform Enterprise", "APAC", 2024, 12000.0),
            (5, 10, "Platform Enterprise", "EMEA", 2023, 18000.0),
        ],
    )
    churned = Table(
        schema="gold_analytics",
        name="churned_customers",
        comment=(
            "Governed list of customers whose paid subscription is cancelled "
            "(churned). Join to gold_finance.unpaid_invoices for 'churned with "
            "unpaid invoices'."
        ),
        columns=[
            _col("customer_id", "BIGINT", pk=True),
            _col("full_name"),
            _col("email"),
            _col("region"),
            _col("churned_on", "DATE"),
            _col("last_plan"),
        ],
        fks=[ForeignKey("customer_id", "silver_crm", "customers", "customer_id")],
        rows=[
            (3, "Carol Diaz", "carol@initech.example", "EMEA", "2024-03-31", "platform"),
            (6, "Frank Liu", "frank@hooli.example", "AMER", "2024-06-30", "analytics"),
        ],
    )
    nrr = Table(
        schema="gold_analytics",
        name="nrr_monthly",
        comment="Governed net revenue retention by month and region.",
        columns=[
            _col("nrr_id", "BIGINT", pk=True),
            _col("month", "DATE"),
            _col("region"),
            _col("nrr_pct", "DOUBLE"),
        ],
        rows=[
            (1, "2024-12-01", "EMEA", 1.08),
            (2, "2024-12-01", "AMER", 0.97),
        ],
    )
    usage = Table(
        schema="gold_analytics",
        name="product_usage",
        comment="Weekly product-usage aggregates for active subscribers. Not bookings.",
        columns=[
            _col("usage_id", "BIGINT", pk=True),
            _col("customer_id", "BIGINT", nullable=False),
            _col("product_id", "BIGINT", nullable=False),
            _col("week_start", "DATE"),
            _col("active_users", "INT"),
        ],
        fks=[
            ForeignKey("customer_id", "silver_crm", "customers", "customer_id"),
            ForeignKey("product_id", "silver_sales", "products", "product_id"),
        ],
        rows=[(1, 5, 10, "2025-12-01", 84)],
    )
    return [
        customer_360,
        subscriber_health,
        open_incidents,
        p1,
        bookings,
        churned,
        nrr,
        usage,
    ]


def _gold_finance() -> list[Table]:
    arr = Table(
        schema="gold_finance",
        name="arr_by_product_region_year",
        comment=(
            "Governed annual recurring revenue (ARR) by product, sales region, and "
            "calendar year. ARR is 12 × active MRR at year end, in USD. This is the "
            "table for '2024 EMEA ARR by product'. Do not compute ARR from bronze "
            "invoices, GL entries, or order shards."
        ),
        columns=[
            _col("arr_id", "BIGINT", pk=True),
            _col("product_name"),
            _col("plan"),
            _col("region"),
            _col("arr_year", "INT"),
            _col("arr_usd", "DOUBLE", comment="Annual recurring revenue in USD."),
        ],
        rows=[
            (1, "Platform Enterprise", "platform", "EMEA", 2024, 324000.0),
            (2, "Analytics Pro", "analytics", "AMER", 2024, 54000.0),
            (3, "Analytics Pro", "analytics", "APAC", 2024, 72000.0),
            (4, "Support Plus", "support_plus", "APAC", 2024, 10800.0),
            (5, "Platform Enterprise", "platform", "EMEA", 2023, 288000.0),
        ],
    )
    unpaid = Table(
        schema="gold_finance",
        name="unpaid_invoices",
        comment=(
            "Governed open (unpaid) invoices. status is open or past_due. Join to "
            "gold_analytics.churned_customers to find churned accounts that still owe."
        ),
        columns=[
            _col("invoice_id", "BIGINT", pk=True),
            _col("customer_id", "BIGINT", nullable=False),
            _col("amount_due", "DOUBLE"),
            _col("currency"),
            _col("due_date", "DATE"),
            _col("status", comment="open or past_due."),
            _col("region"),
        ],
        fks=[ForeignKey("customer_id", "silver_crm", "customers", "customer_id")],
        rows=[
            (9001, 3, 16000.0, "USD", "2024-04-15", "past_due", "EMEA"),
            (9002, 6, 1400.0, "USD", "2024-07-15", "past_due", "AMER"),
            (9003, 8, 6000.0, "USD", "2025-12-31", "open", "APAC"),
        ],
    )
    revrec = Table(
        schema="gold_finance",
        name="revenue_recognition",
        comment="Monthly recognized revenue (not ARR, not bookings).",
        columns=[
            _col("revrec_id", "BIGINT", pk=True),
            _col("month", "DATE"),
            _col("region"),
            _col("recognized_usd", "DOUBLE"),
        ],
        rows=[(1, "2024-12-01", "EMEA", 27000.0)],
    )
    dunning = Table(
        schema="gold_finance",
        name="dunning_queue",
        comment="Invoices in collections / dunning. Subset of unpaid_invoices.",
        columns=[
            _col("dunning_id", "BIGINT", pk=True),
            _col("invoice_id", "BIGINT", nullable=False),
            _col("customer_id", "BIGINT", nullable=False),
            _col("attempt", "INT"),
        ],
        fks=[
            ForeignKey("invoice_id", "gold_finance", "unpaid_invoices", "invoice_id"),
            ForeignKey("customer_id", "silver_crm", "customers", "customer_id"),
        ],
        rows=[(1, 9001, 3, 3), (2, 9002, 6, 2)],
    )
    arr_m = Table(
        schema="gold_finance",
        name="arr_monthly",
        comment=(
            "Governed month-end ARR snapshot: point-in-time annual recurring "
            "revenue by month and region. Use for 'ARR snapshot as of month X' "
            "questions. Not a per-product split."
        ),
        columns=[
            _col("arr_month_id", "BIGINT", pk=True),
            _col("month", "DATE"),
            _col("region"),
            _col("arr_usd", "DOUBLE"),
        ],
        rows=[
            (1, "2024-12-01", "EMEA", 324000.0),
            (2, "2024-12-01", "AMER", 54000.0),
        ],
    )
    return [arr, unpaid, revrec, dunning, arr_m]


# ---------------------------------------------------------------------------
# Bronze / decoy generators (confusion volume)
# ---------------------------------------------------------------------------

def _std_customer_extras() -> list[Column]:
    return [
        _col("email"),
        _col("full_name"),
        _col("country"),
        _col("region"),
        _col("status"),
        _col("src_file", comment="Landing filename; ignore for analytics."),
    ]


def _std_order_extras() -> list[Column]:
    return [
        _col("customer_id", "BIGINT"),
        _col("order_date", "STRING", comment="Unparsed date string from the extract."),
        _col("status"),
        _col("amount", "STRING", comment="Unparsed amount; may include currency symbols."),
        _col("region"),
        _col("src_file"),
    ]


def _std_ticket_extras() -> list[Column]:
    return [
        _col("customer_id", "BIGINT"),
        _col("priority"),
        _col("status"),
        _col("subject"),
        _col("opened_at", "STRING"),
        _col("csat", "STRING"),
        _col("region"),
        _col("src_file"),
    ]


def _std_invoice_extras() -> list[Column]:
    return [
        _col("customer_id", "BIGINT"),
        _col("amount", "STRING"),
        _col("status"),
        _col("due_date", "STRING"),
        _col("region"),
        _col("src_file"),
    ]


def _bronze_sales() -> list[Table]:
    tables: list[Table] = []
    synonyms = {
        "customers": "customer records from the CRM nightly dump",
        "customer": "legacy singular customer extract (same grain as customers)",
        "cust_raw": "unparsed customer CSV from SFTP",
        "cust_master": "source-system customer master replica",
        "customers_raw": "second landing path for customer files",
        "orders": "commerce orders extract",
        "order_raw": "unparsed order JSON dump",
        "order_items": "order line extract",
        "line_items": "duplicate line extract from the warehouse feeder",
        "products": "product catalog dump",
        "sku_master": "ERP SKU master replica",
        "product_catalog": "marketing product catalog export",
        "returns": "return authorizations dump",
        "refunds": "payment-processor refund dump",
        "events": "clickstream events",
        "sessions": "web sessions",
        "carts": "abandoned carts",
        "pageviews": "raw page view hits",
    }
    extras_by_entity = {
        "customer": _std_customer_extras(),
        "order": _std_order_extras(),
        "product": [
            _col("sku"),
            _col("name"),
            _col("price", "STRING"),
            _col("src_file"),
        ],
        "return": [
            _col("order_id", "BIGINT"),
            _col("reason"),
            _col("amount", "STRING"),
            _col("src_file"),
        ],
        "event": [
            _col("session_id"),
            _col("event_name"),
            _col("ts", "STRING"),
            _col("src_file"),
        ],
        "session": [_col("user_id"), _col("started_at", "STRING"), _col("src_file")],
        "cart": [_col("customer_id", "BIGINT"), _col("items", "INT"), _col("src_file")],
        "pageview": [_col("url"), _col("ts", "STRING"), _col("src_file")],
        "sku": [_col("sku"), _col("desc"), _col("src_file")],
        "refund": [_col("order_id", "BIGINT"), _col("amount", "STRING"), _col("src_file")],
        "line": _std_order_extras(),
    }

    def entity_key(name: str) -> str:
        for key in extras_by_entity:
            if key in name:
                return key
        return "order"

    for name, note in synonyms.items():
        ek = entity_key(name)
        extras = extras_by_entity[ek]
        pk_name = "customer_id" if ek == "customer" else f"{ek}_id"
        if ek == "sku":
            pk_name = "sku_id"
        if ek == "line":
            pk_name = "line_id"
        if ek == "refund":
            pk_name = "refund_id"
        tables.append(
            Table(
                schema="bronze_sales",
                name=name,
                comment=(
                    f"Bronze landing: {note}. Do not use for ARR, bookings, or "
                    "active-subscriber questions."
                ),
                columns=_entity_cols(pk_name, extras),
            )
        )

    for year in YEARS:
        tables.append(
            Table(
                schema="bronze_sales",
                name=f"orders_{year}",
                comment=(
                    f"Bronze year-shard landing extract of commerce orders for "
                    f"calendar year {year} only (all regions). Uncleaned. Do not use "
                    "for governed 2024 EMEA bookings — use gold_analytics."
                    "bookings_by_product_region_year."
                ),
                columns=_entity_cols("order_id", _std_order_extras()),
            )
        )
        tables.append(
            Table(
                schema="bronze_sales",
                name=f"customers_{year}",
                comment=(
                    f"Bronze snapshot of customer records as of year {year}. "
                    "Historical; not current subscriber status."
                ),
                columns=_entity_cols("customer_id", _std_customer_extras()),
            )
        )
    for region in REGIONS:
        tables.append(
            Table(
                schema="bronze_sales",
                name=f"orders_{region}",
                comment=(
                    f"Bronze regional landing extract of orders for {region.upper()} "
                    f"only (all years). Uncleaned. Do not use for governed bookings."
                ),
                columns=_entity_cols("order_id", _std_order_extras()),
            )
        )
    return tables


def _bronze_support() -> list[Table]:
    tables: list[Table] = []
    base = {
        "tickets": "all-time support ticket dump",
        "ticket_raw": "unparsed Zendesk JSON",
        "ticket_events": "ticket audit-log dump",
        "agents": "support-agent roster dump",
        "kb_articles": "knowledge-base article dump",
        "chats": "chat transcript dump",
        "csat": "raw CSAT survey dump",
    }
    extras = {
        "ticket": _std_ticket_extras(),
        "agent": [_col("agent_name"), _col("team"), _col("src_file")],
        "kb": [_col("title"), _col("body"), _col("src_file")],
        "chat": [_col("ticket_id", "BIGINT"), _col("transcript"), _col("src_file")],
        "csat": [_col("ticket_id", "BIGINT"), _col("score", "STRING"), _col("src_file")],
    }

    def ek(name: str) -> str:
        for k in extras:
            if name.startswith(k):
                return k
        return "ticket"

    for name, note in base.items():
        k = ek(name)
        pk = f"{k}_id"
        tables.append(
            Table(
                schema="bronze_support",
                name=name,
                comment=(
                    f"Bronze landing: {note}. Do not use for open critical incidents "
                    "— use gold_analytics.subscriber_p1_incidents or silver_support.tickets."
                ),
                columns=_entity_cols(pk, extras[k]),
            )
        )
    for year in YEARS:
        tables.append(
            Table(
                schema="bronze_support",
                name=f"tickets_{year}",
                comment=(
                    f"Bronze year-shard landing extract of support tickets for "
                    f"calendar year {year} only (all regions), including raw CSAT. "
                    "Not the system of record for currently open P1 incidents."
                ),
                columns=_entity_cols("ticket_id", _std_ticket_extras()),
            )
        )
    for region in ("emea", "amer", "apac"):
        tables.append(
            Table(
                schema="bronze_support",
                name=f"tickets_{region}",
                comment=(
                    f"Bronze regional landing extract of support tickets for "
                    f"{region.upper()} only (all years)."
                ),
                columns=_entity_cols("ticket_id", _std_ticket_extras()),
            )
        )
    return tables


def _bronze_finance() -> list[Table]:
    tables: list[Table] = []
    base = {
        "invoices": "invoice extract from NetSuite",
        "invoice_raw": "unparsed invoice CSV",
        "payments": "payment-processor dump",
        "gl_entries": "general-ledger journal dump",
        "invoice_lines": "invoice line extract",
        "credit_memos": "credit-memo dump",
    }
    extras = {
        "invoice": _std_invoice_extras(),
        "payment": [_col("invoice_id", "BIGINT"), _col("amount", "STRING"), _col("src_file")],
        "gl": [_col("account"), _col("amount", "STRING"), _col("src_file")],
        "credit": [_col("invoice_id", "BIGINT"), _col("amount", "STRING"), _col("src_file")],
    }

    def ek(name: str) -> str:
        if name.startswith("payment"):
            return "payment"
        if name.startswith("gl"):
            return "gl"
        if name.startswith("credit"):
            return "credit"
        return "invoice"

    for name, note in base.items():
        k = ek(name)
        tables.append(
            Table(
                schema="bronze_finance",
                name=name,
                comment=(
                    f"Bronze landing: {note}. Do not use to compute ARR — use "
                    "gold_finance.arr_by_product_region_year."
                ),
                columns=_entity_cols(f"{k}_id", extras[k]),
            )
        )
    for year in YEARS:
        tables.append(
            Table(
                schema="bronze_finance",
                name=f"invoices_{year}",
                comment=(
                    f"Bronze year-shard invoice extract for calendar year {year} only."
                ),
                columns=_entity_cols("invoice_id", _std_invoice_extras()),
            )
        )
    for region in ("emea", "amer", "apac"):
        tables.append(
            Table(
                schema="bronze_finance",
                name=f"invoices_{region}",
                comment=f"Bronze regional invoice extract for {region.upper()} only.",
                columns=_entity_cols("invoice_id", _std_invoice_extras()),
            )
        )
    return tables


def _archive() -> list[Table]:
    tables: list[Table] = []
    frozen = {
        "customers": _std_customer_extras(),
        "orders": _std_order_extras(),
        "tickets": _std_ticket_extras(),
        "invoices": _std_invoice_extras(),
        "subscriptions": [
            _col("customer_id", "BIGINT"),
            _col("plan"),
            _col("status"),
            _col("mrr", "STRING"),
            _col("src_file"),
        ],
    }
    for name, extras in frozen.items():
        pk = "customer_id" if name == "customers" else f"{name.rstrip('s')}_id"
        if name == "subscriptions":
            pk = "subscription_id"
        tables.append(
            Table(
                schema="archive",
                name=name,
                comment=(
                    f"Frozen historical copy of {name} (pre-2023). Do not use for "
                    "current ARR, active subscriptions, or open tickets."
                ),
                columns=_entity_cols(pk, extras),
            )
        )
    for year in range(2016, 2022):
        tables.append(
            Table(
                schema="archive",
                name=f"orders_{year}",
                comment=f"Archived orders for calendar year {year}. Frozen; not current.",
                columns=_entity_cols("order_id", _std_order_extras()),
            )
        )
    for year in (2020, 2021, 2022):
        tables.append(
            Table(
                schema="archive",
                name=f"customers_snapshot_{year}",
                comment=f"Year-end {year} customer snapshot. Historical only.",
                columns=_entity_cols("customer_id", _std_customer_extras()),
            )
        )
    return tables


def _staging() -> list[Table]:
    names = [
        ("customers", "customer_id", _std_customer_extras()),
        ("orders", "order_id", _std_order_extras()),
        ("tickets", "ticket_id", _std_ticket_extras()),
        ("invoices", "invoice_id", _std_invoice_extras()),
        ("load_batches", "batch_id", [_col("source"), _col("loaded_at", "STRING")]),
        ("customers_stg", "customer_id", _std_customer_extras()),
        ("orders_stg", "order_id", _std_order_extras()),
        ("tickets_stg", "ticket_id", _std_ticket_extras()),
        ("_landing_customers", "customer_id", _std_customer_extras()),
        ("_landing_orders", "order_id", _std_order_extras()),
    ]
    tables = []
    for name, pk, extras in names:
        tables.append(
            Table(
                schema="staging",
                name=name,
                comment=(
                    f"ETL staging table `{name}`. Truncated every night. Never query "
                    "staging for business answers."
                ),
                columns=_entity_cols(pk, extras),
            )
        )
    return tables


def _marketing() -> list[Table]:
    specs = [
        (
            "customers",
            "customer_id",
            "Marketing audience members (not paying subscribers). Do not use for ARR or active-subscription questions.",
            _std_customer_extras(),
        ),
        (
            "campaigns",
            "campaign_id",
            "Marketing campaigns.",
            [_col("campaign_name"), _col("channel"), _col("start_date", "STRING")],
        ),
        (
            "campaign_touches",
            "touch_id",
            "Campaign touches against the marketing audience.",
            [_col("campaign_id", "BIGINT"), _col("customer_id", "BIGINT"), _col("channel")],
        ),
        (
            "email_sends",
            "send_id",
            "Email send log.",
            [_col("campaign_id", "BIGINT"), _col("email"), _col("sent_at", "STRING")],
        ),
        (
            "email_opens",
            "open_id",
            "Email open log.",
            [_col("send_id", "BIGINT"), _col("opened_at", "STRING")],
        ),
        (
            "leads",
            "lead_id",
            "Marketing leads. Not CRM customers.",
            [_col("email"), _col("source"), _col("status")],
        ),
        (
            "mql",
            "mql_id",
            "Marketing-qualified leads.",
            [_col("lead_id", "BIGINT"), _col("qualified_on", "STRING")],
        ),
        (
            "sql_opps",
            "sql_id",
            "Sales-qualified opportunities from marketing. Not bookings.",
            [_col("lead_id", "BIGINT"), _col("amount", "STRING")],
        ),
        (
            "attribution",
            "attr_id",
            "Multi-touch attribution (marketing).",
            [_col("lead_id", "BIGINT"), _col("campaign_id", "BIGINT"), _col("weight", "DOUBLE")],
        ),
        (
            "website_sessions",
            "session_id",
            "Marketing-site sessions. Not product usage.",
            [_col("anonymous_id"), _col("started_at", "STRING")],
        ),
    ]
    return [
        Table(schema="marketing", name=n, comment=c, columns=_entity_cols(pk, extras))
        for n, pk, c, extras in specs
    ]


def _hr() -> list[Table]:
    specs = [
        (
            "employees",
            "employee_id",
            "HR employee directory.",
            [_col("email"), _col("full_name"), _col("department_id", "BIGINT")],
        ),
        (
            "departments",
            "department_id",
            "HR departments.",
            [_col("department_name")],
        ),
        (
            "contractors",
            "contractor_id",
            "HR contractors.",
            [_col("email"), _col("vendor")],
        ),
        (
            "tickets",
            "ticket_id",
            "HR cases (benefits, payroll, equipment). NOT customer-support tickets. Do not use for P1 incidents.",
            [_col("employee_id", "BIGINT"), _col("category"), _col("status")],
        ),
        (
            "hr_tickets",
            "ticket_id",
            "Alias of HR cases. Not customer support.",
            [_col("employee_id", "BIGINT"), _col("category"), _col("status")],
        ),
        (
            "headcount_monthly",
            "headcount_id",
            "Monthly headcount.",
            [_col("month", "STRING"), _col("department_id", "BIGINT"), _col("n", "INT")],
        ),
        (
            "org_units",
            "org_unit_id",
            "HR org units.",
            [_col("name"), _col("parent_id", "BIGINT")],
        ),
        (
            "pto_requests",
            "pto_id",
            "Paid-time-off requests.",
            [_col("employee_id", "BIGINT"), _col("days", "INT"), _col("status")],
        ),
    ]
    return [
        Table(schema="hr", name=n, comment=c, columns=_entity_cols(pk, extras))
        for n, pk, c, extras in specs
    ]


# ---------------------------------------------------------------------------
# SQL renderer
# ---------------------------------------------------------------------------

def _sql_ident(name: str) -> str:
    return f"`{name}`"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_comment(text: str) -> str:
    return _sql_str(text)


def _literal(value: object, dtype: str) -> str:
    if value is None:
        return "NULL"
    if dtype in {"BIGINT", "INT", "DOUBLE"}:
        return str(value)
    if dtype == "BOOLEAN":
        return "TRUE" if value else "FALSE"
    if dtype == "DATE":
        return f"DATE '{value}'"
    if dtype == "TIMESTAMP":
        if isinstance(value, str) and value.startswith("TIMESTAMP"):
            return value
        return f"TIMESTAMP '{value}'"
    return _sql_str(str(value))


def _create_table_sql(catalog: str, table: Table) -> str:
    cat = _sql_ident(catalog)
    sch = _sql_ident(table.schema)
    tbl = _sql_ident(table.name)
    col_sql: list[str] = []
    pk_cols = [c.name for c in table.columns if c.pk]
    for c in table.columns:
        null = "" if c.nullable else " NOT NULL"
        cmt = f" COMMENT {_sql_comment(c.comment)}" if c.comment else ""
        col_sql.append(f"  {_sql_ident(c.name)} {c.dtype}{null}{cmt}")
    if pk_cols:
        pk_list = ", ".join(_sql_ident(c) for c in pk_cols)
        constraint = f"pk_{table.schema}_{table.name}"[:255]
        col_sql.append(f"  CONSTRAINT {_sql_ident(constraint)} PRIMARY KEY ({pk_list})")
    body = ",\n".join(col_sql)
    return (
        f"CREATE TABLE {cat}.{sch}.{tbl} (\n{body}\n)\n"
        f"COMMENT {_sql_comment(table.comment)};"
    )


def _insert_sql(catalog: str, table: Table) -> str | None:
    if not table.rows:
        return None
    cat = _sql_ident(catalog)
    sch = _sql_ident(table.schema)
    tbl = _sql_ident(table.name)
    insert_cols = [c for c in table.columns if c.seeded]
    # Rows are aligned to insert_cols (timestamps and filler are seeded=False).
    names = ", ".join(_sql_ident(c.name) for c in insert_cols)
    value_lines: list[str] = []
    for row in table.rows:
        if len(row) != len(insert_cols):
            raise ValueError(
                f"{table.fqn}: row has {len(row)} values, expected {len(insert_cols)} "
                f"({[c.name for c in insert_cols]})"
            )
        lits = ", ".join(_literal(v, c.dtype) for v, c in zip(row, insert_cols, strict=True))
        value_lines.append(f"  ({lits})")
    return (
        f"INSERT INTO {cat}.{sch}.{tbl} ({names}) VALUES\n"
        + ",\n".join(value_lines)
        + ";"
    )


def _fk_sql(catalog: str, table: Table, fk: ForeignKey, idx: int) -> str:
    cat = _sql_ident(catalog)
    name = f"fk_{table.schema}_{table.name}_{idx}"[:255]
    return (
        f"ALTER TABLE {cat}.{_sql_ident(table.schema)}.{_sql_ident(table.name)} "
        f"ADD CONSTRAINT {_sql_ident(name)} FOREIGN KEY ({_sql_ident(fk.column)}) "
        f"REFERENCES {cat}.{_sql_ident(fk.ref_schema)}.{_sql_ident(fk.ref_table)} "
        f"({_sql_ident(fk.ref_column)});"
    )


def render_statements(catalog: str, tables: list[Table] | None = None) -> list[str]:
    """Return ordered Databricks SQL statements (no trailing semicolons)."""
    tables = tables if tables is not None else build_lakehouse()
    cat = _sql_ident(catalog)
    stmts: list[str] = [
        f"CREATE CATALOG IF NOT EXISTS {cat}",
        f"USE CATALOG {cat}",
    ]
    for schema, comment in SCHEMA_COMMENTS.items():
        sch = _sql_ident(schema)
        stmts.append(f"DROP SCHEMA IF EXISTS {cat}.{sch} CASCADE")
        stmts.append(f"CREATE SCHEMA {cat}.{sch} COMMENT {_sql_comment(comment)}")
    for table in tables:
        create_sql = _create_table_sql(catalog, table)
        for part in create_sql.split(";\n"):
            part = part.strip().rstrip(";")
            if part:
                stmts.append(part)
        insert = _insert_sql(catalog, table)
        if insert:
            stmts.append(insert.strip().rstrip(";"))
    for table in tables:
        for i, fk in enumerate(table.fks, 1):
            stmts.append(_fk_sql(catalog, table, fk, i).rstrip(";"))
    return stmts


def render_sql(catalog: str, tables: list[Table] | None = None) -> str:
    """Render a Databricks SQL script that recreates the lakehouse schemas."""
    tables = tables if tables is not None else build_lakehouse()
    cat = _sql_ident(catalog)
    chunks: list[str] = [
        f"-- Auto-generated by src/lakehouse.py for catalog {catalog}",
        f"-- {len(tables)} tables across {len(SCHEMA_COMMENTS)} schemas",
        f"CREATE CATALOG IF NOT EXISTS {cat};",
        f"USE CATALOG {cat};",
    ]
    for schema, comment in SCHEMA_COMMENTS.items():
        sch = _sql_ident(schema)
        chunks.append(f"DROP SCHEMA IF EXISTS {cat}.{sch} CASCADE;")
        chunks.append(f"CREATE SCHEMA {cat}.{sch} COMMENT {_sql_comment(comment)};")

    for table in tables:
        chunks.append(_create_table_sql(catalog, table))
        insert = _insert_sql(catalog, table)
        if insert:
            chunks.append(insert)

    for table in tables:
        for i, fk in enumerate(table.fks, 1):
            chunks.append(_fk_sql(catalog, table, fk, i))

    chunks.append(
        f"SELECT table_schema, COUNT(DISTINCT table_name) AS tables, "
        f"COUNT(*) AS columns "
        f"FROM {cat}.information_schema.columns "
        f"WHERE table_schema IN ({', '.join(_sql_str(s) for s in SCHEMA_COMMENTS)}) "
        f"GROUP BY table_schema ORDER BY table_schema;"
    )
    return "\n\n".join(chunks) + "\n"


def lakehouse_stats(tables: list[Table] | None = None) -> dict[str, int]:
    tables = tables if tables is not None else build_lakehouse()
    return {
        "schemas": len(SCHEMA_COMMENTS),
        "tables": len(tables),
        "columns": sum(len(t.columns) for t in tables),
        "fks": sum(len(t.fks) for t in tables),
        "seeded_tables": sum(1 for t in tables if t.rows),
    }


if __name__ == "__main__":
    built = build_lakehouse()
    stats = lakehouse_stats(built)
    print(stats)
    for table in built:
        _insert_sql("neocarta_demo", table)
    names = [t.fqn for t in built]
    dupes = [n for n in names if names.count(n) > 1]
    if dupes:
        raise SystemExit(f"Duplicate tables: {sorted(set(dupes))}")
    print("spec ok")
