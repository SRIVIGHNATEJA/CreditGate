# 🛡️ CreditGate
**Enterprise MSME Underwriting Copilot & Deterministic Risk Decision-Support System**

> 🔐 Licensed under **Apache License 2.0**  
> 📌 Developed by **Sri Vighna Teja** | 2026 | Portfolio Project  
> 🚀 **Live Production Application:** [creditgate.streamlit.app](https://creditgate.streamlit.app)

<div align="center">

  [![Live Demo](https://img.shields.io/badge/Live_Demo-creditgate.streamlit.app-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)](https://creditgate.streamlit.app)
  [![Release](https://img.shields.io/badge/Release-v1.0.0-0A84FF?style=for-the-badge&logo=github&logoColor=white)](https://github.com/SRIVIGHNATEJA/CreditGate/releases/tag/v1.0.0)
  [![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
  [![Pydantic](https://img.shields.io/badge/Pydantic-v2.8-E92063?style=for-the-badge&logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
  [![Math](https://img.shields.io/badge/Math-100%25_Python_Decimal-0F766E?style=for-the-badge)](https://docs.python.org/3/library/decimal.html)
  [![Audit](https://img.shields.io/badge/Audit-SHA--256_Hash_Chain-0B1F3A?style=for-the-badge)](https://en.wikipedia.org/wiki/Cryptographic_hash_function)
  [![Keep-Alive](https://img.shields.io/badge/Uptime-Autonomous_Playwright-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)](https://github.com/SRIVIGHNATEJA/CreditGate/actions)
  [![Pytest](https://img.shields.io/badge/Pytest-113_Tests_Passing-green?style=for-the-badge&logo=pytest&logoColor=white)](https://docs.pytest.org/)

</div>

---

> **The Challenge:** Commercial MSME credit appraisal takes 5 to 10 working days per borrower. Underwriters manually cross-reconcile hundreds of pages of bank statements, GSTR-3B filings, and ITR tax returns. Generic conversational LLMs hallucinate calculations, fail floating-point financial math, leak sensitive PII, and cannot satisfy Reserve Bank of India (RBI) regulatory explainability audits.
> 
> **The Solution:** A decoupled underwriting copilot that uses multimodal AI strictly for document digitization and entity disambiguation, while delegating 100% of risk scoring, DSCR amortization, and credit committee policy gates to a deterministic, fixed-precision Python `Decimal` core protected by cryptographic SHA-256 audit chaining.

---

## 🚀 Business Value & Technical Impact

Engineered for commercial lending institutions, private credit desks, and NBFCs requiring strict regulatory compliance and reproducible credit sanction decisions:

*   **Underwriting Cycle Reduction:** Slashes initial MSME financial appraisal time from **5–7 business days to <15 minutes** without sacrificing analytical rigor.
*   **Zero Math Hallucination:** Eliminates LLM calculation risks by enforcing pure Python `Decimal` fixed-precision arithmetic for Debt-Service Coverage Ratios (DSCR), monthly EMI obligations, and Five-Cs weighted scores.
*   **Cross-Document Forensic Triangulation:** Automatically reconciles taxable turnover (GSTR-3B) against annual bank collections and ITR gross income, flagging circular trading when revenue variance exceeds **10%**.
*   **Statutory Judicial Surveillance:** Screens applicant entities across **672 District Courts (e-Courts NJDG)**, **16 NCLT Benches (IBC 2016)**, **IBBI**, and **DRT/SARFAESI** registries with strict PAN/CIN disambiguation.
*   **Cryptographic Audit Provenance:** Every human adjudication, parameter adjustment, and override is stamped in a tamper-evident SHA-256 hash-chained ledger, exporting formal Microsoft Word (`.docx`) and printable PDF (`.pdf`) Credit Appraisal Memoranda (CAM).
*   **Autonomous Operational Resilience:** Employs an off-peak headless Playwright keep-alive workflow that resets Cloud runtime sleep counters in under 35 seconds without user intervention.

---

## 🏛️ System Architecture

CreditGate implements a decoupled, 4-stage pipeline that enforces complete separation between unstructured document extraction and deterministic credit policy execution.

```mermaid
flowchart LR
    %% Colors and Styles
    classDef intake fill:#f8fafc,stroke:#cbd5e1,stroke-width:1px,color:#0b1f3a;
    classDef extract fill:#f0fdfa,stroke:#99f6e4,stroke-width:1px,color:#0f766e;
    classDef core fill:#eff6ff,stroke:#bfdbfe,stroke-width:1px,color:#1e40af;
    classDef gov fill:#fef2f2,stroke:#fecaca,stroke-width:1px,color:#b91c1c;
    classDef export fill:#f0fdf4,stroke:#bbf7d0,stroke-width:1px,color:#15803d;

    subgraph Stage1 ["1. Intake & Master Profile"]
        A["Borrower Profile<br/>(MCA-21 CIN / PAN)"]:::intake --> B["Sector Taxonomy<br/>(NIC-2008 & Vintage)"]:::intake
        B --> C["Facility Calibration<br/>(Amount, Rate, Tenure)"]:::intake
    end

    subgraph Stage2 ["2. Evidence & Triangulation"]
        D["Multi-Doc Ingestion<br/>(Bank, GSTR-3B, ITR)"]:::extract --> E["Multimodal Extraction<br/>(Gemini / Offline Regex)"]:::extract
        E --> F["Reconciliation Horizon<br/>(Accrual-Cash Gap)"]:::extract
        F --> G{"HITL Verification Gate<br/>(Lock Financials)"}:::extract
    end

    subgraph Stage3 ["3. Deterministic Decision Engine"]
        G -- "Locked State" --> H["Deterministic Five Cs<br/>(Python Decimal Math)"]:::core
        H --> I["Statutory Legal Radar<br/>(eCourts, NCLT, IBBI)"]:::core
        I --> J["Policy Gate & Knockouts<br/>(DSCR & Bounces)"]:::core
        J --> K["Quorum Governance Shield<br/>(Level-3 Dual-Signoff)"]:::gov
    end

    subgraph Stage4 ["4. Artifacts & Ledger"]
        K --> L["Cryptographic Audit Trail<br/>(SHA-256 Hash Chain)"]:::export
        L --> M["Formal CAM Export<br/>(.docx & .pdf Memoranda)"]:::export
    end

    C -. "Sanction Terms" .-> H
```

---

## 💡 Engineering & Consulting Decisions

In technical advisory and enterprise banking, architectural choices must withstand model risk management and regulatory scrutiny:

| Requirement / Challenge | Technical Implementation | Consulting & Regulatory Rationale |
| :--- | :--- | :--- |
| **Zero Math Hallucination** | **Pure Python `Decimal`** (Fixed-precision 2-4 decimal places) | Generative LLMs frequently make subtle floating-point arithmetic errors. Financial ratios (DSCR, EMI, weighted scores) must be 100% deterministic and legally reproducible. |
| **Separation of Extraction & Decision** | **Strict Pydantic V2 Schemas** (`CreditCase`, `BankExtraction`) | LLMs are restricted to structured document extraction and summarization. The credit scoring and knockout decisions are executed entirely by code without model discretion. |
| **Dual-Engine SLA Circuit Breaker** | **Module-Level ThreadPool & Deterministic Fallback** | Wraps cloud multimodal inference in a strict 6.0s timeout pool. If the API latency spikes or gateway errors occur, the pipeline instantly fails over to local PyMuPDF regex extraction. |
| **Human-in-the-Loop Governance** | **Mandatory Financial Lockout Gate** (`st.data_editor`) | Prevents automated pipeline execution on unverified OCR output. An underwriter must review, calibrate, and cryptographically lock financial tables before scoring triggers. |
| **Statutory False-Positive Adjudication** | **Legal Docket Citations & Adjudication** (`CONFIRMED` / `DISMISSED`) | In Indian commercial law, company homonyms cause severe credit misdiagnoses. Underwriters can review order citations (e.g., NCLT CIRP vs. homonym dismissal) with 1-click audit logging. |
| **Insolvency Lockout Rule** | **Immutable Policy Shield** | Discretionary committee overrides are permanently disabled if an admitted SEVERE insolvency finding (e.g., NCLT Section 7/9) is confirmed, preventing corrupt or rogue sanctions. |
| **Tamper-Evident Accountability** | **SHA-256 Event Chaining** (`verify_chain()`) | Creates an unalterable audit ledger from the genesis block (`e3b0c442`) to final memorandum export, satisfying internal risk and statutory RBI audit inspections. |
| **High-Uptime Cloud Availability** | **Headless Playwright Keep-Alive** (Off-peak cron `23 1,9,17 * * *`) | Prevents cloud runtime cold sleeps. Executes automated WebSocket sessions off peak-hour load, maintaining 100% warm availability for evaluators. |

---

### Human-in-the-Loop Financial Verification & Quorum Sequence

```mermaid
sequenceDiagram
    autonumber
    participant U as Credit Underwriter
    participant UI as Underwriting Terminal (Streamlit)
    participant EX as Multimodal Extraction Engine
    participant SC as Deterministic Scoring Core
    participant AL as SHA-256 Audit Ledger
    participant CC as Credit Committee (Dual-Signoff)

    U->>UI: Upload Financial PDFs (Bank, GST, ITR)
    UI->>EX: Ingest & Parse Documents (Gemini / PyMuPDF)
    EX-->>UI: Populate Verification Grid (Pydantic Model)
    Note over U,UI: Human-in-the-Loop Verification Gate
    U->>UI: Review Extracted Line Items & Click "Lock Financials"
    UI->>AL: Record FINANCIALS_LOCKED (SHA-256 Hash Stamped)
    UI->>SC: Compute Five Cs Scorecard & DSCR (Python Decimal)
    SC-->>UI: Render Baseline Verdict (APPROVE / CONDITIONAL / REJECT)
    
    alt Discretionary Override Attempted
        CC->>UI: Input Committee Written Justification (>20 chars)
        opt Confirmed Insolvency Exists
            UI-->>CC: ⛔ Governance Lockout Enforced (Override Prohibited)
        end
        CC->>UI: Authorize Level-3 Dual-Signoff
        UI->>AL: Record EXECUTIVE_OVERRIDE_RECORDED
    end

    UI->>AL: Verify Audit Ledger Chain Integrity
    AL-->>UI: Integrity Confirmed (Chain Valid)
    UI->>U: Export Formal Word (.docx) & Committee Sign-off PDF (.pdf)
```

---

## 📊 Empirical Validation: Benchmark Cohort Gallery

To validate deterministic stability under varying financial stress, CreditGate is evaluated against three golden benchmark cohorts at standard facility terms (₹50.00 L @ 11.50% for 36 months):

<div align="center">

| Benchmark Cohort | Financial Profile | Forensic Signals | DSCR | Deciding Rule | Final Verdict |
| :--- | :--- | :--- | :---: | :--- | :---: |
| **Cohort 1: Prime MSME**<br/>*(Apex Industrial Benchmark)* | Revenue: ₹10.40 Cr<br/>PAT: ₹0.85 Cr<br/>Existing Debt: ₹1.20 Cr | Clean MCA RoC records<br/>0 cheque bounces<br/>Clean judicial dockets | **1.62x** | Baseline Five-Cs Weighted Score (**96.3 / 100**) | <span style="color:#15803d; font-weight:700;">APPROVE</span> |
| **Cohort 2: Distressed**<br/>*(Kaveri Logistics Benchmark)* | Revenue: ₹6.20 Cr<br/>PAT: ₹0.05 Cr<br/>Existing Debt: ₹3.10 Cr | 5 mandate cheque bounces<br/>Active NCLT Sec 9 CIRP<br/>Negative operating cash flow | **0.88x** | Policy Knockout:<br/>`BOUNCE_KNOCKOUT` (≥3 bounces) | <span style="color:#b91c1c; font-weight:700;">REJECT</span> |
| **Cohort 3: Mismatch Trap**<br/>*(Bharat Structural Benchmark)* | Revenue: ₹14.80 Cr<br/>PAT: ₹0.18 Cr<br/>Existing Debt: ₹4.50 Cr | 12.5% GST-Bank turnover gap<br/>1 mandate bounce<br/>Pending GST demand notice | **1.04x** | Weakest-Link Cap:<br/>`CONDITIONS` pillar capped at 35.0 | <span style="color:#b45309; font-weight:700;">CONDITIONAL</span> |

</div>

---

## 🔍 Forensic Engineering & Root Cause Analyses (RCA)

Enterprise software engineering is proven when diagnosing and resolving silent system failures. Below are four key engineering incident spot-checks solved during development:

### 1. Incident Spotlight: Floating-Point Division Drift in DSCR
*   **The Symptom:** Under standard IEEE 754 floating-point math, a borrower with Cash Available for Debt Service (CADS) of `₹2,062,500.00` and Total Debt Service of `₹1,650,000.00` produced a computed DSCR of `1.2499999999999998x`. The policy gate rule strictly requires `DSCR >= 1.25x`, erroneously triggering a conditional covenant penalty.
*   **The Root Cause:** Binary floating-point representation cannot represent fractional decimal values precisely in memory.
*   **The Remediation:** Replaced all financial math across `core/analyser.py` and `core/scorer.py` with Python's fixed-precision `decimal.Decimal` with explicit `ROUND_HALF_UP` quantizing. `Decimal('2062500.00') / Decimal('1650000.00')` evaluates cleanly to `1.2500x`, eliminating spurious policy rejections.

### 2. Incident Spotlight: Google Gateway 400 & ThreadPool Context Manager Lock
*   **The Symptom:** Under high concurrency, invoking Google's Gemini API with client timeouts triggered an `HTTP 400 Bad Request` (`X-Server-Timeout < 10000ms`), while wrapping fallback calls in Python's `with ThreadPoolExecutor()` blocked the thread because `__exit__` calls `shutdown(wait=True)`.
*   **The Root Cause:** Cloud API gateways enforce minimum server timeout constraints, and ephemeral context managers prevent immediate failover by forcing the caller to wait for the hanging child thread to finish.
*   **The Remediation:** Removed invalid client headers, instantiated a module-level persistent `_GEMINI_EXECUTOR = ThreadPoolExecutor(max_workers=4)`, and decoupled failover execution so timeout triggers immediately drop into the deterministic PyMuPDF extractor without thread starvation.

### 3. Incident Spotlight: Session State Widget Pre-Population Collision
*   **The Symptom:** When users switched borrower cohorts or navigated between intake screens, the server console printed repeated warning stack traces: `The widget with key "..." was created with a default value but also had its value set via the Session State API`.
*   **The Root Cause:** Instantiating widgets with both an explicit `value=...` parameter and a `key="case_cin_input"` when that key is already bound in `st.session_state` causes Streamlit's internal validator to flag a state collision.
*   **The Remediation:** Cleanly decoupled widget initialization into `ui/state.py: init_session_state()`, initializing the default values directly into `st.session_state` and omitting redundant `value` constructor overrides, completely silencing the console output.

### 4. Incident Spotlight: Discretionary Override Boundary Leak on Admitted Insolvency
*   **The Symptom:** An officer could theoretically activate an executive veto to override a loan rejection even when the applicant had an admitted Corporate Insolvency Resolution Process (CIRP) order at NCLT.
*   **The Root Cause:** The executive override logic operated at the aggregate score level without inspecting individual finding severity tiers.
*   **The Remediation:** Implemented an immutable governance shield: if any adverse finding marked as `FindingTier.SEVERE` is in `ReviewStatus.CONFIRMED` state, discretionary veto toggles are locked out at the UI layer, and the scoring engine forcefully revokes overrides if an officer confirms severe litigation later in the session.

---

## 💻 Underwriting Terminal Workflow (4-Stage Pipeline)

<table width="100%" style="border-collapse: collapse;">
  <tr>
    <td width="50%" align="center" style="border: none; padding: 10px;">
      <b>🏛️ 1. Case Onboarding & Terms Calibration</b><br/><br/>
      <p align="left"><i>Validates MCA-21 Corporate Identification Number (CIN) syntax, checks PAN entity consistency (Company 'C' vs Proprietor 'P'), auto-classifies NIC-2008 sector codes, and calibrates proposed facility terms.</i></p>
    </td>
    <td width="50%" align="center" style="border: none; padding: 10px;">
      <b>📄 2. Document Extraction & Forensic Triangulation</b><br/><br/>
      <p align="left"><i>Parallel multimodal ingestion of Bank Statements, GSTR-3B filings, and ITR tax forms. Reconciles accrual turnover vs bank credits, detects mandate bounces, and requires mandatory underwriter lock.</i></p>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center" style="border: none; padding: 10px;">
      <b>⚖️ 3. Deterministic Five Cs & Legal Radar</b><br/><br/>
      <p align="left"><i>Evaluates Character, Capacity, Capital, Collateral, and Conditions entirely in Decimal arithmetic. Screens e-Courts, NCLT, and IBBI dockets, provides 1-click legal citations, and enforces dual-signoff quorum.</i></p>
    </td>
    <td width="50%" align="center" style="border: none; padding: 10px;">
      <b>📑 4. Formal Word & PDF CAM Export</b><br/><br/>
      <p align="left"><i>Generates complete Credit Appraisal Memoranda compliant with commercial banking committee standards. Exports styled Microsoft Word (.docx) and printable PDF (.pdf) appraisal packages with full PII masking.</i></p>
    </td>
  </tr>
</table>

---

## 🧪 Quality Assurance & CI/CD Readiness

The codebase is fortified by an automated test suite across parsing, math, policy, and artifact compilation:

*   **Headless UI & Lifecycle Tests (14):** Verifies full end-to-end user navigation, tab routing, session recovery, and widget state synchronization via `streamlit.testing.v1.AppTest`.
*   **Financial Math & Invariant Tests (28):** Verifies pure Decimal DSCR calculations, amortization EMI precision, accrual-cash gap formulas, and CGTMSE collateral credit rules.
*   **Policy Gate & Knockout Tests (18):** Validates bounce thresholds, weakest-link pillar caps, minimum business vintage requirements, and severe litigation knockout triggers.
*   **Statutory Surveillance & Legal Radar Tests (20):** Tests MCA-21 CIN syntax, 4th-character PAN taxonomy, e-Courts citation matching, and anti-clean-chit watchlist interception.
*   **CAM & Export Integrity Tests (15):** Tests ReportLab PDF compilation, python-docx XML entity escaping, and SHA-256 hash-chain verification (`verify_chain()`).
*   **Layout & Governance Guard Tests (18):** Validates zero five-column metric row collapses, zero CommonMark code-block indents, and immutable executive override lockout rules.

```bash
# Execute full test suite in virtual environment
pytest tests/ -q
# Output: 113 passed, 7 warnings in 13.60s
```

---

## 🚀 Quickstart & Reproduction

```bash
# 1. Clone & Setup Python Virtual Environment
git clone https://github.com/SRIVIGHNATEJA/CreditGate.git
cd CreditGate
python3 -m venv .venv && source .venv/bin/activate

# 2. Install Production Dependencies
pip install -r requirements.txt

# 3. Configure API Credentials (Optional - Default runs on offline parser & benchmark dockets)
cp .env.example .env
# Edit .env:
# GOOGLE_API_KEY=your_gemini_api_key_here

# 4. Run Automated Test Suite
pytest tests/ -q

# 5. Launch Underwriting Copilot Terminal
streamlit run app.py
```

*The terminal will launch and bind automatically to `http://localhost:8501`.*

---
<div align="center">
  <i>Architected to demonstrate institutional financial engineering, deterministic AI guardrails, and regulatory-grade commercial underwriting systems.</i>
</div>
