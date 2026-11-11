/* ── DPM-101 Helpdesk Frontend ─────────────────────────────────────────── */
"use strict";

// ── State ──────────────────────────────────────────────────────────────────
const STATE = {
  sessionId: generateId(),
  pending: false,       // true while a request is in-flight
  statsInterval: null,
};

// ── DOM refs ───────────────────────────────────────────────────────────────
const chatMessages   = document.getElementById("chat-messages");
const userInput      = document.getElementById("user-input");
const sendBtn        = document.getElementById("send-btn");
const newChatBtn     = document.getElementById("new-chat-btn");
const charCount      = document.getElementById("char-count");
const statTotal      = document.getElementById("stat-total");
const statGemini     = document.getElementById("stat-gemini");
const statCache      = document.getElementById("stat-cache");
const apiStatusDot   = document.getElementById("api-status");

// ── Utilities ──────────────────────────────────────────────────────────────
function generateId() {
  // Cryptographically random identifier (never used for auth, but randomness
  // must not be guessable so conversations cannot be enumerated).
  if (window.crypto && crypto.getRandomValues) {
    const bytes = new Uint8Array(12);
    crypto.getRandomValues(bytes);
    return "s-" + Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
  }
  // Fallback for very old browsers
  return "s-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 9);
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatAnswer(text) {
  // Convert newlines to <br>
  return escapeHtml(text).replace(/\n/g, "<br>");
}

function scrollToBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ── Render helpers ─────────────────────────────────────────────────────────
function appendUserMessage(text) {
  const row = document.createElement("div");
  row.className = "message-row user";
  row.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
  chatMessages.appendChild(row);
  scrollToBottom();
}

function appendBotMessage(answer, sources, cached) {
  const row = document.createElement("div");
  row.className = "message-row bot";

  let sourcesHtml = "";
  if (sources && sources.length) {
    const tags = sources.map(s => `<span class="source-tag">📄 ${escapeHtml(s)}</span>`).join("");
    sourcesHtml = `<div class="sources-row">${tags}</div>`;
  }

  const cacheTag = cached
    ? `<span class="cache-tag">⚡ Served from cache</span>`
    : "";

  const footerHtml = (sourcesHtml || cacheTag)
    ? `<div class="sources-row">${cacheTag}${sources && sources.length
        ? sources.map(s => `<span class="source-tag">📄 ${escapeHtml(s)}</span>`).join("")
        : ""}</div>`
    : "";

  row.innerHTML = `
    <div class="bubble">${formatAnswer(answer)}</div>
    ${footerHtml}
  `;
  chatMessages.appendChild(row);
  scrollToBottom();
}

function appendError(message) {
  const div = document.createElement("div");
  div.className = "error-bubble";
  div.textContent = message;
  chatMessages.appendChild(div);
  scrollToBottom();
}

function showLoading() {
  const div = document.createElement("div");
  div.className = "loading-row";
  div.id = "loading-indicator";
  div.innerHTML = `
    <span style="font-size:12px;color:#64748b;">Thinking</span>
    <div class="dots"><span></span><span></span><span></span></div>
  `;
  chatMessages.appendChild(div);
  scrollToBottom();
  return div;
}

function removeLoading() {
  const el = document.getElementById("loading-indicator");
  if (el) el.remove();
}

function clearWelcome() {
  const welcome = chatMessages.querySelector(".welcome-message");
  if (welcome) welcome.remove();
}

// ── Send message ───────────────────────────────────────────────────────────
async function sendMessage() {
  if (STATE.pending) return;

  const text = userInput.value.trim();
  if (!text) return;

  clearWelcome();
  appendUserMessage(text);
  userInput.value = "";
  charCount.textContent = "0 / 2000";
  charCount.className = "char-count";
  autoResizeInput();

  setLoading(true);
  const loadingEl = showLoading();

  const requestId = "r-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 9);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: STATE.sessionId,
        request_id: requestId,
        message: text,
      }),
    });

    removeLoading();

    if (!res.ok) {
      let detail = "An error occurred. Please try again.";
      try {
        const err = await res.json();
        detail = err.detail || detail;
      } catch (_) {}
      appendError(detail);
      setApiStatus("error");
      return;
    }

    const data = await res.json();
    appendBotMessage(data.answer, data.sources, data.cached);
    setApiStatus("ok");
    fetchStats();

  } catch (err) {
    removeLoading();
    appendError("Could not reach the server. Please check your connection and try again.");
    setApiStatus("error");
  } finally {
    setLoading(false);
  }
}

// ── UI state helpers ───────────────────────────────────────────────────────
function setLoading(loading) {
  STATE.pending = loading;
  sendBtn.disabled = loading;
  userInput.disabled = loading;
}

function setApiStatus(status) {
  apiStatusDot.className = "status-dot status-" + status;
  apiStatusDot.title = status === "ok"
    ? "API: Online"
    : status === "error"
      ? "API: Error"
      : "API: Unknown";
}

// ── Auto-resize textarea ───────────────────────────────────────────────────
function autoResizeInput() {
  userInput.style.height = "auto";
  userInput.style.height = Math.min(userInput.scrollHeight, 120) + "px";
}

// ── Stats polling ──────────────────────────────────────────────────────────
async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    if (!res.ok) return;
    const data = await res.json();
    statTotal.textContent  = data.total_questions.toLocaleString();
    statGemini.textContent = data.gemini_api_calls.toLocaleString();
    statCache.textContent  = data.cache_hits.toLocaleString();
  } catch (_) {}
}

// ── New chat ───────────────────────────────────────────────────────────────
async function newChat() {
  if (STATE.pending) return;

  try {
    await fetch("/api/session/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: STATE.sessionId }),
    });
  } catch (_) {}

  STATE.sessionId = generateId();
  chatMessages.innerHTML = `
    <div class="welcome-message">
      <div class="welcome-icon">📚</div>
      <h2>Welcome to DPM-101 Helpdesk</h2>
      <p>Ask me anything about your course — exam rules, grading policy, deadlines, certificate tracks, and more.</p>
      <p class="welcome-note">All answers are sourced directly from official course documents.</p>
    </div>
  `;
}

// ── Health check on load ───────────────────────────────────────────────────
async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    if (res.ok) {
      setApiStatus("ok");
    } else {
      setApiStatus("error");
    }
  } catch (_) {
    setApiStatus("error");
  }
}

// ── Event listeners ────────────────────────────────────────────────────────
sendBtn.addEventListener("click", sendMessage);

userInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

userInput.addEventListener("input", () => {
  autoResizeInput();
  const len = userInput.value.length;
  charCount.textContent = `${len} / 2000`;
  charCount.className = "char-count" + (len > 1800 ? " warn" : "") + (len >= 2000 ? " over" : "");
});

newChatBtn.addEventListener("click", newChat);

// ── Init ───────────────────────────────────────────────────────────────────
checkHealth();
fetchStats();
STATE.statsInterval = setInterval(fetchStats, 15000);  // poll every 15 s

