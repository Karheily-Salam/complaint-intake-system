# Deposit methods — intentionally NOT modelled in the prototype

Available deposit methods differ by country/market and change over time, so the
prototype treats the deposit method as a **generic free-text field**
(`deposit_method` in `../deposit.yaml`). The AI extracts whatever the customer
says they used ("bank transfer", "PayPal", "my local payment wallet"); it is
**not** mapped to any predefined category, and it never triggers additional
required fields.

There is no predefined method list anywhere in the active implementation.

## If method/country-specific rules are needed later (optional extension)

The seam is `ComplaintSchema.fields_for()` (see `../../spec.py`). A future
extension can wrap the schema registry so that, given the already-collected
fields (including `deposit_method` and a country), it returns extra `FieldSpec`s
— e.g. a config file:

```yaml
# deposit_method_rules.yaml  (illustrative - not implemented)
- when: { deposit_method_matches: "(?i)bank|wire|sepa", country: DE }
  add_fields:
    - key: iban
      label: IBAN
      type: string
      required: true
```

The conversation engine would not change: it already asks the schema "what
fields are required right now?" every turn and re-computes missing/invalid
deterministically.
