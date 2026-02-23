/**
 * Sonic2Life – Voice-first life assistant for seniors
 *
 * Uses kecweb backend protocol:
 *   Client → Server:
 *     - JSON {"type": "start", "engine": "nova", "voice_id": "..."} – begin
 *     - JSON {"type": "end"} – end conversation
 *     - Binary (ArrayBuffer) – PCM 16-bit 16kHz mono audio
 *
 *   Server → Client:
 *     - JSON {"type": "transcript_user/ai", "text": "..."}
 *     - JSON {"type": "speaking"} / {"type": "barge_in"} / {"type": "done"}
 *     - JSON {"type": "tool_use", "tool": "..."}
 *     - JSON {"type": "error", "text": "..."}
 *     - Binary – PCM 16-bit 16kHz audio for playback
 */

"use strict";

// ── Constants ────────────────────────────────────────────────────────
const TARGET_SAMPLE_RATE = 16000;
const PLAYBACK_SAMPLE_RATE = 16000;
const NOVA_VOICE = "matthew";

// ── State ────────────────────────────────────────────────────────────
const State = {
    IDLE: "idle",
    CONNECTING: "connecting",
    LISTENING: "listening",
    SPEAKING: "speaking",
    TOOL_USE: "tool_use",
};

let currentState = State.IDLE;
let ws = null;
let audioCtx = null;
let micStream = null;
let micSource = null;
let scriptProcessor = null;
let playbackQueue = [];
let isPlaying = false;
let nextPlayTime = 0;

// ── DOM ──────────────────────────────────────────────────────────────
const talkBtn = document.getElementById("talk-btn");
const statusText = document.getElementById("status-text");
const connBadge = document.getElementById("connection-status");
const transcript = document.getElementById("transcript");
const micIcon = document.getElementById("mic-icon");
const waveIcon = document.getElementById("wave-icon");
const stopIcon = document.getElementById("stop-icon");
const spinnerIcon = document.getElementById("spinner-icon");

// ── UI Helpers ───────────────────────────────────────────────────────
function setState(state, text) {
    currentState = state;
    talkBtn.classList.remove("listening", "speaking", "connecting", "tool-use");

    micIcon.classList.add("hidden");
    waveIcon.classList.add("hidden");
    stopIcon.classList.add("hidden");
    spinnerIcon.classList.add("hidden");

    switch (state) {
        case State.IDLE:
            micIcon.classList.remove("hidden");
            statusText.textContent = text || "Tap to talk";
            break;
        case State.CONNECTING:
            talkBtn.classList.add("connecting");
            spinnerIcon.classList.remove("hidden");
            statusText.textContent = text || "Connecting...";
            break;
        case State.LISTENING:
            talkBtn.classList.add("listening");
            stopIcon.classList.remove("hidden");
            statusText.textContent = text || "Listening... tap to stop";
            break;
        case State.SPEAKING:
            talkBtn.classList.add("speaking");
            waveIcon.classList.remove("hidden");
            statusText.textContent = text || "Responding...";
            break;
        case State.TOOL_USE:
            talkBtn.classList.add("tool-use");
            spinnerIcon.classList.remove("hidden");
            statusText.textContent = text || "🔧 Working...";
            break;
    }
}

function setConnectionBadge(status) {
    connBadge.className = "status-badge " + status;
    const labels = {
        connected: "Connected",
        connecting: "Connecting",
        disconnected: "Disconnected",
    };
    connBadge.textContent = labels[status] || "Disconnected";
}

function addTranscript(role, text) {
    const entries = transcript.querySelectorAll(".entry");
    const last = entries[entries.length - 1];

    // Append to existing entry of same role
    if (last && last.dataset.role === role) {
        const p = last.querySelector("p");
        p.textContent += text;
    } else {
        const div = document.createElement("div");
        div.className = "entry";
        div.dataset.role = role;
        const labels = { user: "You", assistant: "Assistant", tool: "🔧 Tool" };
        const label = labels[role] || role;
        div.innerHTML = '<div class="role ' + role + '">' + label + "</div><p></p>";
        div.querySelector("p").textContent = text;
        transcript.appendChild(div);
    }
    transcript.parentElement.scrollTop = transcript.parentElement.scrollHeight;
}

// ── WebSocket ────────────────────────────────────────────────────────
function connectWS() {
    return new Promise(function (resolve, reject) {
        var proto = location.protocol === "https:" ? "wss:" : "ws:";
        var url = proto + "//" + location.host + "/ws/audio";
        console.log("[WS] Connecting to", url);

        ws = new WebSocket(url);
        ws.binaryType = "arraybuffer";

        var timeout = setTimeout(function () {
            reject(new Error("Connection timeout"));
            ws.close();
        }, 20000);

        ws.onopen = function () {
            console.log("[WS] Open");
            clearTimeout(timeout);
            setConnectionBadge("connected");
            resolve();
        };

        ws.onmessage = function (evt) {
            if (typeof evt.data === "string") {
                handleControl(JSON.parse(evt.data));
            } else {
                handleAudioData(evt.data);
            }
        };

        ws.onerror = function (err) {
            console.error("[WS] Error", err);
            clearTimeout(timeout);
            reject(err);
        };

        ws.onclose = function () {
            console.log("[WS] Closed");
            clearTimeout(timeout);
            setConnectionBadge("disconnected");
            ws = null;
            closeMic();
            if (currentState !== State.IDLE) {
                setState(State.IDLE, "Disconnected. Tap again.");
            }
        };
    });
}

function handleControl(msg) {
    console.log("[WS]", msg.type, msg.text || msg.tool || "");

    switch (msg.type) {
        case "transcript_user":
            addTranscript("user", msg.text);
            break;

        case "transcript_ai":
            addTranscript("assistant", msg.text);
            break;

        case "thinking":
            // Model is processing, keep streaming
            break;

        case "speaking":
            setState(State.SPEAKING, "Responding...");
            break;

        case "barge_in":
            // User interrupted — clear playback
            stopPlayback();
            setState(State.LISTENING, "Listening...");
            break;

        case "photo_received":
            console.log("[Camera] Photo received by server:", msg.size_kb, "KB");
            cameraLabel.textContent = "✓ Sent";
            break;

        case "photo_analyzing":
            console.log("[Camera] Analyzing photo...");
            cameraLabel.textContent = "🔍 Analyzing...";
            break;

        case "photo_analyzed":
            console.log("[Camera] Photo analyzed:", msg.description);
            cameraLabel.textContent = "📷 Photo";
            addTranscript("system", "📸 " + msg.description);
            break;

        case "photo_error":
            console.error("[Camera] Photo analysis error:", msg.text);
            cameraLabel.textContent = "❌ Error";
            setTimeout(function () { cameraLabel.textContent = "Photo"; }, 3000);
            break;

        case "done":
            stopPlayback();
            closeMic();
            setState(State.IDLE, "Tap to talk");
            break;

        case "error":
            console.error("[Server]", msg.text);
            statusText.textContent = "Error: " + msg.text;
            break;

        case "tool_use":
            setState(State.TOOL_USE, "🔧 " + (msg.tool || "Working..."));
            addTranscript("tool", msg.tool || "Using tool...");
            break;
    }
}

// ── Conversation ─────────────────────────────────────────────────────
async function startConversation() {
    try {
        setState(State.CONNECTING);
        setConnectionBadge("connecting");

        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            console.log("[Audio] Sample rate:", audioCtx.sampleRate);
        }
        if (audioCtx.state === "suspended") {
            await audioCtx.resume();
        }

        if (!ws || ws.readyState !== WebSocket.OPEN) {
            await connectWS();
        }

        // Send GPS if available
        sendGPS();

        // Start Nova 2 Sonic session
        ws.send(JSON.stringify({
            type: "start",
            engine: "nova",
            voice_id: NOVA_VOICE,
        }));

        await openMic();
        setState(State.LISTENING, "Listening...");
    } catch (err) {
        console.error("[Start]", err);
        setState(State.IDLE, "Error: " + err.message);
    }
}

function endConversation() {
    closeMic();
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "end" }));
    }
    stopPlayback();
    setState(State.IDLE, "Tap to talk");
}

// ── Microphone ───────────────────────────────────────────────────────
async function openMic() {
    if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === "suspended") {
        await audioCtx.resume();
    }

    micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
    });

    micSource = audioCtx.createMediaStreamSource(micStream);

    var bufferSize = 4096;
    scriptProcessor = audioCtx.createScriptProcessor(bufferSize, 1, 1);
    var inputRate = audioCtx.sampleRate;

    var _audioSendCount = 0;
    console.log("[Mic] scriptProcessor created, inputRate=" + inputRate + ", bufferSize=" + bufferSize);
    scriptProcessor.onaudioprocess = function (e) {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            console.warn("[Mic] WS not open (state=" + (ws ? ws.readyState : "null") + "), dropping audio");
            return;
        }

        var inputData = e.inputBuffer.getChannelData(0);

        // Resample to 16kHz
        var ratio = inputRate / TARGET_SAMPLE_RATE;
        var outputLength = Math.floor(inputData.length / ratio);
        var output = new Float32Array(outputLength);

        for (var i = 0; i < outputLength; i++) {
            var srcIdx = i * ratio;
            var idx = Math.floor(srcIdx);
            var frac = srcIdx - idx;
            if (idx + 1 < inputData.length) {
                output[i] = inputData[idx] * (1 - frac) + inputData[idx + 1] * frac;
            } else {
                output[i] = inputData[idx];
            }
        }

        // Float32 → Int16 PCM
        var pcm16 = new Int16Array(output.length);
        for (var j = 0; j < output.length; j++) {
            var s = Math.max(-1, Math.min(1, output[j]));
            pcm16[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }

        // Send as binary ArrayBuffer (kecweb protocol)
        _audioSendCount++;
        if (_audioSendCount % 50 === 1) {
            console.log("[Mic] Sent audio chunk #" + _audioSendCount + " (" + pcm16.buffer.byteLength + " bytes)");
        }
        ws.send(pcm16.buffer);
    };

    micSource.connect(scriptProcessor);
    scriptProcessor.connect(audioCtx.destination);
}

function closeMic() {
    if (scriptProcessor) {
        scriptProcessor.disconnect();
        scriptProcessor = null;
    }
    if (micSource) {
        micSource.disconnect();
        micSource = null;
    }
    if (micStream) {
        micStream.getTracks().forEach(function (t) { t.stop(); });
        micStream = null;
    }
}

// ── Audio Playback (binary PCM16 16kHz from server) ──────────────────
var playbackStartTimer = null;
var PLAYBACK_BUFFER_MS = 1100; // buffer audio for 1100ms before starting playback

function handleAudioData(arrayBuffer) {
    playbackQueue.push(arrayBuffer);
    if (!isPlaying) {
        isPlaying = true;
        nextPlayTime = 0;
        // Buffer a few chunks before starting playback to avoid cutting first words
        if (!playbackStartTimer) {
            playbackStartTimer = setTimeout(function () {
                playbackStartTimer = null;
                drainPlayback();
            }, PLAYBACK_BUFFER_MS);
        }
    }
}

function drainPlayback() {
    if (!isPlaying) return;

    while (playbackQueue.length > 0) {
        var chunk = playbackQueue.shift();
        try {
            if (!audioCtx) {
                audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            }
            var i16 = new Int16Array(chunk);
            var f32 = new Float32Array(i16.length);
            for (var i = 0; i < i16.length; i++) {
                f32[i] = i16[i] / (i16[i] < 0 ? 0x8000 : 0x7fff);
            }

            var buffer = audioCtx.createBuffer(1, f32.length, PLAYBACK_SAMPLE_RATE);
            buffer.getChannelData(0).set(f32);

            var source = audioCtx.createBufferSource();
            source.buffer = buffer;
            source.connect(audioCtx.destination);

            var now = audioCtx.currentTime;
            var startTime = Math.max(now, nextPlayTime);
            source.start(startTime);
            nextPlayTime = startTime + buffer.duration;
        } catch (err) {
            console.error("[Playback]", err);
        }
    }

    // Check if playback finished and mic is still open → go back to LISTENING
    if (playbackQueue.length === 0 && audioCtx && audioCtx.currentTime >= nextPlayTime - 0.05) {
        isPlaying = false;
        if (currentState === State.SPEAKING && micStream) {
            setState(State.LISTENING, "Listening...");
        }
        return;
    }

    if (isPlaying) {
        requestAnimationFrame(drainPlayback);
    }
}

function stopPlayback() {
    if (playbackStartTimer) {
        clearTimeout(playbackStartTimer);
        playbackStartTimer = null;
    }
    playbackQueue = [];
    isPlaying = false;
    nextPlayTime = 0;
}

// ── Button Handler ───────────────────────────────────────────────────
talkBtn.addEventListener("click", async function () {
    try {
        switch (currentState) {
            case State.IDLE:
                await startConversation();
                break;

            case State.LISTENING:
            case State.SPEAKING:
            case State.TOOL_USE:
                endConversation();
                break;

            case State.CONNECTING:
                // Do nothing while connecting
                break;
        }
    } catch (err) {
        console.error("[Button]", err);
        setState(State.IDLE, "Error: " + err.message);
    }
});

// ── GPS Geolocation ──────────────────────────────────────────────────
let lastGpsCoords = null;

function initGPS() {
    if (!navigator.geolocation) {
        console.warn("[GPS] Geolocation not supported");
        return;
    }

    // Get initial position
    navigator.geolocation.getCurrentPosition(
        function (pos) {
            lastGpsCoords = {
                lat: pos.coords.latitude,
                lon: pos.coords.longitude,
                accuracy: pos.coords.accuracy,
            };
            console.log("[GPS] Initial position:", lastGpsCoords);
            sendGPS();
        },
        function (err) {
            console.warn("[GPS] Permission denied or unavailable:", err.message);
        },
        { enableHighAccuracy: true, timeout: 10000 }
    );

    // Watch for position changes
    navigator.geolocation.watchPosition(
        function (pos) {
            lastGpsCoords = {
                lat: pos.coords.latitude,
                lon: pos.coords.longitude,
                accuracy: pos.coords.accuracy,
            };
            sendGPS();
        },
        function (err) {
            console.warn("[GPS] Watch error:", err.message);
        },
        { enableHighAccuracy: true, maximumAge: 30000 }
    );
}

function sendGPS() {
    if (!ws || ws.readyState !== WebSocket.OPEN || !lastGpsCoords) return;
    ws.send(JSON.stringify({
        type: "gps",
        lat: lastGpsCoords.lat,
        lon: lastGpsCoords.lon,
        accuracy: lastGpsCoords.accuracy,
    }));
    console.log("[GPS] Sent to server:", lastGpsCoords.lat, lastGpsCoords.lon);
}

// ── Service Worker + Push ────────────────────────────────────────────
async function initServiceWorker() {
    if (!("serviceWorker" in navigator)) {
        console.warn("[SW] Not supported");
        return;
    }

    try {
        var reg = await navigator.serviceWorker.register("/static/sw.js");
        console.log("[SW] Registered:", reg.scope);

        // Request notification permission
        if ("Notification" in window && Notification.permission === "default") {
            var permission = await Notification.requestPermission();
            console.log("[Push] Permission:", permission);
        }

        // Subscribe to push if permission granted
        if ("Notification" in window && Notification.permission === "granted" && "PushManager" in window) {
            try {
                // Get VAPID public key from server
                var configResp = await fetch("/api/push/vapid-key");
                if (configResp.ok) {
                    var vapidData = await configResp.json();
                    var vapidKey = vapidData.public_key;

                    if (vapidKey) {
                        // Unsubscribe old subscription (VAPID key may have changed after restart)
                        var existingSub = await reg.pushManager.getSubscription();
                        if (existingSub) {
                            try { await existingSub.unsubscribe(); console.log("[Push] Old sub removed"); } catch(e) {}
                        }

                        var sub = await reg.pushManager.subscribe({
                            userVisibleOnly: true,
                            applicationServerKey: urlBase64ToUint8Array(vapidKey),
                        });

                        // Send subscription to server
                        await fetch("/api/push/subscribe", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(sub.toJSON()),
                        });
                        console.log("[Push] Subscribed successfully");
                    }
                }
            } catch (pushErr) {
                console.warn("[Push] Subscription failed:", pushErr.message);
            }
        }
    } catch (err) {
        console.warn("[SW] Registration failed:", err.message);
    }
}

function urlBase64ToUint8Array(base64String) {
    var padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    var rawData = atob(base64);
    var outputArray = new Uint8Array(rawData.length);
    for (var i = 0; i < rawData.length; i++) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

// ── In-App Notification Banner ───────────────────────────────────────
var notifBanner = document.getElementById("notification-banner");
var notifTitle = document.getElementById("notification-title");
var notifBody = document.getElementById("notification-body");
var notifActions = document.getElementById("notification-actions");
var notifDismiss = document.getElementById("notification-dismiss");
var currentNotificationId = null;

function showNotificationBanner(data) {
    var title = data.title || "Sonic2Life";
    var body = data.body || "";
    var actions = data.actions || [];
    currentNotificationId = data.notification_id || null;

    notifTitle.textContent = title;
    notifBody.textContent = body;

    // Build action buttons
    notifActions.innerHTML = "";
    if (actions.length > 0) {
        actions.forEach(function (act) {
            var btn = document.createElement("button");
            // Pick CSS class based on action name
            var cssClass = "action-default";
            if (act.action === "confirm") cssClass = "action-confirm";
            else if (act.action === "skip" || act.action === "dismiss") cssClass = "action-skip";
            btn.className = "notif-action-btn " + cssClass;
            btn.textContent = act.title;
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                respondToNotification(act.action);
            });
            notifActions.appendChild(btn);
        });
    }

    // Show banner with animation
    notifBanner.classList.remove("hidden");
    notifBanner.offsetHeight; // force reflow
    notifBanner.classList.add("visible");

    console.log("[Notif] Banner shown:", title, body, "actions:", actions.length);
}

function hideNotificationBanner() {
    notifBanner.classList.remove("visible");
    setTimeout(function () {
        notifBanner.classList.add("hidden");
        notifActions.innerHTML = "";
        currentNotificationId = null;
    }, 350);
    console.log("[Notif] Banner dismissed");
}

function respondToNotification(action) {
    if (!currentNotificationId) {
        hideNotificationBanner();
        return;
    }
    console.log("[Notif] Responding:", currentNotificationId, action);

    fetch("/api/push/respond", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            notification_id: currentNotificationId,
            action: action,
            source: "banner",
        }),
    }).then(function (resp) {
        console.log("[Notif] Response sent, status:", resp.status);
    }).catch(function (err) {
        console.warn("[Notif] Response failed:", err);
    });

    hideNotificationBanner();
}

// Dismiss × button
notifDismiss.addEventListener("click", function (e) {
    e.stopPropagation();
    respondToNotification("dismiss");
});

// ── SSE (Server-Sent Events) for in-app notifications ───────────────
var sseSource = null;

function initSSE() {
    if (sseSource) return;

    sseSource = new EventSource("/api/push/events");

    sseSource.onopen = function () {
        console.log("[SSE] Connected");
    };

    sseSource.onmessage = function (event) {
        try {
            var data = JSON.parse(event.data);
            if (data.type === "connected") {
                console.log("[SSE] Server confirmed connection");
                return;
            }
            // This is a notification
            console.log("[SSE] Notification received:", data);
            showNotificationBanner(data);
        } catch (e) {
            console.warn("[SSE] Parse error:", e);
        }
    };

    sseSource.onerror = function () {
        console.warn("[SSE] Connection lost, will auto-reconnect");
        // EventSource auto-reconnects by default
    };
}

// Also listen for SW postMessage as fallback (when app was in background)
if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", function (event) {
        if (event.data && event.data.type === "push-notification") {
            console.log("[Notif] Received from SW:", event.data);
            showNotificationBanner(event.data);
        }
    });
}

// ── PWA Install Prompt ───────────────────────────────────────────────
var deferredInstallPrompt = null;
var installBtn = document.getElementById("install-btn");

window.addEventListener("beforeinstallprompt", function (e) {
    // Prevent Chrome from auto-showing the mini-infobar
    e.preventDefault();
    deferredInstallPrompt = e;
    // Show our custom install button
    installBtn.classList.remove("hidden");
    console.log("[PWA] Install prompt captured – showing install button");
});

installBtn.addEventListener("click", async function () {
    if (!deferredInstallPrompt) return;
    // Show the native install dialog
    deferredInstallPrompt.prompt();
    var result = await deferredInstallPrompt.userChoice;
    console.log("[PWA] Install result:", result.outcome);
    deferredInstallPrompt = null;
    installBtn.classList.add("hidden");
});

// Hide install button if already installed (standalone mode)
if (window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone) {
    installBtn.classList.add("hidden");
    console.log("[PWA] Already running as installed app");
}

window.addEventListener("appinstalled", function () {
    installBtn.classList.add("hidden");
    deferredInstallPrompt = null;
    console.log("[PWA] App installed successfully! 🎉");
});

// ── Camera ────────────────────────────────────────────────────────────
var cameraBtn = document.getElementById("camera-btn");
var cameraInput = document.getElementById("camera-input");
var cameraLabel = document.getElementById("camera-label");

if (cameraBtn && cameraInput) {
    cameraBtn.addEventListener("click", function () {
        cameraInput.click();
    });

    cameraInput.addEventListener("change", function (e) {
        var file = e.target.files[0];
        if (!file) return;

        console.log("[Camera] Photo selected:", file.name, file.size, "bytes");
        cameraLabel.textContent = "Processing...";

        var reader = new FileReader();
        reader.onload = function (ev) {
            var img = new Image();
            img.onload = function () {
                // Resize to max 1024px
                var MAX = 1024;
                var w = img.width;
                var h = img.height;
                if (w > MAX || h > MAX) {
                    if (w > h) {
                        h = Math.round(h * MAX / w);
                        w = MAX;
                    } else {
                        w = Math.round(w * MAX / h);
                        h = MAX;
                    }
                }

                var canvas = document.createElement("canvas");
                canvas.width = w;
                canvas.height = h;
                var ctx = canvas.getContext("2d");
                ctx.drawImage(img, 0, 0, w, h);

                // Convert to JPEG base64 (quality 0.85)
                var base64 = canvas.toDataURL("image/jpeg", 0.85);
                var sizeKB = Math.round(base64.length * 3 / 4 / 1024);
                console.log("[Camera] Resized:", w, "x", h, "~" + sizeKB + "KB");

                // Send via WebSocket
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: "photo",
                        data: base64
                    }));
                    cameraLabel.textContent = "Sending...";
                } else {
                    cameraLabel.textContent = "Not connected";
                    setTimeout(function () { cameraLabel.textContent = "Photo"; }, 2000);
                }
            };
            img.src = ev.target.result;
        };
        reader.readAsDataURL(file);

        // Reset input so same file can be selected again
        cameraInput.value = "";
    });
}

// ── Init ─────────────────────────────────────────────────────────────
setState(State.IDLE);
initGPS();
initServiceWorker();
initSSE();
console.log("Sonic2Life ready");
