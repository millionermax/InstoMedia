# Instoper Documentation

Welcome to the technical documentation for **Instoper** — an enterprise-grade automated Instagram Reel publishing and account automation platform built for Android device farms.

---

## Documentation Directory

| Document | Location | Purpose |
|:---|:---|:---|
| **Product Requirements Document (PRD)** | [`PRD.md`](./PRD.md) | Full architectural specifications, anti-bot biometric models, state machines, and system design. |
| **Code Review & Audit Notes** | [`ANTIGRAVITY_REVIEW_NOTES.md`](./ANTIGRAVITY_REVIEW_NOTES.md) | Security, device selection, APK installation, and code review criteria. |
| **Implementation Plans** | [`plans/README.md`](./plans/README.md) | Archive of all 11 modular implementation plans, design systems, and migration workflows. |
| **Workspace Instructions** | [`../GEMINI.md`](../GEMINI.md) | Continuous memory protocol and workspace guidelines. |
| **Project Memory** | [`../PROJECT_MEMORY.md`](../PROJECT_MEMORY.md) | Continuous changelog and operational history. |
| **Root Quickstart** | [`../README.md`](../README.md) | Operator quickstart, installation commands, and CLI invocation. |

---

## Architectural Layout

Following the safety-first architectural migration, Instoper is organized into clear domain packages:

```text
Platfrom-tool/
├── app.py                     # Flask entry point and compatibility launcher
├── requirements.txt           # Python dependency manifest
├── start_gui.bat              # Windows 1-click launcher
├── .gitignore                 # Version control ignores
├── GEMINI.md                  # Workspace instructions and mandatory memory hook
├── PROJECT_MEMORY.md          # Continuous architectural and bugfix log
│
├── backend/                   # Backend application logic
│   ├── routes/                # Domain-specific Flask Blueprints (accounts, devices, tasks, etc.)
│   ├── services/              # Cohesive business logic services & excel_manager
│   └── storage/               # SQLite database access (db.py) and credential encryption (vault.py)
│
├── automation/                # Android automation engine
│   ├── instagram/             # Reel publishing workflow & clone login automation
│   ├── android/               # Package scanning, APK installation, and profile capture
│   ├── interaction/           # Human behavior engine (Bézier curves, Gaussian jitter)
│   └── observability/         # Task screen recording & retention
│
├── frontend/                  # Presentation layer
│   ├── templates/             # Jinja2 templates (index.html)
│   └── static/                # Tailwind CSS, JavaScript, and UI assets
│
├── docs/                      # Central documentation repository
│   ├── README.md              # Documentation overview (this document)
│   ├── PRD.md                 # Product Requirements Document
│   ├── ANTIGRAVITY_REVIEW_NOTES.md # Code review audit checklist
│   └── plans/                 # Architectural implementation plans
│
├── data/                      # Persistent runtime SQLite database (instoper.db)
├── uploads/                   # Runtime video uploads and task artifacts
└── tests/                     # Automated test suites (test_full_suite.py)
```

---

## Anti-Bot & Stealth Engine

Instoper avoids Instagram bot detection by automating real physical Android devices via `uiautomator2` and pure-Python biometric modeling:
- **Cubic Bézier Curve Trajectories**: Non-linear touch arcs with natural sinusoidal acceleration profiles.
- **Gaussian Click Distribution**: Normal distribution jitter ($\sigma = 2\text{px}-4\text{px}$) away from button coordinates.
- **Micro Touch Dwell Times**: Genuine physical `MotionEvent` touch-down (65ms–125ms) and touch-up cycles.
- **Human Cadence Typing**: Word-chunk typing cadence with micro-jitter (5ms–12ms).
- **11-Step Self-Healing Pipeline**: Verifies screen transitions (audio -> camera -> gallery -> preview -> caption -> privacy -> share -> upload -> link capture) with popup auto-dismissal.

---

## Operational Verification

Run the full automated test suite to verify platform health across all packages:
```bash
python -m unittest tests/test_full_suite.py
```
To launch the Web Studio dashboard:
```bash
python app.py
```
Access the dashboard at `http://127.0.0.1:5000`.
