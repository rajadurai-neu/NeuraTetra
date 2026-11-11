import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local dev: the API runs on :8010 (see repo README). In Azure, Static Web Apps routes /api to App Service.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8010" } },
});
