import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './page';
import { installEnglishValidation } from '@/lib/forms';
import './globals.css';
installEnglishValidation();
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
