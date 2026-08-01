import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { fetchDefaultSettings, isAbortError } from './services/api';
import { hasPersonalSettings, setPageDefaultSettings } from './constants/settings';
import { FeedbackProvider } from './components/feedback/FeedbackProvider';
import './App.css';

async function bootstrap() {
  // Personal settings are the strongest preference. Otherwise fetch the
  // server baseline before rendering so all of App's initial states agree.
  if (!hasPersonalSettings()) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 2500);
    try {
      const shared = await fetchDefaultSettings(controller.signal);
      if (shared) setPageDefaultSettings(shared);
    } catch (error) {
      // Settings must never prevent the application from opening. The built-in
      // defaults remain available when the API is offline or slow.
      if (!isAbortError(error)) console.warn('could not load page defaults:', error);
    } finally {
      window.clearTimeout(timeout);
    }
  }

  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <FeedbackProvider>
        <App />
      </FeedbackProvider>
    </React.StrictMode>
  );
}

void bootstrap();
