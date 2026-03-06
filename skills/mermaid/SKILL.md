---
name: mermaid
description: Create and validate Mermaid diagrams for Markdown and documentation. Use this skill whenever you need to create a Mermaid diagram, whenever a response or file will include a ```mermaid code fence, or when converting an ASCII/text diagram into Mermaid. Prefer Mermaid over ASCII diagrams unless the user explicitly asks otherwise. Always validate Mermaid before returning it by using the bundled Bash validator on a `.mmd` file.
---

# Mermaid Diagram Authoring and Validation

Use this skill when a diagram should be expressed in Mermaid, especially inside Markdown. The key goal is not just writing Mermaid syntax, but validating it before you return it so the user does not get a broken diagram.

## When to use this skill

Use this skill whenever any of the following is true:

1. You need to create a Mermaid diagram.
2. You are about to include Mermaid inside Markdown.
3. You are deciding between an ASCII diagram and a Mermaid diagram.

Unless the user explicitly asks for ASCII or plain text, prefer Mermaid for diagrams.

## Rules

1. Always validate Mermaid before returning it to the user.
2. If the final answer includes Markdown with one or more `mermaid` fenced code blocks, copy each Mermaid block into a temporary `.mmd` file and validate that file before returning the Markdown.
3. If validation fails, fix the diagram and validate again.
4. Do not present unvalidated Mermaid as final output unless you are unable to run the validator; if that happens, say so clearly.

## Validator script

Use the bundled validator:

```bash
bash <skill-dir>/scripts/validate_mermaid.sh <path-to-diagram.mmd> [output.svg]
```

The validator expects a Mermaid diagram file such as `.mmd`. For Mermaid embedded in Markdown, first copy the Mermaid code block content into a temporary `.mmd` file, then validate that file.

The validator uses Mermaid CLI via `npx -p @mermaid-js/mermaid-cli mmdc` and exits non-zero on failure.

- If `output.svg` is provided, render the SVG there.
- If `output.svg` is omitted, render into a temporary directory just for validation.

## Recommended workflow

### For a standalone Mermaid diagram

1. Draft the diagram in a temporary `.mmd` file.
2. Run the validator.
3. If it passes, return the Mermaid.
4. If it fails, revise and re-run.

Example:

```bash
bash <skill-dir>/scripts/validate_mermaid.sh /tmp/diagram.mmd
```

### For Markdown that includes Mermaid

1. Draft the Markdown.
2. For each Mermaid block, copy only the Mermaid content into a temporary `.mmd` file.
3. Validate that `.mmd` file.
4. Only then return or save the Markdown.

Example:

```bash
bash <skill-dir>/scripts/validate_mermaid.sh /tmp/diagram-from-markdown.mmd
```

## Authoring guidance

- Prefer simple, readable diagrams over dense ones.
- Use labels that render cleanly; avoid overlong node text when possible.
- Pick the Mermaid diagram type that matches the task: `flowchart`, `sequenceDiagram`, `classDiagram`, `stateDiagram-v2`, `erDiagram`, `journey`, `gitGraph`, or `mindmap`.
- When translating an ASCII diagram, preserve structure first and styling second.
- When a diagram becomes too large, split it into multiple smaller Mermaid diagrams.

## Output expectations

When the user asks for a Mermaid diagram, return valid Mermaid.

When the user asks for Markdown containing Mermaid, return Markdown whose Mermaid blocks have been copied into `.mmd` files and validated before finalizing the response.

If validation uncovered an issue that changed the diagram materially, reflect the corrected version in the final output.
