/*
 * The two staff behaviours that used to sit in inline attributes (D93).
 *
 * The Content-Security-Policy has no 'unsafe-inline' in script-src, so an
 * onclick or an onsubmit attribute would simply not run. One delegated
 * listener for each keeps the behaviour without any inline script.
 */
"use strict";

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-print-page]");
  if (button) {
    window.print();
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-submit-once]");
  if (!form) {
    return;
  }
  const button = form.querySelector("button[type=submit]");
  if (button) {
    button.disabled = true;
  }
});
