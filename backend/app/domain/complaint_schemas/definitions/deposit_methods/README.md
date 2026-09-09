# Deposit methods

Deposit complaints share the `common_fields` defined in `../deposit.yaml` plus the
`method_field` (`deposit_method`). Each **method** listed under `methods:` then
contributes its own `additional_fields`, which become required **only after** the
conversation engine has determined which method the customer used.

## Adding or changing a method (no code changes)

Add an entry under `methods:` in `../deposit.yaml`:

```yaml
methods:
  - key: giro
    label: Giro / standing order
    description: Recurring domestic giro payment.
    aliases: ["giro", "standing order", "direct debit"]
    additional_fields:
      - key: giro_mandate_reference
        label: Mandate reference
        type: string
        required: true
        description: The giro mandate reference.
        extraction_hint: A mandate / mandate reference identifier.
```

- `key` is added automatically to `deposit_method`'s allowed enum values.
- `aliases` let a provider map free-text wording ("standing order", "direct
  debit") onto the method key. Keep synonyms here, not in Python.
- `additional_fields` use the same `FieldSpec` shape as `common_fields`.

The conversation engine reads all of this through `ComplaintSchemaRegistry` and:

- extracts `deposit_method` from whichever message reveals it (possibly not the
  first),
- once known, folds in that method's `additional_fields`,
- asks only for whichever required fields are still missing or invalid,
- creates the ticket the moment the deterministic required-field set is satisfied.

A later refactor may move each method into its own file here and merge them in
`ComplaintSchemaRegistry.from_directory`; the `FieldSpec` shape stays identical.
