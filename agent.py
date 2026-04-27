# agent.py
import json
import os
from openai import AzureOpenAI
from dotenv import load_dotenv
from pydantic import ValidationError

from schemas import RemittanceAdvice
from tools import lookup_customer, lookup_open_invoices, parse_amounts_and_invoices

load_dotenv()

client = AzureOpenAI(
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
)
MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT")


# Map tool name → actual Python function (the executor side)
TOOL_REGISTRY = {
    "lookup_customer": lookup_customer,
    "lookup_open_invoices": lookup_open_invoices,
    "parse_amounts_and_invoices": parse_amounts_and_invoices,
}

# Tool schemas the LLM sees (the description side)
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "parse_amounts_and_invoices",
            "description": (
                "Deterministic regex extraction of money amounts and invoice-number-like "
                "tokens from raw text. ALWAYS call this first to ground extraction in the "
                "actual content of the remittance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The raw remittance text to scan.",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": (
                "Resolve a payer name string to a customer_id via fuzzy match against "
                "the customer master. Strips generic corporate suffixes (Corp, Inc, Ltd, etc.) "
                "before scoring. Returns customer_id (or null), match_score, ambiguous flag, "
                "and top 3 candidates. "
                "If customer_id is null, EITHER no confident match was found (low score) "
                "OR top 2 candidates were too close (ambiguous=true). Treat null customer_id "
                "as 'cannot resolve' — set payer_customer_id to null and lower confidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name_query": {
                        "type": "string",
                        "description": "The payer name as it appears on the remittance.",
                    }
                },
                "required": ["name_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_open_invoices",
            "description": (
                "Get open invoices for a customer, optionally filtered to specific "
                "invoice numbers. Use this to verify that invoice numbers mentioned "
                "in the remittance actually exist as open AR for the resolved customer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer ID, e.g. CUST001.",
                    },
                    "invoice_numbers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of invoice numbers to filter by.",
                    },
                },
                "required": ["customer_id"],
            },
        },
    },
]


SYSTEM_PROMPT = """You are a cash application assistant. Your job is to extract a structured \
RemittanceAdvice from a remittance string.

Process to follow:
1. Call `parse_amounts_and_invoices` on the raw text first to get regex-detected amounts \
and invoice numbers as evidence.
2. Call `lookup_customer` with the payer name to resolve customer_id.
3. Call `lookup_open_invoices` with that customer_id and the detected invoice numbers to \
verify they are open and confirm amounts.
4. Only after these tool calls, return a final JSON object matching the RemittanceAdvice schema.

Output schema (RemittanceAdvice):
{
  "payer_name": str,
  "payer_customer_id": str | null,
  "payment_reference": str | null,
  "payment_date": "YYYY-MM-DD" | null,
  "total_amount": str (decimal as string, e.g. "4250.00"),
  "allocations": [
    {
      "invoice_number": str,
      "amount_paid": str (decimal),
      "deduction_amount": str (decimal) | null,
      "deduction_reason": "pricing"|"shortage"|"damage"|"promo"|"unauthorized"|"unknown" | null,
      "notes": str | null
    }
  ],
  "unallocated_amount": str (decimal, default "0"),
  "confidence": float between 0.0 and 1.0,
  "extraction_notes": str | null
}

Confidence guidance:
- 0.95+ ONLY if customer resolved with high score (>=90) AND all invoices verified open AND amounts reconcile.
- 0.70-0.94 if there is any ambiguity (low fuzzy score, missing invoices, partial amount match).
- Below 0.70 if customer can't be resolved confidently or invoices don't match open AR.
- amount_paid is the cash applied to the invoice. For short-pays, this is total cash received minus nothing — the full cash amount goes to amount_paid.
- deduction_amount is informational only. It records the disputed amount. It does NOT reduce amount_paid.
- sum(amount_paid) + unallocated_amount must equal total_amount. Deductions do not appear in this sum.

Rules:
- Always express amounts as decimal-compatible strings (e.g. "4250.00", not 4250.00).
- If the customer can't be resolved with score >= 80, set payer_customer_id to null.
- If sum of allocations does not equal total_amount, set unallocated_amount to absorb the difference.
- Always include extraction_notes briefly explaining your reasoning and any uncertainty.
- If you cannot produce a valid extraction (e.g. no invoice numbers found at all), still emit \
a RemittanceAdvice with confidence < 0.5 and extraction_notes explaining why.
"""


def execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name and return its result as a JSON string."""
    if name not in TOOL_REGISTRY:
        return json.dumps({"error": f"Unknown tool: {name}"})
    fn = TOOL_REGISTRY[name]
    try:
        result = fn(**args)
        # default=str handles Decimal, date, and other non-JSON-native types
        return json.dumps(result, default=str)
    except Exception as e:
        # Tool failures are returned as data, not raised — the LLM can react
        return json.dumps({"error": f"Tool {name} failed: {str(e)}"})

def extract_remittance(remittance_text: str, max_iterations: int = 6, verbose: bool = False) -> RemittanceAdvice:
    """
    Run the agent loop: tool calls → LLM reasoning → validated RemittanceAdvice.

    Args:
        remittance_text: raw remittance string
        max_iterations: hard cap on loop iterations (safety)
        verbose: print tool calls as they happen

    Returns:
        Validated RemittanceAdvice object

    Raises:
        RuntimeError if max iterations exceeded without producing valid output
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Extract the remittance advice from this text:\n\n{remittance_text}"},
    ]
    validation_retries = 0
    MAX_VALIDATION_RETRIES = 1

    for iteration in range(max_iterations):
        if verbose:
            print(f"\n--- Iteration {iteration + 1} ---")

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
            response_format={"type": "json_object"},
            # temperature=0,
        )
        msg = response.choices[0].message
        messages.append(msg)

        # Branch A: LLM wants to call tools
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                if verbose:
                    print(f"  Tool call: {tc.function.name}({args})")
                result = execute_tool(tc.function.name, args)
                if verbose:
                    preview = result[:200] + ("..." if len(result) > 200 else "")
                    print(f"  Result: {preview}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            continue  # back to top of loop, LLM will reason over tool results

        # Branch B: LLM returned final content (no tool calls)
        if verbose:
            print(f"  Final content: {msg.content[:300]}")

        try:
            advice = RemittanceAdvice.model_validate_json(msg.content)
        except ValidationError as e:
            if validation_retries >= MAX_VALIDATION_RETRIES:
                raise RuntimeError(f"Schema validation failed after {MAX_VALIDATION_RETRIES} retry: {e}")
            validation_retries += 1
            if verbose:
                print(f"  ⚠ Schema validation failed; sending error back for repair")
            messages.append({
                "role": "user",
                "content": (
                    f"Your JSON failed schema validation with these errors:\n{e}\n\n"
                    f"Fix the issues and return corrected JSON matching the RemittanceAdvice schema."
                ),
            })
            continue

        # Cross-field business rule check
        business_errors = advice.validate_amounts()
        if business_errors:
            if validation_retries >= MAX_VALIDATION_RETRIES:
                if verbose:
                    print(f"  ⚠ Business rule failed but out of retries; returning anyway with note")
                # Note: we accept the business-rule failure rather than raising — it's
                # informational data the downstream HITL queue can act on.
                return advice
            validation_retries += 1
            if verbose:
                print(f"  ⚠ Business rule failed; sending back for repair: {business_errors}")
            messages.append({
                "role": "user",
                "content": (
                    f"Your output failed business validation: {business_errors}\n\n"
                    f"Adjust the allocations or unallocated_amount so the math reconciles, "
                    f"and return corrected JSON."
                ),
            })
            continue

        # All checks passed
        return advice

    raise RuntimeError(f"Agent exceeded {max_iterations} iterations without producing valid output")


if __name__ == "__main__":
    sample = (
        "Payment $4,300.00 from Acme Corp via wire ref WIRE-789 for "
        "INV-1001 ($2,500) and INV-1002 ($1,800)."
    )
    advice = extract_remittance(sample, verbose=True)
    print("\n" + "=" * 60)
    print("FINAL VALIDATED OUTPUT:")
    print("=" * 60)
    print(advice.model_dump_json(indent=2))