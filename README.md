# 🎙️ Sonic2Life — Voice-First Life Assistant for Elderly & Visually Impaired

> **"Your voice companion for everyday life"** — A proactive, caring AI assistant that helps seniors and visually impaired people navigate daily life through natural voice conversation.

[![Amazon Nova](https://img.shields.io/badge/Amazon%20Nova-2%20Sonic-orange?style=for-the-badge&logo=amazon-aws)](https://aws.amazon.com/ai/generative-ai/nova/)
[![Category](https://img.shields.io/badge/Category-Voice%20AI-blue?style=for-the-badge)](https://amazon-nova.devpost.com/)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

**#AmazonNova** | [Amazon Nova AI Hackathon](https://amazon-nova.devpost.com/)

---

## 🎯 The Problem

Millions of elderly people and visually impaired individuals face daily challenges that most of us take for granted:

- 💊 **Forgetting medications** — missed doses lead to health complications
- 🗺️ **Getting disoriented** — "Where am I? How do I get to the pharmacy?"
- 📅 **Missing appointments** — doctor visits, family events slip through the cracks
- 🌧️ **Weather unawareness** — going out underdressed in cold or rain
- 😔 **Social isolation** — no one to talk to, no patient helper available 24/7

Traditional apps with small text, complex menus, and visual interfaces are **useless** for these users. They need something fundamentally different.

## 💡 The Solution

**Sonic2Life** is a **100% voice-first** PWA assistant powered by **Amazon Nova 2 Sonic** speech-to-speech AI. It doesn't just answer questions — it **proactively cares**:

- 🗣️ **Natural voice conversation** — no screens, no typing, no menus (auto-detects language)
- 💊 **Medication management** — tracks schedules, sends reminders, confirms intake
- 📍 **Real-time location awareness** — "Where am I?", nearby pharmacies, walking directions
- 📅 **Calendar & events** — appointments, birthdays, daily schedule briefings
- 🌤️ **Weather-aware advice** — "It's cold today, wear a warm jacket"
- 🔔 **Proactive push notifications** — medication reminders, morning briefings, event alerts
- 🫶 **Warm, patient persona** — never rushes, always kind, remembers preferences

### One Big Button. That's It.

The entire UI is a single large button. Press it, talk. The AI handles everything else.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Browser (PWA)                         │
│ ┌───────────┐ ┌──────────┐ ┌─────────┐ ┌──────────────┐  │
│ │ Mic/Audio │ │   GPS    │ │  Push   │ │    Admin     │  │
│ │ Capture   │ │ Tracking │ │ Notifs  │ │    Panel     │  │
│ └─────┬─────┘ └────┬─────┘ └───┬─────┘ └──────┬───────┘  │
│       └──────┬─────┘           │              │          │
│              │ WebSocket       │ SSE + Push   │ REST     │
└──────────────┼─────────────────┼──────────────┼──────────┘
               │                 │              │           
┌──────────────┼─────────────────┼──────────────┼──────────┐
│              v                 v              v          │
│       FastAPI Server (Python 3.12)                       │
│ ┌────────────────────────────────────────────────────┐   │
│ │          WebSocket Handler                         │   │
│ │ (Continuous PCM16 Audio + GPS + Control Msgs)      │   │
│ └──────────────────┬─────────────────────────────────┘   │
│                    v                                     │
│ ┌────────────────────────────────────────────────────┐   │
│ │   Amazon Nova 2 Sonic (AWS Bedrock)                │   │
│ │ Bidirectional Speech-to-Speech Streaming           │   │
│ │      Built-in VAD + Barge-in Support               │   │
│ └──────────────────┬─────────────────────────────────┘   │
│                    │ Tool Calls (askAgent)               │
│                    v                                     │
│ ┌────────────────────────────────────────────────────┐   │
│ │     Strands Agent + Amazon Nova 2 Lite             │   │
│ │          (Tool Orchestration)                      │   │
│ │                                                    │   │
│ │ Tools:                     MCP Servers:            │   │
│ │ +-- Medication (5 tools)   +-- AWS Knowledge       │   │
│ │ +-- Events (5 tools)       +-- Amazon Location     │   │
│ │ +-- Memory (3 tools)                               │   │
│ │ +-- Weather + Forecast                             │   │
│ │ +-- Vision (auto photo)                            │   │
│ │ +-- Web Search                                     │   │
│ │ +-- Emergency Contacts (4)                         │   │
│ │ +-- SMS via SNS (1)                                │   │
│ │ +-- Utilities                                      │   │
│ └──────────────────┬─────────────────────────────────┘   │
│                    v                                     │
│ ┌──────────┐ ┌──────────┐ ┌──────────────────┐           │
│ │  SQLite  │ │ Scheduler│ │  Push Service    │           │
│ │    DB    │ │ (asyncio)│ │ (VAPID/WebPush)  │           │
│ └──────────┘ └──────────┘ └──────────────────┘           │
└──────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Why |
|----------|-----|
| **Single Tool Interface** | Nova Sonic sees only ONE tool (`askAgent`). Behind it, Strands Agent orchestrates 18+ tools. This simplifies the voice model's decision-making. |
| **Continuous Audio Streaming** | No push-to-talk. Audio streams non-stop from mic to server; Nova Sonic handles VAD, turn detection, and barge-in server-side. |
| **GPS Context Injection** | GPS coordinates are automatically injected into every agent call — the AI always knows where the user is without asking. |
| **Dual Notification Delivery** | SSE for in-app (instant, reliable) + Web Push for background (system notifications when app is closed). |
| **Actionable Notifications** | Push notifications include action buttons ("✅ Taken" / "⏰ Snooze") with **real backend logic** — taken logs to DB, snooze reschedules reminder. |
| **User Profile Personalization** | Admin-set user name is injected into system prompts. The AI greets by name and infers language from the name. |

---

## 🛠️ AWS Services & Amazon Nova Models

| Service | Role |
|---------|------|
| **Amazon Nova 2 Sonic** (Bedrock) | Core speech-to-speech model — real-time voice conversation with tool calling |
| **Amazon Nova 2 Lite** (Bedrock) | Agent reasoning model — powers the Strands Agent for complex tool orchestration |
| **Amazon Location Service** (via MCP) | Reverse geocoding, place search, nearby POIs, route calculation, waypoint optimization |
| **AWS Knowledge Base** (via MCP) | General knowledge retrieval for answering user questions |

---

## ✨ Features

### 🗣️ Voice Conversation
- Real-time speech-to-speech via Amazon Nova 2 Sonic
- **Dynamic language detection** — automatically matches the user's language (English, Czech, German, etc.)
- Warm, patient persona that adapts to the user's name and profile
- Continuous listening with server-side VAD (no button holding needed)
- Barge-in support (interrupt the AI mid-sentence)

### 💊 Medication Management
- Track medication schedules (name, dosage, times, days of week)
- Confirm medication intake via voice or notification button
- View medication history and compliance in admin panel
- **Proactive push reminders** when it's time to take meds
- **Functional action buttons**: "✅ Taken" logs to medication history, "⏰ Snooze 15min" reschedules reminder
- Snooze & response data persisted to SQLite (survives restarts)

### 📅 Calendar & Events
- Add, view, cancel, and reschedule events via voice
- Today's schedule overview (events + medications combined into timeline)
- **Morning briefing** push notification (6:00–9:00) with day's agenda
- Pre-event reminders (configurable minutes before)

### 📍 Location & Navigation (Amazon Location Service)
- "Where am I?" — instant reverse geocoding from GPS
- Find nearby places (pharmacies, shops, restaurants)
- Walking directions with step-by-step guidance
- Multi-stop route optimization ("I need the pharmacy AND the post office")
- Search for places by name or category

### 📞 Emergency Contacts & SMS
- **Voice-managed contacts** — add, list, update, remove contacts by voice
- **Contact details** — name, full name, relationship, phone number
- **Send SMS via voice** — "Send SMS to Jana that I'm okay" → Amazon SNS delivery
- **SMS logging** — all sent messages logged with status (sent/failed/error)
- **Admin panel** — Contacts tab with contact management + Sent SMS Messages table

### 🌤️ Weather & Forecast (Open-Meteo)
- **No API key needed** — uses Open-Meteo free API
- Current conditions (temperature, humidity, wind, precipitation)
- **Hourly forecast** — next 24 hours, every 3 hours
- **Daily forecast** — 3 days ahead with min/max temps, rain chance, sunrise/sunset
- WMO weather codes mapped to human-readable descriptions
- Senior-friendly recommendations (cold/hot/rain/wind/snow)
- Location-aware (uses GPS automatically)
- Ask: *"How will the weather be tomorrow morning?"*

### 👤 User Profile & Personalization
- Admin-configurable user profile (name, full name, phone)
- **Dynamic greeting** — assistant addresses user by name
- **Language inference** from user profile (e.g., "Miroslav" → Czech, "Jack" → English)
- Profile injected into both Nova Sonic and Strands Agent system prompts

### 🧠 Memory & Preferences
- Remembers user preferences, names, habits
- Persistent across sessions (SQLite-backed)
- "Remember that I like..." / "What's my...?"

### 🔔 Proactive Notifications
- Background asyncio scheduler checks medications and events periodically
- Push notifications even when app is closed (Web Push + VAPID)
- In-app banner notifications when app is open (SSE)
- Morning daily briefing with schedule overview
- **Actionable buttons with real backend logic:**
  - "✅ Taken" → logs medication to `medication_log` (visible in admin history)
  - "⏰ Snooze 15min" → stores snooze in SQLite, scheduler re-sends after expiry
- All notification responses persisted to SQLite

### 📸 Camera & Photo Analysis
- **Camera button** on main screen — tap to take a photo
- **"Photo First" flow** — take a photo even before starting a conversation
  - Photo stored as pending, camera shows **📷 Ready**
  - Tap "Talk" → session starts → greeting plays → photo auto-sent after 3s
- **Automatic analysis** — photo is analyzed by Nova 2 Lite vision immediately
- No need to ask — assistant **automatically describes** what it sees
- Identifies medications, reads text, describes objects
- Follow-up questions via voice: *"What's the dosage?"*, *"Is this safe?"*
- Works on mobile: camera app deactivates mic, but photo is queued and sent when session resumes

### 🔍 Web Search
- **DuckDuckGo integration** — no API key needed
- Search the internet for current information via voice
- Ask: *"Search for side effects of Metformin"*

### 📱 PWA (Progressive Web App)
- Installable on phone home screen
- Works offline (service worker cache)
- Full-screen standalone mode
- Mobile-first, accessibility-focused design

### ⚙️ Admin Panel
- Web-based dashboard at `/admin`
- CRUD management for medications, events, memory entries, settings
- Scheduler configuration (enable/disable, interval, timezone)
- Database backup & file management
- Dashboard with statistics overview
- **Push Subscriptions management** — view all subscriptions with endpoint, user agent, created date, last success, fail count
  - Per-subscription delete, bulk "Delete All", "Delete Stale" (failed deliveries)
  - Dashboard stats card shows subscription count + stale count

---

## 🎬 Demo Scenarios

### 🌅 Morning Routine
```
👤 "Good morning!"
🤖 "Good morning, Jack! Today is Sunday, February 23rd.
    It's 5 degrees and cloudy outside — wear a warm jacket.
    You need to take Metformin and Enalapril.
    At 10 AM you have Dr. Smith at the clinic on Main Street."
```

### 💊 Medication Reminder (Proactive Push)
```
📱 [Push notification: "💊 Time to take: Metformin 500mg"]
   [Buttons: ✅ Taken | ⏰ Snooze 15min]
👤 *taps "Taken"*
🤖 ✅ Logged to medication history. Next medication at 8:00 PM.

--- or ---

👤 *taps "Snooze 15min"*
🤖 ⏰ Snoozed. Reminder will repeat in 15 minutes.
📱 [15 min later: "💊 Reminder (after snooze): Metformin 500mg"]
```

### 🗺️ Finding a Pharmacy
```
👤 "Where is the nearest pharmacy?"
🤖 "The nearest pharmacy is Dr. Max on Vinohradská Street,
    400 meters from you. Would you like directions?"
👤 "Yes"
🤖 "Walk straight ahead. In 100 meters, turn left onto
    Vinohradská. The pharmacy will be on your right
    after 300 meters."
```

### 📍 "Where Am I?"
```
👤 "Where am I?"
🤖 "You're at Wenceslas Square number 23,
    Prague 1. Would you like to know what's nearby?"
```

### 🛒 Multi-Stop Route
```
👤 "I need to go to the pharmacy and the post office"
🤖 "The post office is closer, so let's go there first,
    then the pharmacy. About 15 minutes walking total.
    Shall I navigate?"
```

---

## 🚀 Quick Start

### Prerequisites
- **AWS Account** with Bedrock access (Nova 2 Sonic + Nova 2 Lite enabled in `eu-north-1`)
- **Python 3.12+**
- **Docker** (optional, for containerized deployment)

### Option 1: Local Development

```bash
# Clone the repository
git clone https://github.com/mirecekd/sonic2life.git
cd sonic2life

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your AWS credentials

# Run the server
./start.sh
# or: uvicorn app.main:app --host 0.0.0.0 --port 5005 --reload
```

Open `http://localhost:5005` in your browser.

### Option 2: Docker

```bash
# Clone and configure
git clone https://github.com/mirecekd/sonic2life.git
cd sonic2life
cp .env.example .env
# Edit .env with your credentials

# Build and run
docker-compose up --build
```

Open `http://localhost:5005` in your browser.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AWS_ACCESS_KEY_ID` | Yes | AWS credentials for Bedrock access |
| `AWS_SECRET_ACCESS_KEY` | Yes | AWS credentials for Bedrock access |
| `AWS_REGION` | Yes | AWS region (default: `eu-north-1`) |
| `NOVA_SONIC_MODEL_ID` | No | Default: `amazon.nova-2-sonic-v1:0` |
| `NOVA_SONIC_VOICE_ID` | No | Default: `tiffany` |
| `VAPID_PRIVATE_KEY` | No | Auto-generated if empty (raw urlsafe base64) |
| `VAPID_PUBLIC_KEY` | No | Auto-generated if empty (raw urlsafe base64) |
| `HOST` | No | Default: `0.0.0.0` |
| `PORT` | No | Default: `5005` |

> **Note:** Push notifications require HTTPS (except on localhost). Use [ngrok](https://ngrok.com/) for quick HTTPS tunneling during development.

---

## 📁 Project Structure

```
sonic2life/
├── app/
│   ├── main.py                 # FastAPI app, routes, startup/shutdown
│   ├── config.py               # Environment config, system prompt
│   ├── websocket_handler.py    # WebSocket bridge (audio + GPS + control)
│   ├── nova_sonic.py           # Nova 2 Sonic bidirectional streaming
│   ├── agent.py                # Strands Agent, tool registration, MCP clients
│   ├── push.py                 # VAPID keys, push subscriptions, send notifications
│   ├── scheduler.py            # Background scheduler (medication & event reminders)
│   ├── admin.py                # Admin panel API routes
│   ├── static/
│   │   ├── index.html          # PWA main page
│   │   ├── admin.html          # Admin panel UI
│   │   ├── app.js              # Mic capture, playback, GPS, push subscription
│   │   ├── style.css           # Dark theme, accessibility-focused
│   │   ├── sw.js               # Service worker (caching + push handler)
│   │   ├── manifest.json       # PWA manifest
│   │   └── icons/              # PWA icons (192px, 512px)
│   └── tools/
│       ├── database.py         # SQLite init, table creation
│       ├── weather.py          # Open-Meteo weather + forecast (free, no API key)
│       ├── medication.py       # 5 medication management tools
│       ├── memory.py           # 3 memory/preference tools
│       ├── events.py           # 5 calendar/event tools
│       ├── web_search.py       # DuckDuckGo web search (no API key)
│       └── vision.py           # Photo analysis via Nova 2 Lite vision
├── memory-bank/                # Project documentation (Cline memory bank)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── start.sh
├── .env.example
└── README.md
```

---

## 🧪 Tech Stack

| Layer | Technology |
|-------|------------|
| **Voice AI** | Amazon Nova 2 Sonic (Bedrock, bidirectional streaming) |
| **Agent Reasoning** | Amazon Nova 2 Lite + Strands Agents framework |
| **Vision** | Amazon Nova 2 Lite (Bedrock Converse API) for photo analysis |
| **Location** | Amazon Location Service (via MCP server) |
| **Knowledge** | AWS Knowledge Base (via MCP server) |
| **Backend** | Python 3.12, FastAPI, uvicorn |
| **Database** | SQLite |
| **Frontend** | Vanilla JS, CSS3 (dark theme), Web Audio API |
| **Audio** | AudioWorklet ring buffer (24kHz PCM16), continuous streaming |
| **PWA** | Service Worker, Web App Manifest |
| **Push** | Web Push API, VAPID (py-vapid + pywebpush) |
| **Deployment** | Docker, docker-compose |

---

## 🎯 Hackathon Alignment

### Category: Voice AI
> *"Create real-time conversational voice experiences using Nova 2 Sonic."*

Sonic2Life is a **real-time, bidirectional voice assistant** that demonstrates the full power of Nova 2 Sonic:
- Speech-to-speech with no intermediate text pipeline
- Real-time tool calling during voice conversation
- Server-side VAD with barge-in support
- Natural, empathetic persona with dynamic language detection

### Judging Criteria Coverage

| Criteria (Weight) | How Sonic2Life Addresses It |
|-------------------|-----------------------------|
| **Technical Implementation (60%)** | Multi-model architecture (Nova 2 Sonic + Nova 2 Lite), Strands Agent with 18+ tools, 2 MCP servers, continuous audio streaming with AudioWorklet, proactive scheduler, dual notification system (SSE + Web Push), PWA with offline support |
| **Enterprise/Community Impact (20%)** | Directly serves elderly and visually impaired communities — populations often excluded from technology. Addresses real health risks (missed medications), safety concerns (disorientation), and social isolation. |
| **Creativity & Innovation (20%)** | Single-tool interface pattern (askAgent), GPS context injection into voice AI, proactive care via push notifications, voice-first design that makes AI accessible to non-tech users |

---

## 🤝 Community Impact

Sonic2Life addresses a critical gap in assistive technology:

- **1.3 billion people** worldwide live with some form of visual impairment (WHO)
- **~20% of the population** in developed countries is over 65
- Most digital assistants require **visual interaction** — screens, text, menus
- Sonic2Life is **100% voice-operated** — truly accessible to those who need it most

### Real-World Applications
- **Independent living support** for seniors aging in place
- **Medication adherence** — a $300B/year problem in healthcare
- **Wayfinding assistance** for visually impaired pedestrians
- **Daily routine management** reducing caregiver burden
- **Emergency calling** — voice-initiated phone calls to emergency contacts

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 👨‍💻 Author

Built with ❤️ for the [Amazon Nova AI Hackathon](https://amazon-nova.devpost.com/)

**#AmazonNova**