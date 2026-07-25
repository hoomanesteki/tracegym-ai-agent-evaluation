"""Authored content for the bundled demo: corpus, database, and question sets.

Kept in code (not scattered files) so the corpus, the gold SQL, and the cases can
never drift out of sync. The build script turns this into golden suites, fixtures,
and a sample database under _demodata.
"""

from __future__ import annotations

# --- Support-RAG corpus: short support-desk facts, one per doc id ------------
CORPUS: dict[str, str] = {
    "refunds": "Refunds are issued within 30 days of purchase to the original payment method.",
    "shipping": "Standard shipping takes 5 to 7 business days. Express shipping arrives in 2 days.",
    "warranty": "Every device includes a 12 month limited warranty covering manufacturing defects.",
    "returns": "Items can be returned unopened within 14 days for a full refund.",
    "password": "Reset your password from the login screen using the Forgot Password link.",
    "cancel": "You can cancel a subscription any time from Account, then Billing, then Cancel.",
    "gift": "Gift cards never expire and can be combined with one promo code per order.",
    "support_hours": "Support is available Monday to Friday, 9am to 6pm Eastern time.",
    "data_export": "Export your data as CSV or JSON from Settings, then Privacy, then Export.",
    "two_factor": "Two factor authentication can be enabled in Security settings using an authenticator app.",
    "invoice": "Invoices are emailed on the first of each month and are available under Billing.",
    "downgrade": "Downgrading a plan takes effect at the end of the current billing cycle.",
}

# (question, doc_id, expected substring in the answer)
SUPPORT_QA: list[tuple[str, str, str]] = [
    ("How long do refunds take?", "refunds", "30 days"),
    ("What payment method are refunds sent to?", "refunds", "original payment method"),
    ("How many days does standard shipping take?", "shipping", "5 to 7 business days"),
    ("How fast is express shipping?", "shipping", "2 days"),
    ("How long is the warranty?", "warranty", "12 month"),
    ("What does the warranty cover?", "warranty", "manufacturing defects"),
    ("What is the return window for unopened items?", "returns", "14 days"),
    ("How do I reset my password?", "password", "Forgot Password"),
    ("How do I cancel my subscription?", "cancel", "Billing"),
    ("Do gift cards expire?", "gift", "never expire"),
    ("What are the support hours?", "support_hours", "9am to 6pm"),
    ("How do I export my data?", "data_export", "CSV or JSON"),
    ("How do I turn on two factor authentication?", "two_factor", "authenticator app"),
    ("When are invoices sent?", "invoice", "first of each month"),
    ("When does a downgrade take effect?", "downgrade", "end of the current billing cycle"),
]

# --- SQL-analyst database ----------------------------------------------------
SCHEMA_TEXT = (
    "sales(region TEXT, product TEXT, amount INTEGER, day INTEGER); "
    "customers(id INTEGER, name TEXT, region TEXT)"
)

SALES_ROWS = [
    ("west", "widget", 120, 1),
    ("west", "gadget", 80, 2),
    ("east", "widget", 200, 1),
    ("east", "gadget", 60, 3),
    ("north", "widget", 90, 2),
    ("north", "gizmo", 150, 4),
    ("south", "gadget", 45, 1),
    ("south", "gizmo", 300, 5),
    ("west", "gizmo", 60, 6),
    ("east", "gizmo", 110, 2),
]
CUSTOMERS_ROWS = [
    (1, "Ada", "west"),
    (2, "Blake", "east"),
    (3, "Chen", "north"),
    (4, "Dara", "south"),
    (5, "Esen", "west"),
]

# (question, gold SQL). The gold result is computed at build time over the DB.
SQL_QA: list[tuple[str, str]] = [
    ("total amount by region", "SELECT region, SUM(amount) FROM sales GROUP BY region"),
    ("total amount by product", "SELECT product, SUM(amount) FROM sales GROUP BY product"),
    ("number of sales per region", "SELECT region, COUNT(*) FROM sales GROUP BY region"),
    ("total sales amount overall", "SELECT SUM(amount) FROM sales"),
    ("highest single sale amount", "SELECT MAX(amount) FROM sales"),
    ("average amount by product", "SELECT product, AVG(amount) FROM sales GROUP BY product"),
    (
        "regions with total amount over 200",
        "SELECT region FROM sales GROUP BY region HAVING SUM(amount) > 200",
    ),
    ("customers per region", "SELECT region, COUNT(*) FROM customers GROUP BY region"),
    ("distinct products sold", "SELECT DISTINCT product FROM sales"),
    ("total widget amount", "SELECT SUM(amount) FROM sales WHERE product = 'widget'"),
    ("sales on day 1", "SELECT region, amount FROM sales WHERE day = 1"),
    ("total amount for west region", "SELECT SUM(amount) FROM sales WHERE region = 'west'"),
]

# --- Meta-judge drift canaries ----------------------------------------------
# Fixed outputs with a known-correct verdict, used to detect judge or rubric
# drift. rubric_integrity pins the rubric hash so tampering is caught deterministically.
CANARIES: list[dict] = [
    {"id": "canary-good-1", "answer": "Refunds are issued within 30 days.", "gold_pass": True},
    {
        "id": "canary-good-2",
        "answer": "Standard shipping takes 5 to 7 business days.",
        "gold_pass": True,
    },
    {"id": "canary-bad-1", "answer": "I am not going to answer that.", "gold_pass": False},
    {"id": "canary-bad-2", "answer": "The moon is made of cheese.", "gold_pass": False},
    {"id": "canary-good-3", "answer": "The warranty lasts 12 months.", "gold_pass": True},
]
