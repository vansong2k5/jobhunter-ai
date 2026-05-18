```
┌─────────────────────────────────────────────────────────────┐
│                    JOBHUNTER AI PLATFORM                    │
└─────────────────────────────────────────────────────────────┘

LAYER 1: DATA COLLECTION (Engineering)
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  LinkedIn    │    │  JobStreet   │    │  TopCV /     │
│  Jobs API    │    │  Scraper     │    │  VietnamWorks│    ← Dùng Selenium để crawl
│  (Python)    │    │  (Python)    │    │  Scraper     │ 
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       └──────────────────┼────────────────────┘
                          ▼
               ┌──────────────────┐
               │  n8n Scheduler   │  ← Chạy tự động mỗi 6h
               │  (Orchestration) │
               └────────┬─────────┘
                        ▼
LAYER 2: DATA STORAGE (Engineering)
               ┌──────────────────┐
               │   PostgreSQL     │  ← Raw job data
               │   + pgvector     │  ← Job embeddings cho RAG
               └────────┬─────────┘
                        ▼
LAYER 3: DATA TRANSFORMATION (Analytics Engineering)
               ┌──────────────────┐
               │  Python ETL      │  ← Clean, normalize, deduplicate
               │  (pandas + SQL)  │  ← Tính job market stats
               └────────┬─────────┘
                        ▼
LAYER 4: AI INTELLIGENCE (AI/LLM Layer)
       ┌────────────────┼────────────────────┐
       ▼                ▼                    ▼
┌─────────────┐  ┌─────────────┐   ┌─────────────────┐
│  CV Parser  │  │  Job Match  │   │  CV Optimizer   │
│  (Claude)   │  │  RAG Engine │   │  (Claude)       │
│             │  │  (pgvector) │   │                 │
└──────┬──────┘  └──────┬──────┘   └────────┬────────┘
       └─────────────────┼───────────────────┘
                         ▼
LAYER 5: AUTOMATION (n8n Workflows)
               ┌──────────────────┐
               │  Auto-Apply      │  ← Gửi CV theo batch
               │  Email Sender    │  ← Customized cover letter
               │  Status Tracker  │  ← Track responses
               └────────┬─────────┘
                        ▼
LAYER 6: ANALYTICS & REPORTING (Business Intelligence)
               ┌──────────────────┐
               │  Streamlit UI    │  ← Main interface
               │  + Dashboard     │
               │  Power BI Report │  ← Deep analytics (show to HR)
               └──────────────────┘
```
