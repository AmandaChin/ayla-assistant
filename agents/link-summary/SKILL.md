---
name: ayla-link-summary
description: Use when Ayla needs to summarize a Feishu document or web page link into a concise workbench-ready brief with source attribution.
---

# Ayla Link Summary

## Fetch Order

1. Feishu/Lark document URLs such as `larkoffice.com/docx`, `larkoffice.com/docs`, or `larkoffice.com/wiki` should be fetched with `lark-cli docs +fetch --api-version v2 --doc <url> --format json`.
2. Dynamic or authenticated web pages should be fetched with the browser MCP/browser-use surface when available.
3. Plain public web pages can fall back to HTTP HTML/text fetching.
4. If fetching fails, keep the original URL and say the content was not fully fetched; do not invent the missing content.

## Summary Shape

Return a short Chinese brief for the workbench:

- Keep only the source document/page's core content in the visible summary.
- Treat user phrases like `总结下`, `这个不错`, `这个文档不错`, `帮我看看`, `整理下`, `学习下`, and `mark` as capture instructions, not summary content; remove them from the displayed brief.
- A user note may be used only as a private focus hint for classification/title fallback when it contains concrete nouns, but it should not appear as `你的备注` in the summary.
- Include the source type and source title.
- Write one `简单总结` sentence based only on fetched content.
- Add 2-3 `关键点` bullets when the content supports them.
- Always keep `source_url` so the UI can link back to the original page.

## Markdown Asset Shape

When the link is materialized into Study / knowledge assets, keep a full Markdown conversion of the fetched source, not only the workbench brief:

- Preserve the source title, source URL, fetch provider, and fetch status at the top of the Markdown body.
- Convert document/page headings, paragraphs, links, images, lists, and code blocks into Markdown.
- Preserve Mermaid/whiteboard text as fenced `mermaid` blocks when available.
- The visible workbench card may stay concise, but the `.md` asset should contain the fetched source content so it can be searched and reviewed later.
- If the source cannot be fetched, the `.md` asset must keep the source URL and an explicit `抓取失败` note instead of fabricating content.

## Quality Bar

- Do not summarize the URL string itself when fetched content is available.
- Do not output a long article rewrite.
- Prefer concrete technical nouns, project names, people, dates, and decisions from the source.
- If fetching fails, emit an explicit `抓取失败` warning and mark the workbench card as a red/coral warning; keep the source URL and do not invent document content.
- If the page is a Feishu document, keep the result as an internal/private candidate unless the user clearly marks it public.
