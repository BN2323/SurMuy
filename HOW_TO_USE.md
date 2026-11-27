# SurMuy

## Screenshot chat

Double-click the **SurMuy** Windows shortcut to open the app. Use
the **Capture** menu for screenshots and **New chat** beside the message field
to start fresh. Ctrl+N also starts a new chat when the panel has focus.
Esc or the close button hides the window. Click the SurMuy icon in the
notification area near Wi-Fi to reopen it, or look under the **^** overflow
arrow. Right-click the icon for **Exit**. Ctrl+Shift+Space also toggles the window.

The app runs OCR and screenshot chat together. The existing Windows Startup
shortcut goes through `start_daemon.vbs`, which now opens the same app. During
migration, the app uses the existing OCR process if it is already running.

| Shortcut | Action |
|---|---|
| **Ctrl + Shift + 1** | Drag a region on any monitor and release to send it. Esc cancels. |
| **Ctrl + Shift + 2** | Send the entire desktop, including all monitors. |
| **Ctrl + Shift + 3** | Capture and send the last selected region again. |
| **Ctrl + Shift + Space** | Open or hide the chat panel. Esc also hides the panel. |
| **Shift + F7** | Quit SurMuy. |

Press **Ctrl+Shift+Space**, type a message, and press **Enter** to start a text chat.
Use **New chat** to clear the conversation and detach the previous screenshot.

Use **Sessions → Save session** (Ctrl+S) to keep a conversation as a local chat file.
Give the session a name and the app saves it in `chats/`. The file includes
completed exchanges, any draft, and any attached screenshot.
Use **Sessions → Saved sessions** (Ctrl+O) to pick a chat from the in-app list.
After saving or opening a session, completed replies auto-save to that file.
Use **New chat** to start an unsaved session. Opening a file keeps its contents
local until you send a message.
New sessions are saved manually by default. Turn on **Sessions → Auto-save new
sessions** to save each new chat after its first completed reply, with a title
derived locally from your first message.
API-key requests use the selected model's **Context budget (tokens)** in AI
settings, with 2,048 tokens reserved for its reply. The app sends recent
turns and a reusable summary of any earlier turns it compacted. The full
transcript stays in saved sessions. Keep the adjacent `.attachments/` directory
when moving a saved session so it can still find its screenshots and documents.
Token counts, estimates, and cache usage appear in `logs/api-usage.jsonl`.

Capture sends a PNG immediately to the selected provider. To ask a specific first
question, type it into the chat input before capturing. Otherwise the app asks the
agent to answer the question shown or describe the image. Press Enter to ask a
follow-up about the current screenshot. Each new capture starts a fresh chat.

**Show answers in corner** is on by default in the **Capture** menu. After a
capture, the chat stays hidden and the reply appears at the lower-right corner
of the capture monitor. Click the text to dismiss it early, or open the chat to
read the full reply. Turn the option off to keep using the chat window for replies.

Open **Sessions → AI instructions** to apply the same instruction to typed messages
and screenshots while the app is open. **Capture → Capture response** offers
**Answer or describe**, **Answer questions**, and **Describe screenshot**.
A message typed before capture takes priority over the default response mode.
Choose **Attach only** to add the screenshot to the current chat and wait for
your question. Use **📎** beside the message field to attach one image, text
file, PDF, or Word document, or paste an image or copied file with Ctrl+V.
Document attachments send extracted text. PDFs are read up to 25 pages and
long document text is limited to 24,000 characters. Click **×** beside the
attachment to remove it.

API-key requests include a new attachment on its first send. Later replies
can retrieve it when you refer to it, for example with “that screenshot” or
“image 1.” Unrelated requests skip the upload. Codex and Antigravity send
the active attachment once per live conversation. Replacing or removing it
starts a fresh native conversation using the existing chat history.
Check **Answer letters only** in the Capture menu for choices such as
`B` or `A,C` without an explanation. These settings apply to captures, including
repeat captures. A message typed before capture is sent alongside them once.
Changing providers for a follow-up sends the current screenshot and prior
successful turns to the newly selected provider. Wait for the current request
to finish before capturing again.

The compact panel is an ordinary window when open and can be visible during
desktop sharing. The app hides the panel during its own captures.

- **OpenAI** and **Gemini** each offer **Account sign-in** and **API key** under
  the gear. Account sign-in uses the local Codex CLI for OpenAI and Antigravity
  CLI for Gemini. The app checks for an existing Gemini connection first. If
  needed, click **Sign in with Google** and leave settings open until the
  browser sign-in finishes and models appear. OpenAI uses **Sign in with ChatGPT**.
- Account chat keeps Codex or Antigravity running and reuses its conversation for
  follow-ups. A fresh capture or **New chat** starts a separate conversation in
  the same process. The first request can still take longer while it starts.
  Older Codex installations may fall back to a one-shot command.
- **Claude**, **Kimi**, and **DeepSeek** use their API keys. Choose the provider,
  enter its key, click **Load models**, and save your selection.
- **Custom API** accepts an OpenAI-compatible base URL and an optional key.
  Use HTTPS for remote servers or HTTP for local loopback servers. Load models
  or enter the model ID if the server does not publish a catalog.
- After connecting, use the **model picker beside the provider** to switch
  models. Screenshots require a model with image input support. API keys and
  connection preferences are kept in Windows Credential Manager.
- Existing `GEMINI_API_KEY` and `SCREEN_CHAT_GEMINI_MODEL` environment settings
  still work as a fallback if you have no Gemini key saved in the app.

Provider adapters are registered in `src/vision_agents.py`. An adapter receives an
optional Pillow image, previous question/answer pairs, and the current question.

A tool that reads text off my screen so an AI agent can answer QCM / quiz questions.

## For me (the user) — watch mode

1. Press **Ctrl + Alt + Q** once, drag a box over the question area, release.
   It now **locks that box and keeps watching it**.
2. When the question changes, it auto-captures the new text and pops a notice.
   Just say **"go"** (or "next") to the agent for each answer — no re-selecting.
3. Press **Ctrl + Alt + S** to stop watching, **Ctrl + Alt + Q** to pick a new box.

Hotkeys:
- **Ctrl + Alt + Q** — select a box + AUTO-watch (reads whenever text changes)
- **Ctrl + Alt + W** — select a box in MANUAL mode (idle; reads only on F8)
- **Ctrl + Alt + V** — flash the box outline on screen to confirm where it is
- **F8** — re-read the locked box right now (force)
- **Ctrl + Alt + S** — stop watching
- **Ctrl + Alt + X** — quit the daemon

Two modes:
- **Auto (Ctrl+Alt+Q):** polls the box and captures on every change. Hands-free
  but uses a little CPU continuously.
- **Manual (Ctrl+Alt+W):** sets the box and then does nothing until you press
  F8. Zero processing between questions — lightest option.

Captured text is saved to `screen_text.txt`; a running history goes to `qcm_log.txt`.

- The daemon **auto-starts on login** (shortcut in the Windows Startup folder),
  so the hotkeys are always ready. No agent needed to start it.

## For the AI agent (paste this if it's a new session)

> This folder contains an OCR screen reader. The user presses **Ctrl+Alt+Q**,
> drags a box over a question, and the recognized text is written to
> `screen_text.txt`. When the user says **"go"**, read `screen_text.txt` and
> answer the question(s) in it. Do NOT re-run capture yourself — the user
> triggers it with the hotkey; you just read the file.

## Files

| File | Purpose |
|------|---------|
| `src/read_screen.py` | Capture and Windows OCR |
| `src/reader_daemon.py` | OCR shortcut listener |
| `start_daemon.vbs` | Opens the app through the existing Windows Startup entry |
| `logs/daemon.log` | OCR diagnostics created when the app starts its OCR worker |
| `.local/archive/` | Previous launchers and test files, excluded from Git |
| `screen_text.txt` | Where the recognized text lands — the agent reads this |

## If the hotkey stops working

Exit from the notification-area icon and reopen the **SurMuy** shortcut.

## Accuracy / languages

- Default OCR language is **French** (falls back to English if French isn't installed).
- Installed engines are English only right now. To add French (one time, as admin):
  ```powershell
  Add-WindowsCapability -Online -Name "Language.OCR~~~fr-FR~0.0.1.0"
  ```
  Then restart the daemon (Ctrl+Alt+X, then double-click `start_daemon.vbs`).
- Crop tight with the box for the cleanest results.
