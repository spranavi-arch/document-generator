legal_drafting_system/
│
├── backend/
│   │
│   ├── main.py                → Entry point (runs pipeline)
│   │
│   ├── config.py              → API keys & settings
│   │
│   ├── llm/
│   │   └── client.py          → LLM connection (OpenAI/Azure)
│   │
│   ├── blueprint/
│   │   ├── generator.py       → Finds sections (Step 1)
│   │   └── validator.py       → Checks blueprint quality
│   │
│   ├── extractor/
│   │   └── section_extractor.py → Splits samples into sections
│   │
│   ├── prompts/
│   │   └── prompt_builder.py  → Builds dynamic prompts (Step 2)
│   │
│   ├── draft/
│   │   └── draft_engine.py    → Generates sections (Step 3)
│   │
│   ├── assembler/
│   │   └── assembler.py       → Merges sections (Step 4)
│   │
│   ├── utils/
│   │   └── text_utils.py      → Cleaning helpers
│   │
│   └── storage/
│       └── templates.json     → Saved templates
│
├── data/
│   ├── samples/
│   │   ├── sample1.txt
│   │   └── sample2.txt
│   │
│   ├── case/
│   │   └── case_summary.txt
│   │
│   └── output/
│       └── final_draft.txt
│
├── frontend/                  → (Optional UI)
│
└── requirements.txt
