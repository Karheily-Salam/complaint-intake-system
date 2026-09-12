# Datasets

Small, hand-written, multilingual (English / Russian / Arabic) datasets used to
train and evaluate the ML components. Every line is synthetic: it was written
for this repository and contains no real customer email, name, address,
account number or identifier. Production data is never copied here.

| File | Purpose | Used for training? |
|---|---|---|
| `complaints/train.jsonl` | Complaint-type classifier training data | Yes |
| `complaints/test.jsonl` | Held-out classification test set | **Never** |
| `complaints/extraction_test.jsonl` | Gold field values for extraction evaluation | **Never** |

## Format

One JSON object per line:

```json
{"id": "en-w-01", "language": "en", "label": "withdrawal", "text": "..."}
```

`label` is one of `withdrawal`, `deposit`, `other`, `unclear`. `unclear` marks
messages that say too little to classify ("Hello", "I have a problem"): the
correct behaviour for those is to *abstain* and ask the customer, so the
classifier is trained to recognise them rather than guess.

The test files are kept apart on purpose. The trained model's metadata records
the SHA-256 of the training file (see `app/ml/artifacts/`), and a test fails
if the training data changes without the model being retrained, or if any
test sentence also appears in the training data.

## Reproducing the model and the evaluation

```bash
cd backend
python scripts/train_classifier.py      # rewrites app/ml/artifacts/complaint_classifier.*
python scripts/evaluate_ml.py           # prints and saves the evaluation report
```

## Limitations

The sets are small (hundreds of lines, not thousands), and written by one
author, so they are less varied than real traffic. The metrics they produce
measure the pipeline and catch regressions; they are not a claim about
accuracy on real customer mail.
