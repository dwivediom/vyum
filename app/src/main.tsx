import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

/** A rendering bug shows a message with a way back instead of a blank window. */
class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: React.ErrorInfo) { console.error(error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="splash">
        <span className="mark lg">V</span>
        <p>Something went wrong on this screen. Your chats and documents are safe.</p>
        <p className="error-text">{this.state.error.message}</p>
        <button className="btn primary" onClick={() => location.reload()}>Reload Vyum</button>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
