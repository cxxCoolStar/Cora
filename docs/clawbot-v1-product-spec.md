# ClawBot V1 Product Spec

## 1. Overview

`ClawBot` is the first real end-user scenario for the `core` agent runtime.

Its goal is to replace part of the user's current "WeChat File Transfer Assistant" workflow:

- receive text, links, and files from the user
- preserve them as structured personal knowledge assets
- help organize them automatically
- make them easy to retrieve later through natural language

Unlike a passive file inbox, `ClawBot` should act as a **conversational personal archive assistant**.

## 2. Core Product Goal

The user wants a bot that behaves like a trusted personal intake and retrieval assistant:

1. The user can send content naturally, without rigid commands
2. The bot should infer whether the user wants to save, search, or organize content
3. If the bot is uncertain, it must ask a short clarification question before acting
4. Later, the user can ask about previously sent materials and get grounded answers

In short:

> ClawBot should help the user save, organize, and retrieve personal materials through chat.

## 3. Product Positioning

ClawBot is not a general-purpose agent in V1.

It is a focused assistant for:

- personal content capture
- lightweight knowledge organization
- natural-language retrieval
- grounded question answering over saved materials

It should feel:

- more useful than a plain file inbox
- more cautious than a fully automatic agent
- more conversational than a search box

## 4. Design Principles

### 4.1 Natural Interaction

The user should be able to send content directly in normal language, without needing special tags or command syntax.

Examples:

- send a long paragraph directly
- send a GitHub link directly
- send a `.txt` or `.docx` file directly
- ask "Where is that RAG note I sent before?"

### 4.2 Clarify Before Uncertain Action

If ClawBot cannot confidently determine user intent, it must ask for clarification before taking action.

This is a first-class product rule, not a fallback detail.

### 4.3 Grounded Retrieval

When answering questions about saved materials, ClawBot should answer from stored content and provide traceable evidence when possible.

### 4.4 Small But Reliable V1

V1 should not attempt to solve all content types or all workflow automation.

It should do a few core flows well:

- receive
- save
- organize
- find
- answer from saved data

## 5. Primary User Scenario

The main scenario is:

1. The user encounters a useful sentence, note, link, or file
2. The user sends it to ClawBot
3. ClawBot decides whether to save it directly or ask what the user wants
4. ClawBot stores the content in a structured way
5. Later, the user asks for the content or asks a question based on it
6. ClawBot retrieves the right material and answers correctly

This scenario is the core of V1.

## 6. User Goals

The user wants to:

- save materials quickly
- avoid manually organizing everything
- find old content by asking naturally
- get useful summaries from what was saved
- trust that the bot will not act incorrectly when intent is unclear

## 7. Non-Goals For V1

ClawBot V1 does not need to support:

- complex multi-agent workflows
- broad internet research by default
- deep workflow automation across many apps
- advanced collaboration or sharing
- OCR-heavy document pipelines
- audio understanding
- image understanding beyond file storage metadata
- full WeChat integration in the first build

## 8. Supported Inputs In V1

V1 should support the following input types, in priority order.

### 8.1 Direct Text

Examples:

- a long paragraph
- a copied quote
- meeting notes
- an interview question list

This is the highest-priority input mode because it matches the user's real habit.

### 8.2 Links

Examples:

- GitHub repo links
- blog article links
- documentation links

V1 should at least save the URL and basic metadata.

### 8.3 Files

Initial file support:

- `.txt`
- `.docx`

Future extensions:

- `.pdf`
- images
- audio transcripts

## 8.4 V1 Interaction Mode

ClawBot V1 does not connect to WeChat directly.

Instead, V1 should provide a local interaction surface that simulates the same user behavior:

- paste text
- paste links
- upload files
- receive bot replies
- answer clarification questions

Recommended V1 interaction mode:

- local web chat page
- backed by FastAPI APIs

CLI remains a developer tool, not the main end-user surface.

## 9. Key User Intents

ClawBot V1 should reason primarily over four top-level intents.

### 9.1 Capture

The user wants the bot to save incoming content.

Examples:

- user sends a long text block
- user sends a link
- user uploads a document
- user says "save this"

### 9.2 Retrieve

The user wants the bot to locate previously saved material.

Examples:

- "I sent a note about RAG before, find it"
- "What was that GitHub link I shared last week?"

### 9.3 Organize

The user wants the bot to summarize, group, classify, or transform saved material.

Examples:

- "Summarize the content I sent earlier"
- "Group my saved quotes by theme"

### 9.4 Chat

The user is discussing content conversationally without clearly asking to save or search.

Examples:

- "What do you think of this?"
- "Is this useful for interview prep?"

## 10. Intent Classification Strategy

Intent recognition should not rely on LLM free-form judgment alone.

V1 should use a hybrid strategy:

1. rule-based signals first
2. LLM classification second
3. policy layer decides whether confidence is high enough to act

### 10.1 Rule Signals

Examples of strong signals:

- attached file -> likely `capture`
- only a URL -> likely `capture`
- query phrases like "find", "where", "which one", "I sent before" -> likely `retrieve`
- phrases like "summarize", "organize", "classify" -> likely `organize`

### 10.2 Confidence-Based Decision

Each turn should produce:

- `intent`
- `confidence`
- `reason`
- `candidate_actions`

If confidence is low or multiple intents are plausible, ClawBot should ask a clarification question.

## 11. Clarification Policy

This is a defining V1 behavior.

### 11.1 Principle

If ClawBot is not confident enough about the user's intent, it should ask first rather than acting automatically.

### 11.2 When To Clarify

Examples:

- the user sends a long text block without any instruction
- the user sends a link and it is unclear whether they want to save or summarize it
- the user sends content that could reasonably mean "save", "summarize", or "discuss"

### 11.3 Clarification Style

Clarification questions should be:

- short
- direct
- limited to the main ambiguity

Good examples:

- "Do you want me to save this, or summarize it first?"
- "Should I store this link, or extract the main points now?"
- "Do you want this document filed, or should I organize it for you first?"

Bad examples:

- asking too many choices at once
- long multi-part forms
- vague wording that does not resolve the actual ambiguity

### 11.4 Pending Clarification State

V1 should support a lightweight pending state for unresolved turns.

Suggested fields:

- `pending_intent_candidates`
- `clarification_question`
- `pending_payload`
- `awaiting_user_confirmation`

This allows the user to answer simply:

- "Save it"
- "Summarize first"
- "Just keep it"

## 12. Default Behavior Rules

V1 should follow these default behavior rules.

### 12.1 High Confidence Capture

If the input strongly indicates storage intent:

- save the content
- generate metadata
- confirm what was saved

### 12.2 High Confidence Retrieval

If the input clearly asks for previously stored material:

- search the archive
- return the best result with evidence

### 12.3 High Confidence Organize

If the input clearly asks for organization or transformation:

- retrieve the relevant content
- produce the requested summary or grouping

### 12.4 Uncertain Intent

If the intent is uncertain:

- do not guess
- ask a clarification question
- wait for user response before acting

## 13. What ClawBot Stores

V1 should treat saved material as structured knowledge items, not just raw messages.

### 13.1 Two-Level Model

Recommended abstraction:

- `Message`
  - one user turn or inbound submission
- `Item`
  - one saved content unit extracted from that message

This allows future support for cases where one inbound turn contains more than one meaningful asset.

### 13.2 Item Types

Initial item types:

- `text_note`
- `link`
- `document`

### 13.3 Item Fields

Each stored item should include, at minimum:

- `id`
- `source_message_id`
- `item_type`
- `title`
- `raw_content`
- `normalized_text`
- `summary`
- `tags`
- `created_at`
- `updated_at`
- `source_type`
- `status`

For link items:

- `url`
- `domain`
- `fetch_status`
- `fetched_title`
- `fetched_content`

For document items:

- `file_name`
- `file_path`
- `mime_type`
- `parsed_text`

## 14. Ingestion Pipeline

When the user sends content that should be captured, V1 should process it through a simple ingestion pipeline.

### 14.1 Ingestion Steps

1. accept inbound content
2. detect input type
3. detect user intent
4. if uncertain, ask clarification and stop
5. normalize content
6. create item record
7. extract text if needed
8. generate title
9. generate summary
10. extract tags
11. split into chunks for retrieval
12. generate embeddings
13. persist item, chunks, and indexes
14. send user confirmation

### 14.2 Confirmation Style

The confirmation should be useful but lightweight.

Example:

- "Saved. I classified this as Agent / RAG / interview notes."
- "Saved this GitHub link. You can ask me about it later."

## 15. Organization Capabilities

V1 organization features should be intentionally modest but useful.

### 15.1 Per-Item Organization

For each saved item:

- extract title
- generate short summary
- attach tags

### 15.2 Query-Time Organization

When asked, V1 should also support:

- summarize one item
- summarize multiple related items
- group notes by theme
- extract key points

## 16. Retrieval Capabilities

V1 retrieval should support both lookup and grounded question answering.

### 16.1 Lookup Queries

Examples:

- "Find the Agent interview note I sent before"
- "What was that GitHub repository link?"

### 16.2 Question Answering Over Saved Content

Examples:

- "What did the RAG note say about chunking?"
- "Summarize the interview questions I saved about memory in agents"

### 16.3 Retrieval Strategy

Recommended V1 retrieval pipeline:

1. metadata filtering
2. keyword search
3. vector search
4. simple ranking
5. answer generation from retrieved evidence

## 17. Response Style For Retrieval

Responses should favor:

- a direct answer first
- source references second
- relevant snippets when needed

Example:

- "You saved a note titled `Agent and RAG interview questions`."
- "It includes questions about ReAct, memory, tool use, chunking, embeddings, and evaluation."
- "Source: saved on 2026-04-29"

For grounded answers:

- "In the note you saved, RAG evaluation was described from both retrieval and generation stages."
- "Relevant snippet: `How do you comprehensively evaluate a RAG system...`"

## 17.1 File Recall Strategy

Because V1 does not rely on native WeChat file return capability, ClawBot should not promise direct file resend.

Instead, when the user asks for a previously sent file, ClawBot should help the user locate it in chat history.

For file recall, the response should include:

- original file name
- saved date
- approximate time
- short content summary
- nearby context hints when available

Example:

- "I found the file `Agent-RAG-questions.docx`."
- "You sent it on 2026-04-29 around 19:40."
- "Summary: Agent and RAG interview question list."
- "You can look for it near the GitHub link you sent on the same day."

This makes ClawBot a file locator and content indexer, rather than a direct file-return channel.

## 18. V1 Architecture Implications

This scenario changes the focus of `core`.

The runtime should no longer be only a chat-and-tool loop.

V1 for ClawBot needs additional modules:

- `intent_router`
- `clarification_manager`
- `ingestion`
- `parser`
- `organizer`
- `indexer`
- `retriever`
- `grounded_qa`

The existing runtime can still be reused, but it needs a decision phase before execution.

### 18.1 Two-Phase Turn Handling

Recommended runtime structure:

1. **decision phase**
   - classify intent
   - estimate confidence
   - decide whether to clarify or execute
2. **execution phase**
   - run capture / retrieve / organize actions
   - produce final response

## 19. Suggested Data Model Additions

In addition to existing session/message storage, V1 for ClawBot likely needs:

- `items`
- `item_chunks`
- `item_tags`
- `clarification_states`

### 19.1 `items`

Fields:

- `id`
- `session_id`
- `source_message_id`
- `item_type`
- `title`
- `raw_content`
- `normalized_text`
- `summary`
- `metadata_json`
- `created_at`
- `updated_at`

### 19.2 `item_chunks`

Fields:

- `id`
- `item_id`
- `chunk_index`
- `content`
- `embedding_ref`
- `metadata_json`

### 19.3 `clarification_states`

Fields:

- `id`
- `session_id`
- `source_message_id`
- `candidate_intents`
- `question`
- `pending_payload_json`
- `status`
- `created_at`
- `updated_at`

## 20. Example User Flows

### 20.1 Direct Save Of Long Text

User:

- sends a long text about Agent and RAG interview questions

ClawBot:

- if confident it is a save request, save it
- respond with a short confirmation and tags

### 20.2 Ambiguous Long Text

User:

- sends a long text with no instruction

ClawBot:

- "Do you want me to save this, or summarize it first?"

User:

- "Save it"

ClawBot:

- stores it and confirms

### 20.3 Link Intake

User:

- sends a GitHub repository URL

ClawBot:

- if uncertain, asks whether to save or summarize
- otherwise saves it and optionally fetches metadata

### 20.4 Retrieval

User:

- "What was the Agent interview note I sent before?"

ClawBot:

- retrieves the relevant note
- returns title, summary, and key evidence

### 20.5 Grounded QA

User:

- "What did I save about RAG evaluation?"

ClawBot:

- retrieves the relevant chunks
- answers from saved content
- cites the source note

## 21. V1 Scope Recommendation

To keep V1 realistic, the recommended scope is:

- local web interaction page
- direct text capture
- link capture
- `.txt` and `.docx` file capture
- cautious intent routing
- clarification before uncertain action
- basic item summaries and tags
- keyword + vector retrieval
- grounded QA over saved materials

Not recommended for initial implementation:

- full WeChat integration
- PDF parsing in the first milestone
- image understanding
- autonomous proactive workflows

## 22. Success Criteria

ClawBot V1 is successful if the user can:

1. send content naturally
2. trust the bot not to act blindly when unclear
3. later ask for saved content in plain language
4. receive correct, source-grounded results

## 23. Open Product Questions

These questions should be resolved before implementation is finalized:

1. Should long plain text default to save, or default to clarify?
2. Should links default to save, or default to clarify?
3. How much metadata should be shown in save confirmations?
4. Should every saved item be chunked immediately, or only when needed?
5. Should ClawBot support "save + summarize" automatically in one turn when clearly requested?

## 24. Recommended Next Step

The next design step should be a technical design document for:

- intent router
- clarification flow
- item storage schema
- ingestion pipeline
- retrieval architecture

That document can then drive the first ClawBot-focused implementation milestone.
