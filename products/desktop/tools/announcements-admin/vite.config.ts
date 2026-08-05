import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Absolute base: the SPA fallback serves index.html on sub-routes like
// /oauth/callback, where relative asset paths would resolve to /oauth/assets.
export default defineConfig({
  base: "/",
  plugins: [react()],
});
