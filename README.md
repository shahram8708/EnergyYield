# EnergyYield IoT Solar Tracker Platform

### 1️⃣ Project Title & Professional Description
- **Project Name:** EnergyYield
- **Short Explanation:** Web platform to monitor, optimize, and remotely control small solar trackers in real time.
- **What This Platform Is:** A Flask-based web app + REST API with analytics, AI diagnostics, and a built-in digital-twin simulator for solar tracking devices.
- **Problem It Solves:** Gives operators a single place to ingest telemetry, issue commands, analyze efficiency, and detect issues (dust, shading, power rail drops) without bespoke tooling.
- **Who Should Use It:** Hardware teams, solar/IoT engineers, operations teams, and researchers running distributed solar trackers or mini PV experiments.
- **Where It’s Useful:** Remote solar deployments, labs, field trials, education demos, and any scenario needing fine-grained solar tracker observability and control.
- **Why It Exists / Motivation:** To shorten feedback loops between hardware telemetry and operational decisions, reduce wasted moves, and surface actionable maintenance and energy insights automatically.

### 2️⃣ Table of Contents
1. Project Title & Professional Description  
2. Table of Contents  
3. Project Overview  
4. Key Features  
5. System Architecture & Technology Stack  
6. Technical Deep Dive  
7. Installation & Setup Guide  
8. Usage Guide  
9. Environment Variables / Configuration  
10. Project Screens / Pages Description  
11. Security & Privacy Notes  
12. Performance & Optimization Notes  
13. Limitations & Known Issues  
14. Future Enhancements  
15. Real-World Value & Business Perspective  
16. Contribution Guidelines  
17. License  
18. Final Professional Conclusion

### 3️⃣ Project Overview (Detailed Explanation)
- **What the project does:** Collects device telemetry/events, visualizes live and historical performance, computes slot/angle analytics, queues remote commands, and generates AI-driven diagnostics and recommendations.
- **Platform type:** Web app + REST API + background analytics + optional digital-twin simulator (SaaS-style control panel).
- **Purpose & goal:** Maximize solar tracker yield while minimizing wasted movements and maintenance overhead.
- **Real-world applications:** Remote asset monitoring, preventative maintenance, firmware validation, and operational tuning for PV trackers.
- **Target audience / users:** Device owners, field engineers, data/analytics teams, and researchers.
- **Industry relevance:** Distributed energy resources (DER), IoT, renewable energy operations, and asset performance management.
- **Vision & scope:** Provide end-to-end observability (data capture → analysis → action) with safety controls, AI summaries, and simulator-backed testing.

### 4️⃣ Key Features (Extremely Detailed)
**Core Features**
- **Telemetry Ingestion (/api/telemetry):** Stores per-minute voltage/current/power/angle/energy and health metrics; keeps device online status fresh.
- **Event Logging (/api/event):** Captures device-side events (moves, resets, low supply, sensor faults, cleanings) for forensics and maintenance context.
- **Command Queueing (/api/send_cmd, /api/cmd, /api/cmd_ack):** Issue and track remote commands (set mode, set angle, thresholds, request snapshot) with acknowledgement.
- **Device Settings:** Persist per-device mode, move thresholds, and motor power parameters; exposed via UI and API.

**Major Features**
- **Live Dashboard:** Real-time cards and charts (power, energy, angle) with device status and last-seen timestamps.
- **History Replay:** Day-level playback of power/angle/energy timelines plus slot averages.
- **Control Panel:** Remote mode/angle/threshold updates, snapshot requests, and command history with status badges.
- **Analytics & Optimizer:** Slot/angle heatmap, best-angle table, movement efficiency stats, daily performance (7d), and net-gain decision table.
- **AI Diagnostics:** Dust vs. shading inference, sensor health scoring, power-rail risk, RTC reliability, efficiency score, recommendations, and narrative explanations (Gemini-powered).
- **Maintenance Logging:** Record cleanings with energy-before baseline and automatic post-clean improvement calculation.

**Minor Features**
- **Device Claiming & API Key Management:** Claim devices, regenerate keys, and mask keys in UI.
- **Fleet Overview:** Cards/table showing status, today’s energy, and quick actions.
- **API Test Page:** Ready-made payload example for quick endpoint checks.

**Hidden / Backend Features**
- **Background Jobs:** Every 15 minutes compute cleaning improvements, refresh analytics (slot stats, daily summaries), and run AI jobs.
- **SQLite Schema Patching:** Auto-adds missing columns for compatibility.
- **Data Source Guard:** Blocks simulator data when in device mode and vice versa.

**Admin / User Features**
- **User Auth:** Email/password auth, session-based, with protected routes.
- **Per-User Device Access Control:** Only owners can view/control devices and fetch AI/analytics data.

### 5️⃣ System Architecture & Technology Stack
- **Architecture Overview:** Monolithic Flask app with Blueprints (api.py, web.py), SQLAlchemy models, background worker thread, and client-side dashboards using Chart.js.
- **Frontend Technology:** HTML/Jinja templates (Bootstrap 5, Font Awesome), Chart.js (line, doughnut, matrix), custom JS for dashboards, analytics, AI, and control pages.
- **Backend Technology:** Flask, Flask-SQLAlchemy/SQLAlchemy, background thread for analytics/AI, Google Generative AI client for explanations.
- **Database:** Default SQLite (`data.db`), overridable via `DATABASE_URL`.
- **APIs Used:** Internal REST endpoints; optional Google Gemini (`google-genai`) for explanations (needs `GOOGLE_API_KEY`/`GEMINI_API_KEY`).
- **Libraries & Dependencies:** Flask, Flask-SQLAlchemy, SQLAlchemy, numpy/pandas (not heavily used), gunicorn (deploy), google-genai.
- **File Structure (high level):**
  - app.py (factory, logging, background jobs)
  - `routes/` (API + web views)
  - models.py (users, devices, telemetry, events, commands, settings, logs, summaries, AI outputs)
  - analytics.py (slot stats, efficiency, summaries, net-gain logic)
  - ai_engine.py (diagnostics, recommendations, Gemini explainers)
  - seed.py (idempotent seed + digital-twin simulator)
  - `static/js` & `templates/` (UI and charts)
- **Component Interaction & Data Flow:** Devices post telemetry/events → stored in DB → background jobs compute stats → UI/JS fetches chart/analytics endpoints → commands queued and fetched by devices → acknowledgements update status.
- **Request / Response Process:** JSON REST endpoints for devices and UI AJAX; HTML rendered server-side for pages.
- **Authentication:** Session cookies for users; per-device API keys in `X-API-KEY` header for device endpoints.
- **Security Mechanisms:** API key validation, ownership checks, session hardening flags (HttpOnly, SameSite, optional Secure).

### 6️⃣ Technical Deep Dive
- **Languages:** Python (backend), JavaScript (frontend), HTML/CSS.
- **Frameworks & Tools:** Flask, SQLAlchemy, Bootstrap, Chart.js, google-genai.
- **Key Modules & Logic:**
  - api.py: ingestion, event handling, command lifecycle, charts, analytics, alerts, cleaning logs, device selection.
  - analytics.py: slot statistics, movement efficiency, daily summaries, heatmap, net-gain projection logic.
  - ai_engine.py: dust/shading inference, sensor health scoring, power-rail and RTC forensics, recommendations, Gemini explainer, AI summary persistence.
  - seed.py: admin/device bootstrap and DigitalTwinSimulator (minute-by-minute synthetic telemetry/events).
- **Important Models:** `User`, `Device`, `Telemetry`, `Event`, `Command`, `DeviceSettings`, `CleaningLog`, `SlotStatistic`, `DailySummary`, `MovementLog`, `AISummary`, `Alert`, `DeviceFaultLog`.
- **Backend Logic Highlights:**
  - Net gain calculation considers motor energy cost, recent moves/hour, min net gain thresholds, and per-slot best angle.
  - Cleaning improvement computed 2h post-clean to compare before/after energy.
  - Power-rail risk correlates moves with voltage dips/resets; RTC reliability counts rtc_lost events.
- **Frontend Logic Highlights:**
  - Live refresh loops (5s dashboard/control, 60s analytics/AI) with graceful fallbacks.
  - Matrix heatmap for slot-angle power; doughnut for daily energy progress.
  - Control UI issues commands and shows history with status badges.
- **Algorithms:** Simple heuristics for dust/shading via slot power ratios; net-gain projection using best-angle stats and motor cost; movement efficiency via before/after power deltas.
- **Coding Practices:** App factory pattern, Blueprints, idempotent seeds, defensive parsing/validation, structured logging.

### 7️⃣ Installation & Setup Guide (Step-By-Step)
- **System Requirements:** Python 3.10+ recommended; SQLite included; internet needed for Gemini explanations (optional).
- **Prerequisites:** Python, pip, optional Google API key for AI text.
- **Setup Steps:**
  1. `python -m venv .venv && .venv\Scripts\activate` (Windows).
  2. `pip install -r requirements.txt`.
  3. Set env vars (see Section 9). For local demo: `set DATA_SOURCE=SIMULATOR`.
  4. (Optional) Set `GOOGLE_API_KEY` for AI explanations.
  5. Run: `python app.py`.
- **Start Server:** App listens on `http://0.0.0.0:5000` (Flask built-in; use gunicorn for production).
- **Access Website:** Open `http://127.0.0.1:5000`, register/login, claim/select device.
- **Troubleshooting:**
  - Server fails to start: ensure virtualenv active and deps installed.
  - No data on dashboard: in simulator mode, wait ~1–2 minutes for synthetic telemetry; in device mode, post telemetry with correct `X-API-KEY`.
  - Gemini errors: ensure `GOOGLE_API_KEY`/`GEMINI_API_KEY` and network access.
  - SQLite lock issues: avoid multiple writers; consider a real DB for production.

### 8️⃣ Usage Guide (How to Use the Platform)
- **User Flow:**
  1. Register and log in.
  2. Claim a device (or use seeded simulator device) to get an API key.
  3. Select device from navbar to load dashboards.
  4. Monitor live cards and charts; switch to History for day replay.
  5. Use Control Panel to change mode/angle/thresholds or request snapshots.
  6. Check Analytics for best angles, movement efficiency, daily energy, and net-gain decisions.
  7. Review AI Insights for health, risks, forecasts, and recommendations.
  8. Record maintenance actions; watch improvements auto-compute.
- **Device Interaction:**
  - Devices post telemetry/events with `X-API-KEY`.
  - Fetch commands via `/api/cmd/<device_id>` and ack via `/api/cmd_ack`.
- **Example Scenarios:**
  - **Dust detected:** AI shows high dust probability → schedule cleaning, record action, verify improvement.
  - **Power rail risk:** Alerts show power dips near moves → reduce moves/hour or inspect wiring.
  - **Optimization:** Use decision table to move only when net gain exceeds motor cost.

### 9️⃣ Environment Variables / Configuration
- **Core:**
  - `SECRET_KEY` (Flask sessions)
  - `DATABASE_URL` (e.g., sqlite:///... or postgres URL)
  - `DATA_SOURCE` (`SIMULATOR` or `DEVICE`)
  - `SESSION_LIFETIME_SECONDS`, `SESSION_COOKIE_SECURE` (session hardening)
- **Simulator:**
  - `SIM_SPEED` (`FAST` or `REALTIME`)
  - `SIMULATOR_DEVICE_ID` (default `AEY-SIM-001`)
  - `SIMULATOR_API_KEY` (default `SIM-LOCAL-KEY`)
- **Seeding/Admin:**
  - `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD`, `SEED_ADMIN_NAME`
- **AI:**
  - `GOOGLE_API_KEY` or `GEMINI_API_KEY` (enables Gemini explanations)
- **Auth & Cookies:** SameSite Lax, HttpOnly by default; set Secure in production.

### 🔟 Project Screens / Pages Description
- **Dashboard:** Live KPIs, power/angle charts, energy progress, status badge.
- **History:** Date-picker with power, angle, and cumulative energy charts.
- **Control Panel:** Mode/angle/threshold controls, snapshot requests, command history, maintenance recorder.
- **Analytics:** Heatmap (slot vs angle), best-angle table, movement efficiency cards, 7-day energy chart, net-gain decision table.
- **AI Insights:** Dust/shading, sensor health, forecast, efficiency score, power-rail/RTC risk, alerts, recommendations, fault timeline.
- **Devices / Overview:** Fleet cards and table with status, energy, and quick links.
- **Claim Device:** Claim/register device and obtain/regenerate API key.
- **API Test:** Sample telemetry payload for quick API checks.
- **Device Detail:** Latest telemetry snapshot for a single device.
- **Maintenance:** Cleaning logs and improvements (embedded in maintenance/control flows).

### 1️⃣1️⃣ Security & Privacy Notes
- API authentication via per-device `X-API-KEY`; user auth via sessions.
- Ownership enforced on all user-facing APIs; devices restricted to their own keys.
- Logging to `logs/server.log` and `logs/security.log` (handle securely in production).
- No TLS termination provided—use a reverse proxy with HTTPS.
- Session cookies are HttpOnly + SameSite; enable `SESSION_COOKIE_SECURE` in production.
- Inputs validated; still deploy behind WAF/reverse proxy and apply rate limiting externally.

### 1️⃣2️⃣ Performance & Optimization Notes
- Background analytics runs every 15 minutes; inexpensive for small fleets.
- SQLite suits local/small deployments; use Postgres/MySQL for scale/concurrency.
- Chart.js rendering is lightweight; polling intervals tuned (5s live, 60s analytics/AI).
- Net-gain logic avoids excessive moves; helps battery/rail stability.
- Gemini calls are optional and may add latency; results cached as summaries.

### 1️⃣3️⃣ Limitations & Known Issues
- No built-in HTTPS, rate limiting, or CSRF protection for form posts.
- SQLite migrations are minimal (pragma-based); complex schema changes need migrations.
- AI explanations depend on external API and key; fall back to structured metrics if absent.
- No automated tests included.
- Fleet multi-tenancy is basic (per-user devices); no org/role model.

### 1️⃣4️⃣ Future Enhancements
- Full migration tooling (Alembic) and production DB configs.
- Role-based access and organization/workspace model.
- WebSocket/SSE for true push updates instead of polling.
- More device commands (calibration, firmware actions) and richer event taxonomy.
- Advanced analytics (weather integration, yield forecasts) and anomaly detection.
- Hardening: CSRF protection, rate limiting, API tokens per app, audit trails.

### 1️⃣5️⃣ Real-World Value & Business Perspective
- **Usefulness:** Reduces downtime, improves yield, and speeds root-cause analysis for solar trackers.
- **Who benefits:** Solar asset operators, OEMs validating firmware/hardware, integrators running pilots, and educators demonstrating PV tracking.
- **Business vision:** Offer as a managed monitoring and optimization service for small distributed solar assets with optional digital-twin sandboxing.

### 1️⃣6️⃣ Contribution Guidelines
- Fork and branch from `main`.
- Follow PEP8 and keep functions small and typed where possible.
- Add/update docstrings when touching analytics or AI logic.
- Include API/behavior notes in PR descriptions; keep PRs focused.
- Avoid breaking telemetry/command contracts; validate payloads.

### 1️⃣7️⃣ License Section
- No license file is present; usage defaults to “all rights reserved” unless a license is added. Add a LICENSE before redistribution or production use.

### 1️⃣8️⃣ Final Professional Conclusion
EnergyYield delivers an end-to-end loop for solar tracker operations: ingest data, analyze it, act on it, and explain results with AI. With live dashboards, control tooling, analytics, AI diagnostics, and a built-in simulator, it accelerates experimentation and improves real-world reliability. Configure env vars, start the app, claim a device, and you have a practical, data-driven control center for your trackers.