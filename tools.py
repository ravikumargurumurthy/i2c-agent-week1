# tools.py
import json
import re
from decimal import Decimal
from pathlib import Path
from rapidfuzz import fuzz

DATA_DIR = Path(__file__).parent / "data"


def _load(filename: str):
    """Internal helper — load a JSON file from the data directory."""
    return json.loads((DATA_DIR / filename).read_text())


# ---------- Tool 3: Open invoice lookup ----------
def lookup_open_invoices(customer_id: str, invoice_numbers: list[str] | None = None) -> list[dict]:
    """
    Return open invoices for a customer, optionally filtered by specific invoice numbers.

    Args:
        customer_id: e.g. "CUST001"
        invoice_numbers: optional list to filter, e.g. ["INV-1001", "INV-1002"]

    Returns:
        List of invoice dicts: [{invoice_number, customer_id, amount, issue_date, due_date}, ...]
    """
    invoices = _load("open_invoices.json")
    result = [i for i in invoices if i["customer_id"] == customer_id]
    if invoice_numbers:
        wanted = set(invoice_numbers)
        result = [i for i in result if i["invoice_number"] in wanted]
    return result

# ---------- Tool 1: Regex parser ----------
def parse_amounts_and_invoices(text: str) -> dict:
    """
    Deterministic regex extraction of money amounts and invoice-number-like tokens.
    The LLM uses this output as evidence to ground its extraction.

    Args:
        text: raw remittance text

    Returns:
        {
            "amounts_detected": ["2500.00", "50.00"],
            "invoice_numbers_detected": ["INV-1024", "INV-1025"]
        }
    """
    # Money pattern: optional $, optional space, then digits with optional commas + optional .XX
    money_pattern = r"\$?\s?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|\d+\.\d{2})"
    amounts = [m.replace(",", "") for m in re.findall(money_pattern, text)]

    # Invoice number pattern: INV-1234, INV1234, or 5+ digit numbers
    inv_pattern = r"\b(?:INV[-_]?\d{3,}|\d{5,})\b"
    invoices = re.findall(inv_pattern, text, re.IGNORECASE)

    # Dedupe while preserving order
    invoices = list(dict.fromkeys(invoices))

    return {
        "amounts_detected": amounts,
        "invoice_numbers_detected": invoices,
    }

# ---------- Tool 2: Customer lookup with fuzzy matching ----------
def lookup_customer(name_query: str) -> dict:
    """
    Resolve a payer name string to a customer_id using fuzzy matching against
    legal name and aliases in the customer master.

    Args:
        name_query: e.g. "Acme Corp" or "ACME CORP"

    Returns:
        {
            "customer_id": "CUST001" or None if no confident match,
            "legal_name": "Acme Corporation" or None,
            "match_score": 95,  # 0-100
            "top_candidates": [
                {"customer_id": "CUST001", "legal_name": "Acme Corporation", "score": 95},
                {"customer_id": "CUST002", "legal_name": "Acme Industries Inc", "score": 78},
                ...
            ]
        }
    """
    customers = _load("customers.json")
    candidates = []

    for c in customers:
        # Score against legal name AND every alias; take the best
        names_to_check = [c["legal_name"]] + c.get("aliases", [])
        best_score = max(fuzz.WRatio(name_query, name) for name in names_to_check)
        candidates.append({
            "customer_id": c["customer_id"],
            "legal_name": c["legal_name"],
            "score": best_score,
        })

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)

    top = candidates[0]
    confident = top["score"] >= 80

    return {
        "customer_id": top["customer_id"] if confident else None,
        "legal_name": top["legal_name"] if confident else None,
        "match_score": top["score"],
        "top_candidates": candidates[:3],
    }