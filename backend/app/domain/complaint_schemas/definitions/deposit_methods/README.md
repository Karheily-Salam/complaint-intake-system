# Deposit methods

Deposit complaints share the `common_fields` defined in `../deposit.yaml`, but
each deposit **method** can require additional fields.

## Adding a method (no code changes)

Add an entry under `methods:` in `../deposit.yaml`:

```yaml
methods:
  - key: bank_transfer
    label: Bank transfer
    description: SEPA / SWIFT / domestic bank transfer.
    additional_fields:
      - key: bank_reference
        label: Bank reference / IBAN
        type: string
        required: true
        description: The IBAN or bank reference used for the transfer.
        extraction_hint: An IBAN or bank transfer reference code.
      - key: amount_sent
        label: Amount sent
        type: number
        required: true
        description: The amount the customer transferred.
```

The conversation engine reads these through `ComplaintSchemaRegistry` and will:

- ask the customer which method they used (using `method_selector_label`),
- merge `common_fields` + the selected method's `additional_fields`,
- ask only for whichever of those are still missing.

A follow-up task may split each method into its own file in this directory and
extend `ComplaintSchemaRegistry.from_directory` to merge them; the schema shape
stays identical.
