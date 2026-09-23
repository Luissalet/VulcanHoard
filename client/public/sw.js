// Minimal service worker: makes the app installable on a phone home screen.
// It caches nothing (the library changes constantly); every request goes to the network.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
