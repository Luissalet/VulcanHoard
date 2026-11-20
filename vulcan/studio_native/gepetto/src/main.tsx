import React from 'react';
import ReactDOM from 'react-dom/client';
import '@fontsource-variable/manrope';
import App from './App';
import './style.css';
import { installHoardCopilot } from './hoard-copilot';
import { language, tr } from './i18n';

document.documentElement.lang = language;
const title = tr('Figuras y relieves', 'Figures and reliefs');
const studioBar = document.querySelector('.fusion-bar');
const studioName = studioBar?.getAttribute('aria-label');
document.title = studioName ? `${studioName} · ${title}` : title;
const moduleName = studioBar?.querySelector('span');
const functionsLink = studioBar?.querySelector('.fusion-functions');
if (moduleName) moduleName.textContent = title;
if (functionsLink) functionsLink.textContent = tr('Funciones', 'Functions');

installHoardCopilot();
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
