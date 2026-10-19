/* Native views keep their own DOM/router; all their requests use this module. */
(() => {
  const key = document.currentScript.dataset.studioModule;
  const prefix = '/' + key;
  const scoped = (value) => {
    const url = new URL(value, location.href);
    if (url.origin === location.origin && url.pathname !== prefix && !url.pathname.startsWith(prefix + '/') && !url.pathname.startsWith('/fusion-static/')) url.pathname = prefix + url.pathname;
    return url.href;
  };
  const fetchNative = window.fetch.bind(window);
  window.fetch = (input, init) => fetchNative(input instanceof Request ? new Request(scoped(input.url), input) : scoped(input), init);
  const openNative = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) { return openNative.call(this, method, scoped(url), ...rest); };
  const NativeEvents = window.EventSource;
  if (NativeEvents) window.EventSource = class extends NativeEvents { constructor(url, options) { super(scoped(url), options); } };
  const imageSource = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
  Object.defineProperty(HTMLImageElement.prototype, 'src', {...imageSource, set(value) { imageSource.set.call(this, scoped(value)); }});
  const register = navigator.serviceWorker?.register.bind(navigator.serviceWorker);
  if (register) navigator.serviceWorker.register = (url, options={}) => register(scoped(url), {...options, scope: prefix + '/'});
  document.addEventListener('click', event => {
    const anchor = event.target.closest?.('a[href]');
    if (!anchor || anchor.closest('.fusion-bar') || anchor.getAttribute('href').startsWith('#')) return;
    const before = anchor.href; const after = scoped(before); if (before !== after) anchor.href = after;
  }, true);
})();
