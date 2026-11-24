// Priority: runtime config.js (Docker API_URL) > build-time VITE_API_URL (Vercel/Netlify) > localhost
const runtime = typeof window !== "undefined" ? window.APP_CONFIG?.API_URL : "";
export const API_URL = (runtime || import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");
