# ClawBot V1 Technical Design

## 1. Overview

This document describes the technical design for `ClawBot V1`, based on the product requirements defined in:

- [clawbot-v1-product-spec.md](C:/Users/asta1/ai-project/Cora/docs/clawbot-v1-product-spec.md)

The goal is to implement the first working version of ClawBot as a reliable "personal archive assistant" that can:

- receive text, links, and simple files
- infer the user's intent
- ask clarification questions when uncertain
- save content as structured items
- retrieve and answer over saved materials later

This design intentionally favors fast delivery and stable foundations over framework-heavy abstraction.

## 2. Technical Goals

V1 should:

- support direct text input
- support link input
- support `.txt` and `.docx` file input
- support intent-driven turn routing
- support clarification-before-action when intent is uncertain
- persist structured knowledge items
- support keyword and vector retrieval
- support grounded answers based on retrieved evidence

V1 should avoid:

- premature multi-agent architecture
- over-reliance on agent frameworks
- complex asynchronous orchestration
- heavyweight document parsing for formats we do not need yet

## 3. Recommended Stack

### 3.1 Language And Runtime

- Python `3.11+`

### 3.2 App Layer

- `FastAPI`
- `Typer`
- minimal server-rendered HTML page

Usage split:

- `FastAPI` for the primary application interface
- `Typer` for local development, debug workflows, and admin utilities
- a minimal web page for simulating chat-style usage before WeChat integration

### 3.3 Validation And Settings

- `Pydantic v2`
- `pydantic-settings`

### 3.4 Database

- `SQLAlchemy 2.x`
- `SQLite` for V1
- `Alembic` for migrations

### 3.5 File / Content Parsing

- built-in file reading for plain text
- `python-docx` for `.docx`
- `Trafilatura` for web page extraction

### 3.6 Vector Retrieval

- `Qdrant Local` through `qdrant-client`

### 3.7 LLM And Embeddings

- OpenAI-compatible chat completion client
- OpenAI-compatible embeddings client
- `httpx` for provider calls

### 3.8 Testing

- `pytest`

## 4. Why This Stack

This stack is selected because it keeps the system:

- simple to understand
- fast to implement
- easy to run locally
- easy to evolve later

Specific reasoning:

- `FastAPI` gives a clean request/response API for future app integration
- `SQLite` is sufficient for a personal archive in V1
- `Qdrant Local` enables local semantic retrieval without requiring a running vector DB server
- `python-docx` and `Trafilatura` cover the first real input types with low complexity
- the current repository already uses `Pydantic`, `Typer`, and `httpx`, so reuse is high

## 5. High-Level Architecture

ClawBot V1 should be implemented as a small application with five main layers:

1. **API / CLI Layer**
2. **Local Web UI Layer**
3. **Turn Decision Layer**
4. **Execution Layer**
5. **Storage And Retrieval Layer**
6. **Provider Layer**

High-level request flow:

1. user sends content or asks a question
2. system normalizes the input
3. intent router classifies intent
4. policy decides whether to clarify or execute
5. execution layer performs capture / retrieve / organize
6. results are stored and returned

## 6. Core Modules

Recommended module layout:

```text
src/core/
  api/
    app.py
    templates/
      chat.html
    static/
    routes/
      ingest.py
      query.py
      clarification.py
      ui.py
  clawbot/
    service.py
    intent_router.py
    clarification.py
    policies.py
  ingestion/
    service.py
    normalizer.py
    parsers/
      base.py
      text_parser.py
      link_parser.py
      txt_parser.py
      docx_parser.py
  organization/
    summarizer.py
    tagger.py
    chunker.py
  retrieval/
    service.py
    keyword.py
    vector.py
    ranker.py
    grounded_qa.py
  embeddings/
    service.py
    openai_client.py
  llm/
    base.py
    openai_client.py
  storage/
    db.py
    models.py
    repositories/
  vectorstore/
    qdrant_local.py
  schemas/
    ...
```

## 7. Turn Handling Model

V1 should move away from a simple "chat -> tool loop" and instead use a two-phase turn model.

### 7.1 Decision Phase

Inputs:

- user text
- optional files
- current session state
- pending clarification state

Outputs:

- resolved intent
- confidence
- whether clarification is required
- candidate actions

### 7.2 Execution Phase

Possible execution paths:

- capture content
- retrieve saved materials
- organize content
- chat-only answer
- clarification response handling

This separation will keep behavior easier to reason about and test.

## 8. Intent Router Design

### 8.1 Responsibilities

The intent router should:

- inspect the current turn
- inspect whether the session is waiting for clarification
- classify the current user intent
- estimate confidence
- produce candidate actions

### 8.2 Intent Labels

Initial labels:

- `capture`
- `retrieve`
- `organize`
- `chat`
- `clarify_response`

### 8.3 Strategy

V1 should use:

- deterministic rules first
- LLM-assisted classification second
- policy thresholding third

### 8.4 Example Rule Heuristics

- file attached -> `capture`, high confidence
- only URL -> `capture`, medium confidence
- text contains "find", "where", "what was", "I sent before" -> `retrieve`
- text contains "summarize", "organize", "classify" -> `organize`
- active pending clarification -> `clarify_response`

### 8.5 Output Schema

```python
class IntentDecision(BaseModel):
    intent: Literal["capture", "retrieve", "organize", "chat", "clarify_response"]
    confidence: Literal["high", "medium", "low"]
    reason: str
    candidate_actions: list[str]
    needs_clarification: bool = False
```

## 9. Clarification Manager

### 9.1 Responsibilities

The clarification manager should:

- store unresolved intent decisions
- generate a short question for the user
- resolve the follow-up reply into a concrete action

### 9.2 Clarification State

Each unresolved turn should persist a clarification record so follow-up turns can resume the intended flow.

Minimum fields:

- `session_id`
- `source_message_id`
- `question`
- `candidate_intents`
- `pending_payload`
- `status`

### 9.3 Response Handling

If the user replies with a short answer such as:

- "save it"
- "summarize first"
- "just store it"

the router should interpret that response against the pending clarification state rather than treat it as a fresh standalone turn.

## 10. Ingestion Pipeline Design

### 10.1 Ingestion Responsibility

The ingestion service should convert raw inbound content into persisted knowledge items.

### 10.2 Input Types

Initial inbound content types:

- direct text
- single URL
- `.txt` file
- `.docx` file

### 10.3 Ingestion Steps

1. accept normalized input
2. determine `item_type`
3. extract text if necessary
4. normalize textual content
5. generate title
6. generate summary
7. generate tags
8. split into chunks
9. embed chunks
10. persist item and chunk records
11. index vectors

### 10.4 Parser Strategy

V1 should use parser interfaces rather than hardcode file logic into the ingestion service.

Suggested interface:

```python
class BaseParser(Protocol):
    def parse(self, source: ParsedSource) -> ParsedContent: ...
```

Initial implementations:

- `TextParser`
- `LinkParser`
- `TxtFileParser`
- `DocxFileParser`

This makes it easy to add a future `DoclingParser` or `PdfParser`.

## 11. Organization Layer

V1 organization features should be simple and deterministic where possible.

### 11.1 Title Generation

Prefer:

- use file name if available
- use page title for URLs if available
- otherwise ask the LLM for a short title

### 11.2 Summary Generation

Use the LLM to generate:

- a short 1 to 3 sentence summary

### 11.3 Tag Extraction

Use the LLM to extract:

- 3 to 8 concise tags

### 11.4 Chunking

Use simple chunking in V1:

- chunk by paragraphs first
- fallback to token/character-based chunking for long blocks
- keep metadata linking each chunk back to its item

V1 does not need an advanced semantic chunker.

## 12. Retrieval Design

V1 retrieval should combine structured filtering, keyword lookup, and semantic search.

### 12.1 Retrieval Pipeline

1. parse query
2. optionally infer filters
3. keyword search in SQLite FTS
4. vector search in Qdrant Local
5. merge and rank candidates
6. either:
   - return direct matches
   - or pass evidence into grounded QA

### 12.2 Keyword Search

Recommended:

- store searchable text in SQLite
- enable FTS5 virtual table for item and chunk search

### 12.3 Vector Search

Recommended:

- embed each chunk
- store vectors in Qdrant Local
- keep payload metadata:
  - `item_id`
  - `chunk_id`
  - `item_type`
  - tags
  - timestamps

### 12.4 Ranking

V1 ranking can be simple:

- weighted combination of keyword and vector scores
- small score boosts for exact title/tag hits

No reranker is required in V1.

## 13. Grounded QA Design

### 13.1 Responsibility

Grounded QA should answer user questions only from retrieved evidence.

### 13.2 Inputs

- user query
- retrieved chunks
- item metadata

### 13.3 Prompting Rules

The QA prompt should instruct the model to:

- answer only from the provided materials
- admit uncertainty if evidence is insufficient
- cite source item titles or timestamps when possible

### 13.4 Output Style

Recommended answer structure:

- direct answer
- source reference
- optional snippet

## 14. Data Model

V1 should extend the current database model with item-centric storage.

### 14.1 Existing Tables

Already present or conceptually aligned:

- `sessions`
- `messages`
- `events`

### 14.2 New Tables

Required:

- `items`
- `item_chunks`
- `clarification_states`

Optional but helpful:

- `item_tags`

## 15. Table Design

### 15.1 `items`

Purpose:

- one logical saved content unit

Suggested fields:

- `id`
- `session_id`
- `source_message_id`
- `item_type`
- `source_type`
- `title`
- `raw_content`
- `normalized_text`
- `summary`
- `metadata_json`
- `created_at`
- `updated_at`

For document items, metadata should also preserve file-location hints such as:

- `original_file_name`
- `saved_at`
- `approximate_time_hint`
- `locator_hint`

### 15.2 `item_chunks`

Purpose:

- retrieval units derived from items

Suggested fields:

- `id`
- `item_id`
- `chunk_index`
- `content`
- `metadata_json`
- `created_at`

### 15.3 `clarification_states`

Purpose:

- store unresolved turns that need user clarification

Suggested fields:

- `id`
- `session_id`
- `source_message_id`
- `question`
- `candidate_intents_json`
- `pending_payload_json`
- `status`
- `created_at`
- `updated_at`

### 15.4 FTS Tables

Recommended:

- `item_chunks_fts`

Indexed fields:

- chunk content
- title
- summary

## 16. Vector Store Design

### 16.1 Why Qdrant Local

Qdrant Local lets us:

- avoid a separate vector DB server in V1
- preserve compatibility with future Qdrant server migration
- keep vector logic simple

### 16.2 Collection Design

Recommended initial collection:

- `clawbot_chunks`

Payload fields:

- `item_id`
- `chunk_id`
- `item_type`
- `title`
- `tags`
- `created_at`

## 17. Embedding Strategy

### 17.1 V1 Approach

Use an OpenAI-compatible embeddings endpoint for:

- chunk embeddings
- optionally title+summary embeddings later

### 17.2 Embedding Granularity

Embed:

- each chunk

Do not embed:

- every message
- every metadata field

### 17.3 Model Recommendation

Use a small embedding model first for cost and speed.

The embedding service should be abstracted so it can later switch to:

- OpenAI-compatible provider
- local embedding model
- hosted vector inference

## 18. LLM Use Cases

ClawBot V1 needs LLM support for several tasks:

1. ambiguous intent classification fallback
2. title generation
3. summary generation
4. tag generation
5. grounded question answering

This should reuse a single LLM service abstraction, with task-specific prompts.

## 19. API Design

V1 should expose a small HTTP API.

### 19.1 `POST /sessions`

Create a conversation session.

Response:

- `session_id`

### 19.2 `GET /`

Serve a minimal local chat page for manual testing.

The page should support:

- text input
- link input via text box
- file upload
- clarification follow-up
- conversation history view

### 19.3 `POST /sessions/{session_id}/ingest`

Accept text, link, or file input.

Request options:

- text
- file upload
- optional metadata

Response:

- assistant reply
- action taken
- whether clarification is pending

### 19.4 `POST /sessions/{session_id}/reply`

Continue a conversation turn, including clarification responses and normal queries.

Response:

- assistant reply
- structured turn result

### 19.5 `GET /sessions/{session_id}/items`

List saved items.

### 19.6 `GET /sessions/{session_id}/items/{item_id}`

Fetch one item and its metadata.

### 19.7 `POST /sessions/{session_id}/query`

Ask a natural-language retrieval or grounded QA question.

Response:

- answer
- matched items
- evidence snippets

## 20. CLI Design

CLI remains useful for:

- local dev testing
- seed data ingestion
- manual retrieval debugging

Suggested commands:

- `core-cli chat`
- `core-cli ingest-text`
- `core-cli ingest-file`
- `core-cli query`

## 21. Suggested Repository Evolution

Current repository already contains runtime scaffolding. The next stage should reorganize around ClawBot-specific modules without discarding reusable base components.

Recommended evolution:

- keep generic runtime pieces in `core`
- add ClawBot-specific application layer under `core/clawbot`
- add ingestion and retrieval modules beside the generic agent components

## 22. Implementation Order

This is the recommended development sequence.

### Milestone 1: Storage Foundation

Build:

- SQLAlchemy setup
- new tables for `items`, `item_chunks`, `clarification_states`
- repositories

### Milestone 2: Basic Ingestion

Build:

- text ingestion
- link ingestion
- `.txt` file ingestion
- parser interfaces
- file metadata storage for later file recall

### Milestone 3: Intent + Clarification

Build:

- intent router
- confidence policy
- clarification manager
- pending clarification flow

### Milestone 4: Organization

Build:

- title generation
- summary generation
- tag generation
- chunking

### Milestone 5: Retrieval

Build:

- SQLite FTS search
- Qdrant Local integration
- merge-and-rank retrieval service

### Milestone 6: Grounded QA

Build:

- evidence assembly
- grounded answer generation

### Milestone 7: FastAPI

Build:

- minimal web chat page
- session APIs
- ingest APIs
- query APIs

## 23. Testing Strategy

V1 needs tests at several levels.

### 23.1 Unit Tests

For:

- intent routing
- clarification state handling
- parsers
- chunking
- ranking

### 23.2 Storage Tests

For:

- item persistence
- chunk persistence
- FTS queries

### 23.3 Integration Tests

For:

- text capture flow
- ambiguous input -> clarification -> save flow
- retrieval flow
- grounded QA flow

### 23.4 Provider Mocks

Use mocks/fakes for:

- LLM calls
- embeddings calls
- web extraction where possible

## 24. Risks And Mitigations

### Risk 1: Intent misclassification

Mitigation:

- hybrid rules + LLM
- clarification on low confidence

### Risk 2: Retrieval quality is weak

Mitigation:

- combine FTS and vector search
- keep chunks small and traceable

### Risk 3: Too much framework complexity

Mitigation:

- keep orchestration local and explicit
- avoid adopting heavy agent frameworks in V1

### Risk 4: Input parsing variability

Mitigation:

- start with only text, links, `.txt`, `.docx`
- keep parsers modular

### Risk 5: File cannot be directly returned in chat

Mitigation:

- store file recall metadata
- return date/time/context hints
- position V1 as a locator rather than a binary file return channel

## 25. Migration Path

This design leaves clean upgrade paths for:

- PDF support via Docling or another parser
- PostgreSQL instead of SQLite
- Qdrant server instead of Qdrant Local
- richer reranking
- WeChat or external app integration
- stronger memory or cross-session personalization

## 26. Final Recommendation

ClawBot V1 should be built as a focused application on top of the current `core` runtime, not as a generic large-scale agent framework.

The best V1 stack is:

- `FastAPI + Typer`
- `Pydantic + pydantic-settings`
- `SQLAlchemy + SQLite`
- `python-docx + Trafilatura`
- `Qdrant Local`
- OpenAI-compatible chat and embeddings

The most important implementation priority is:

- intent routing
- clarification
- structured ingestion
- hybrid retrieval
- grounded QA

If these are done well, ClawBot will already deliver a strong first version of the "smarter file transfer assistant" experience.
