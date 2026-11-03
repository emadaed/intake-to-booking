# Knowledge base (synthetic)

These are **fictional example policies** for a made-up clinic, written for this demo.
They are not real policies and describe no real organization.

Each file starts with a small header:

- `id`, `title`, `owner`
- `status`: `current`, `superseded` or `draft` (only `current` documents are searched)
- `effective_date`: documents dated in the future are not searched yet
- `superseded_by`: optional, points to the replacing document

Files whose names start with `_` are ignored by the assistant.
