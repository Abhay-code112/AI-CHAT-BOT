import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { API_URL } from "./config";

const newSessionId = () =>
  "s-" + (globalThis.crypto?.randomUUID?.().slice(0, 8) ?? Math.random().toString(36).slice(2, 10));

function formatDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        const field = Array.isArray(e.loc) ? e.loc.filter((p) => p !== "body").join(".") : "";
        return field ? `${field}: ${e.msg}` : e.msg;
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

function friendlyError(status, detail) {
  if (status === 503) return "All AI providers are currently unavailable. Please try again in a moment.";
  if (status === 422) return `Your message could not be sent (${formatDetail(detail) || "invalid input"}).`;
  return formatDetail(detail) || `Request failed (HTTP ${status}).`;
}

let nextId = 1;

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId); // new session on each page load
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    const userId = nextId++;
    setMessages((m) => [...m, { id: userId, role: "user", content: text }]);
    setInput("");
    setLoading(true);

    const headers = { "Content-Type": "application/json" };
    if (simulate.trim()) headers["X-Simulate-Failure"] = simulate.trim();
    const body = { session_id: sessionId, message: text };
    if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();

    try {
      const res = await fetch(`${API_URL}/chat`, { method: "POST", headers, body: JSON.stringify(body) });
      let data = null;
      try {
        data = await res.json();
      } catch {
        /* non-JSON body */
      }
      if (!res.ok) {
        setMessages((m) => [
          ...m.map((x) => (x.id === userId ? { ...x, failed: true } : x)),
          { id: nextId++, role: "error", content: friendlyError(res.status, data?.detail) },
        ]);
      } else {
        setMessages((m) => [
          ...m,
          { id: nextId++, role: "assistant", content: data.reply, metadata: data.metadata },
        ]);
      }
    } catch {
      setMessages((m) => [
        ...m.map((x) => (x.id === userId ? { ...x, failed: true } : x)),
        { id: nextId++, role: "error", content: "Could not reach the server. Check your connection and try again." },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function newChat() {
    setSessionId(newSessionId());
    setMessages([]);
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>AI Chat</h1>
          <span className="session">session {sessionId}</span>
        </div>
        <div className="actions">
          <button className="ghost" onClick={() => setShowSettings((s) => !s)}>
            {showSettings ? "Hide settings" : "Settings"}
          </button>
          <button className="ghost" onClick={newChat}>New chat</button>
        </div>
      </header>

      {showSettings && (
        <section className="settings">
          <label>
            System prompt (applies to this session)
            <textarea
              data-testid="system-prompt-input"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="e.g. You are a concise assistant that answers in one sentence."
              rows={2}
            />
          </label>
          <label>
            Simulate failure (demo only, sent as X-Simulate-Failure)
            <input
              value={simulate}
              onChange={(e) => setSimulate(e.target.value)}
              placeholder="key_1=429   or   *=429"
            />
          </label>
        </section>
      )}

      <main className="chat">
        {messages.length === 0 && <p className="empty">Send a message to start the conversation.</p>}
        {messages.map((m) => {
          if (m.role === "user") {
            return (
              <div key={m.id} className="row user">
                <div className="bubble user-bubble">
                  {m.content}
                  {m.failed && <div className="not-sent">not delivered</div>}
                </div>
              </div>
            );
          }
          if (m.role === "error") {
            return (
              <div key={m.id} className="row">
                <div className="bubble error" role="alert" data-testid="error-message">{m.content}</div>
              </div>
            );
          }
          const md = m.metadata;
          return (
            <div key={m.id} className="row">
              <div className="bubble assistant" data-testid="assistant-message">
                <div className="text"><ReactMarkdown>{m.content}</ReactMarkdown></div>
                <div className="meta">
                  <span
                    className={`chip ${md.resolution_source === "LLM_CACHE" ? "cache" : "live"}`}
                    data-testid="meta-source"
                  >
                    {md.resolution_source}
                  </span>
                  <span className="kv"><em>model</em> <b data-testid="meta-model">{md.model}</b></span>
                  <span className="kv"><em>latency</em> <b data-testid="meta-latency">{md.latency_ms} ms</b></span>
                  <span className="kv"><em>in</em> <b data-testid="meta-input-tokens">{md.input_tokens}</b></span>
                  <span className="kv"><em>out</em> <b data-testid="meta-output-tokens">{md.output_tokens}</b></span>
                  <span className="kv dim"><em>key</em> {md.key_id ?? "—"}</span>
                  <span className="kv dim"><em>fallbacks</em> {md.fallback_attempts}</span>
                </div>
              </div>
            </div>
          );
        })}
        {loading && (
          <div className="row">
            <div className="bubble assistant typing" aria-live="polite">Thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      <footer className="composer">
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a message — Enter to send, Shift+Enter for a new line"
          rows={1}
          autoFocus
        />
        <button data-testid="send-button" onClick={send} disabled={loading}>
          Send
        </button>
        {input.length > 3500 && <span className="count">{input.length}/4000</span>}
      </footer>
    </div>
  );
}