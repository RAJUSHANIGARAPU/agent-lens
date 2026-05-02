# Demo Storyboard: 15-Second Agent-Lens Demo

## Setup
- Terminal with `python examples/04_pause_and_fork.py` running
- Browser open at `http://127.0.0.1:7878`
- Screen recorded at 1920×1080, 60fps

---

## Frame 1 (0:00 – 0:02) — The Problem

**Screen**: Terminal showing a multi-step agent running. LLM calls scroll by.

**Narration / Caption**: "Your agent just made 5 LLM calls and got a wrong answer. To test a fix at step 3, you have to restart and wait through steps 1 and 2 again."

**Action**: Show agent output, then the disappointment of a wrong final answer.

---

## Frame 2 (0:02 – 0:04) — One Line of Setup

**Screen**: Code editor. Zoom in on two lines.

```python
import agent_lens
agent_lens.install()
```

**Caption**: "agent-lens. One import. Zero infra."

---

## Frame 3 (0:04 – 0:06) — Dashboard Opens

**Screen**: Browser window animates open. Dark dashboard appears with a run in the left panel. Run is marked "RUNNING" (blue badge, pulsing dot).

**Caption**: "Dashboard opens automatically at localhost:7878"

---

## Frame 4 (0:06 – 0:07) — Pause

**Screen**: Mouse clicks the **⏸ Pause** button in the top bar. Run badge switches to "PAUSED" (yellow, pulsing).

**Caption**: "Pause the running agent."

---

## Frame 5 (0:07 – 0:09) — Tree View

**Screen**: Left panel shows the span tree. User clicks on span "step-2-research". Inspector panel shows the messages array: system prompt + user message.

**Caption**: "Inspect exactly what the LLM received at step 2."

---

## Frame 6 (0:09 – 0:11) — Fork Modal

**Screen**: User clicks **⑂ Fork** button. Modal slides in. Messages textarea is pre-populated with the current messages. User edits the user message from "What are Python 3.12 features?" to "What are the PERFORMANCE improvements in Python 3.12?".

**Caption**: "Edit the message. Fork from this point."

---

## Frame 7 (0:11 – 0:12) — Fork Created

**Screen**: User clicks **⑂ Fork** in the modal. Left panel shows two runs: `original-agent` (paused) and `original-agent [fork]` (running). The forked run's spans start populating in real time.

**Caption**: "A new run diverges from step 2 with your edited message."

---

## Frame 8 (0:12 – 0:13) — Resume Original

**Screen**: User selects the original run in the left panel. Clicks **▶ Resume**. Status badge changes to "RUNNING". Both runs now show "RUNNING" simultaneously.

**Caption**: "Resume the original. Both runs execute in parallel."

---

## Frame 9 (0:13 – 0:15) — Compare Results

**Screen**: Split view (or toggle) showing:
- Original run Inspector: "Python 3.12 features include improved error messages, f-string enhancements..."
- Forked run Inspector: "Python 3.12 performance: perf-opt specializing adaptive interpreter, 10-60% speedup..."

**Caption**: "Same starting point. Different question. Different answer. Zero restarts."

---

## Frame 10 (0:15) — End Card

**Screen**: agent-lens logo + tagline on dark background.

```
agent-lens
The interactive debugger for AI agents.

pip install agentlens
```

---

## Recording Notes

- Record at 2x speed and play at 1x, or record 30-second version and trim to 15s
- Use a demo agent that runs in 3-4 seconds per LLM call (mock calls)
- Pre-populate the database with a few completed runs for visual richness
- Use keyboard shortcuts (Space to pause, F to fork) for cinematic feel
- Zoom into code at 150% for readability
- Terminal font: JetBrains Mono 14pt
- Browser zoom: 90% to fit dashboard
- Cursor: large, high-contrast

## Screen Resolution

- Dashboard: 1200×800 browser window
- Terminal: 900×400, 2/3 of screen
- Recording tool: OBS or macOS built-in screen recording
