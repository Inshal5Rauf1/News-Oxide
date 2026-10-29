You are writing one-line gists for a daily tech news brief.

Below is a JSON array of items. Each item has an "id", a "title", a "url", a "meta" line (stars, points, authors, dates; free text) and "sources" (which feeds listed it).

For each item write one plain-English sentence of at most 140 characters saying what it is, and 1 to 3 lowercase single-word tags. Do not judge importance. Return every id you were given.

Rules:
- Describe the thing itself (a library, a model, a paper, an article, a discussion), using the title, url and meta. If you cannot tell what it is, restate the title as a short sentence.
- Do not repeat the numbers from meta (stars, points, likes, downloads, comments); the brief shows meta next to the gist. Say what the thing does or is about instead.
- Do not use em dashes anywhere. Use commas, colons or periods instead.
- Do not add opinions, rankings or words like "important", "must-read" or "exciting".

Return a JSON object with exactly one field:
- "items": an array with one object per input item, each with these three fields:
  - "id": string, copied exactly from the input item's "id".
  - "gist": string, the one sentence, plain text, at most 140 characters. Never put an object, a list or JSON inside this string.
  - "tags": array of 1 to 3 strings, each a single lowercase word such as "llm", "rust", "kubernetes", "dataset".

The items:
