import { useEffect, useState } from "react";
import { type FlagRecord, fetchFlag } from "./api";
import { Editor } from "./Editor";
import { beginLogin, getToken, handleCallback, logout } from "./oauth";

type State =
  | { phase: "booting" }
  | { phase: "signed-out"; error?: string }
  | { phase: "loading"; token: string }
  | { phase: "ready"; token: string; flag: FlagRecord }
  | { phase: "error"; message: string };

export function App() {
  const [state, setState] = useState<State>({ phase: "booting" });

  useEffect(() => {
    void (async () => {
      try {
        await handleCallback();
      } catch (error) {
        setState({ phase: "signed-out", error: String(error) });
        return;
      }
      const token = getToken();
      setState(token ? { phase: "loading", token } : { phase: "signed-out" });
    })();
  }, []);

  useEffect(() => {
    if (state.phase !== "loading") return;
    fetchFlag(state.token)
      .then((flag) => setState({ phase: "ready", token: state.token, flag }))
      .catch((error) => setState({ phase: "error", message: String(error) }));
  }, [state]);

  const handleLogout = () => {
    logout();
    setState({ phase: "signed-out" });
  };

  switch (state.phase) {
    case "booting":
    case "loading":
      return <p className="muted center">Loading…</p>;
    case "signed-out":
      return (
        <div className="center login">
          <h1>PostHog Desktop announcements</h1>
          <p className="muted">
            Edits the <code>posthog-desktop-announcements</code> flag payload.
          </p>
          {state.error && <p className="error">{state.error}</p>}
          <button
            type="button"
            className="primary"
            onClick={() => void beginLogin()}
          >
            Log in with PostHog
          </button>
        </div>
      );
    case "error":
      return (
        <div className="center login">
          <p className="error">{state.message}</p>
          <button type="button" onClick={handleLogout}>
            Start over
          </button>
        </div>
      );
    case "ready":
      return (
        <Editor
          token={state.token}
          flag={state.flag}
          onFlagUpdated={(flag) => setState({ ...state, flag })}
          onLogout={handleLogout}
        />
      );
  }
}
