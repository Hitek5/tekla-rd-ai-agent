# Meeting Brief for Kudryashov Discussion

## Recommended message

We can support the prototype, but the first milestone should be a safe local agent with RAG and controlled Tekla tools, not immediate model training. Fine-tuning becomes useful after we collect a clean, reviewed dataset of tasks and correct Tekla API outputs.

## Questions to settle

- Which Tekla Structures versions must be supported?
- Which first three use cases matter most: modeling, reinforcement, drawings, validation, or RD drafting?
- Are current examples C# source files, macros, Tekla plugins, or screenshots/manual notes?
- Can the pilot work only on copied models?
- Who approves promotion from generated code to typed tool?
- Who owns the verified training dataset?

## Proposed first milestone

In 2-4 weeks:

- local LLM endpoint in KSPD;
- RAG over Tekla examples and internal docs;
- read-only Tekla host;
- dry-run create beam/column/rebar tools;
- audit log;
- fixed eval set with baseline metrics.

