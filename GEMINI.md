# Antigravity Workspace Instruction & Memory Hook

## MANDATORY PROTOCOL: Continuous Project Memory Logging

You are working on the **NectarLink Reel Studio** repository.

Whenever you make **ANY** code modification, bug fix, feature addition, refactoring, or tuning in this project, you **MUST** immediately append an entry to the end of [`PROJECT_MEMORY.md`](file:///e:/Download/Code/Platfrom-tool/PROJECT_MEMORY.md) before concluding your turn.

### Required Entry Format
```markdown
### [YYYY-MM-DD HH:MM:SS] <Headline / Title>
- **User Request**: "<Exact or paraphrased user instruction>"
- **Context & Problem**: <What was the issue or requirement? Why did it happen?>
- **Action Taken & Changes**:
  - `<file_path>`: <Exact changes made in code>
- **Technical Mechanism**: <How the fix/feature works under the hood>
- **Verification & Outcome**: <How was it tested/verified? What was the result?>
```

### Core Architecture Context
- **Target Device**: Android device (`LD7j-AS` / `0624531123033570`).
- **Core Automation**: [`adb.py`](file:///e:/Download/Code/Platfrom-tool/adb.py) — 11-step pipeline with state verification & self-healing.
- **Anti-Bot Engine**: [`human_behavior.py`](file:///e:/Download/Code/Platfrom-tool/human_behavior.py) — Bézier curves, Gaussian click jitter, dwell times, 10x typing cadence.
- **Web Studio**: [`app.py`](file:///e:/Download/Code/Platfrom-tool/app.py) + [`templates/index.html`](file:///e:/Download/Code/Platfrom-tool/templates/index.html) running at `http://127.0.0.1:5000`.
- **Full Specifications**: See [`PRD.md`](file:///e:/Download/Code/Platfrom-tool/PRD.md).
