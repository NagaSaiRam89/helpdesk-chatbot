/* ═══════════════════════════════════════════════════════════════════════════
   DPM-101 Course Helpdesk — frontend logic
   Vanilla JS. No dependencies. Talks to POST /api/chat, /api/stats,
   /api/health and POST /api/session/reset.
   ═══════════════════════════════════════════════════════════════════════════ */
"use strict";

// ── State ──────────────────────────────────────────────────────────────────
const STATE = {
  sessionId: generateId(),
  pending: false,          // true while a request is in-flight (double-submit guard)
  statsInterval: null,
  lastStats: { total: null, gemini: null, cache: null },
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
const apiStatus      = document.getElementById("api-status");
const apiStatusText  = document.getElementById("api-status-text");

// ── Quick-start suggestions (drawn from the official sample questions) ─────
const SUGGESTIONS = [
  { icon: "🎯", text: "What score do I need to pass?",
    question: "What is the minimum score needed to pass the course and get a Verified Certificate?" },
  { icon: "📅", text: "When is the Capstone due?",
    question: "When is the Capstone Product Case due?" },
  { icon: "📝", text: "Is the final exam open-book?",
    question: "Is the final exam open-book or closed-book, and when is it scheduled?" },
  { icon: "💳", text: "How much does the certificate cost?",
    question: "How much does the Verified Certificate track cost?" },
];

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
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Escape first, then apply light formatting: blank-line paragraphs,
 * "-"/"•"/"*" bullet lists, and single newlines as line breaks.
 */
function formatAnswer(text) {
  const safe = escapeHtml(text).trim();
  if (!safe) return "";

  return safe.split(/\n{2,}/).map(block => {
    const lines = block.split("\n");
    const isList = lines.length > 1 && lines.every(l => /^\s*[-•*]\s+/.test(l));
    if (isList) {
      const items = lines.map(l => `<li>${l.replace(/^\s*[-•*]\s+/, "")}</li>`).join("");
      return `<ul>${items}</ul>`;
    }
    return `<p>${lines.join("<br>")}</p>`;
  }).join("");
}

function scrollToBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ── Welcome screen ─────────────────────────────────────────────────────────
function renderWelcome() {
  const chips = SUGGESTIONS.map(s => `
    <button class="suggestion-chip" type="button" data-question="${escapeHtml(s.question)}">
      ${escapeHtml(s.text)}
    </button>`).join("");

  chatMessages.innerHTML = `
    <div class="welcome-message">
      <div class="welcome-icon" aria-hidden="true">🎓</div>
      <h2>How can I help you today?</h2>
      <p>Ask anything about DPM-101 — exam rules, grading, deadlines,
         certificate tracks and more.</p>
      <div class="suggestions" aria-label="Suggested questions">${chips}</div>
      <p class="welcome-note">🔒 Answers are sourced only from the official DPM-101 documents.</p>
    </div>`;
}

function clearWelcome() {
  const welcome = chatMessages.querySelector(".welcome-message");
  if (welcome) welcome.remove();
}

// ── Render helpers ─────────────────────────────────────────────────────────
function appendUserMessage(text) {
  const row = document.createElement("div");
  row.className = "message-row user";
  row.innerHTML = `
    <div class="avatar user" aria-hidden="true">🧑‍🎓</div>
    <div class="message-content">
      <div class="bubble">${escapeHtml(text)}</div>
    </div>`;
  chatMessages.appendChild(row);
  scrollToBottom();
}

function appendBotMessage(answer, sources, cached) {
  const row = document.createElement("div");
  row.className = "message-row bot";

  const meta = [];

  if (cached) {
    meta.push(`<span class="cache-tag">⚡ Served from cache</span>`);
  }

  (sources || []).forEach(s => {
    meta.push(`<span class="source-tag" title="${escapeHtml(s)}">📄 ${escapeHtml(s)}</span>`);
  });

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "copy-btn";
  copyBtn.textContent = "Copy";
  copyBtn.setAttribute("aria-label", "Copy answer");
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(answer);
      copyBtn.textContent = "Copied ✓";
    } catch (_) {
      copyBtn.textContent = "Copy failed";
    }
    setTimeout(() => { copyBtn.textContent = "Copy"; }, 1600);
  });

  const content = document.createElement("div");
  content.className = "message-content";
  content.innerHTML = `
    <div class="bubble">${formatAnswer(answer)}</div>
    <div class="meta-row">${meta.join("")}</div>`;
  content.querySelector(".meta-row").appendChild(copyBtn);

  row.innerHTML = `<div class="avatar bot" aria-hidden="true">🎓</div>`;
  row.appendChild(content);
  chatMessages.appendChild(row);
  scrollToBottom();
}

function appendError(message) {
  const div = document.createElement("div");
  div.className = "error-bubble";
  div.innerHTML = `<span aria-hidden="true">⚠️</span><span>${escapeHtml(message)}</span>`;
  chatMessages.appendChild(div);
  scrollToBottom();
}

function showLoading() {
  const div = document.createElement("div");
  div.className = "loading-row";
  div.id = "loading-indicator";
  div.innerHTML = `
    <div class="avatar bot" aria-hidden="true">🎓</div>
    <div class="loading-bubble">
      <span>Searching course documents</span>
      <div class="dots" aria-hidden="true"><span></span><span></span><span></span></div>
    </div>`;
  chatMessages.appendChild(div);
  scrollToBottom();
  return div;
}

function removeLoading() {
  const el = document.getElementById("loading-indicator");
  if (el) el.remove();
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
  showLoading();

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
    userInput.focus();
  }
}

// ── UI state helpers ───────────────────────────────────────────────────────
function setLoading(loading) {
  STATE.pending = loading;
  sendBtn.disabled = loading;
  userInput.disabled = loading;
  chatMessages.setAttribute("aria-busy", loading ? "true" : "false");
}

function setApiStatus(status) {
  apiStatus.className = "status-pill status-" + status;
  const labels = { ok: "Online", error: "Offline", unknown: "Connecting…" };
  apiStatusText.textContent = labels[status] || labels.unknown;
  apiStatus.title = "API: " + (labels[status] || labels.unknown);
}

// ── Auto-resize textarea ───────────────────────────────────────────────────
function autoResizeInput() {
  userInput.style.height = "auto";
  userInput.style.height = Math.min(userInput.scrollHeight, 132) + "px";
}

// ── Stats polling ──────────────────────────────────────────────────────────
function bumpIfChanged(el, newText) {
  if (el.textContent !== newText) {
    el.textContent = newText;
    el.classList.remove("bump");
    void el.offsetWidth;           // restart the animation
    el.classList.add("bump");
  }
}

async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    if (!res.ok) return;
    const data = await res.json();
    bumpIfChanged(statTotal,  Number(data.total_questions).toLocaleString());
    bumpIfChanged(statGemini, Number(data.gemini_api_calls).toLocaleString());
    bumpIfChanged(statCache,  Number(data.cache_hits).toLocaleString());
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
  renderWelcome();
  userInput.value = "";
  charCount.textContent = "0 / 2000";
  charCount.className = "char-count";
  autoResizeInput();
  userInput.focus();
}

// ── Health check ───────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const res = await fetch("/api/health");
    setApiStatus(res.ok ? "ok" : "error");
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

// Suggestion chips (event delegation — survives re-rendering the welcome screen)
chatMessages.addEventListener("click", (e) => {
  const chip = e.target.closest(".suggestion-chip");
  if (!chip || STATE.pending) return;
  userInput.value = chip.dataset.question || chip.textContent.trim();
  autoResizeInput();
  sendMessage();
});

// ── Init ───────────────────────────────────────────────────────────────────
renderWelcome();
checkHealth();
fetchStats();
STATE.statsInterval = setInterval(fetchStats, 15000);  // poll every 15 s
userInput.focus();
