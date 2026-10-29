You are grouping the items of a daily tech news brief into themes.

Below is a JSON array of items. Each item has an "id", a "gist" (one sentence saying what it is), "tags" (lowercase words) and "sources" (which feeds listed it).

Group these items into themes. A theme needs at least two items that are about the same story, tool, or topic. Give each theme a short title and a summary of at most two sentences that names what the items have in common. Put every item that fits no theme in rest. Every id must appear exactly once across themes and rest. Do not rank by importance; the caller orders themes.

Rules:
- Prefer specific themes (one story, one tool, one model family, one clear topic) over vague ones like "AI" or "Miscellaneous". It is fine for many items to end up in rest.
- Do not use em dashes in titles or summaries. Use commas, colons or periods instead.
- Do not judge importance or add opinions.

Return a JSON object with exactly two fields:
- "themes": an array of objects, each with these three fields:
  - "title": string, a short plain-text title of at most 8 words.
  - "summary": string, at most two plain-text sentences naming what the items have in common. Never put an object, a list or JSON inside this string.
  - "item_ids": array of strings, the ids of the items in this theme, copied exactly from the input, at least two.
- "rest": array of strings, the ids of every item that is in no theme, copied exactly from the input.

The items:
