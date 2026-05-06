# Topic Conventions

Topics are represented as folders under `topics/`.

Recommended conventions:

- use lowercase slug-style names such as `personal-photos`
- prefer stable reusable topics over one-off folders
- avoid encoding timestamps or user ids in topic names
- keep topic choice explainable from the asset summary or description

Examples:

- `personal-photos`
- `travel`
- `receipts`
- `documents`

The topic folder is a primary storage boundary, but retrieval should still go through the structured JSONL index first.
