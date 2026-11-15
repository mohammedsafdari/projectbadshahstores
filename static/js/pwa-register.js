/*
 * Registers the service worker that makes the shop installable (D91).
 *
 * The address is written on this script's own tag by the page (data-sw-url), so
 * the JavaScript never has to know a Django URL.
 */
(function () {
  "use strict";

  // document.currentScript is only set while this file is being executed, so it
  // is read here and kept for the load callback below.
  var scriptTag = document.currentScript;

  // An older browser has no service worker at all; there is nothing to do then.
  if (!("serviceWorker" in navigator)) {
    return;
  }

  // Registering after the page has loaded keeps the first paint fast.
  window.addEventListener("load", function () {
    var serviceWorkerUrl = (scriptTag && scriptTag.getAttribute("data-sw-url")) || "/sw.js";
    // A failure is ignored on purpose: the shop works without a service worker.
    navigator.serviceWorker.register(serviceWorkerUrl, { scope: "/" }).catch(function () {});
  });
})();
